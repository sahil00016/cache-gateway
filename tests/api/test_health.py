"""Tests for the liveness, readiness and metrics endpoints."""

import httpx


async def test_healthz_returns_ok_without_touching_dependencies(
    client: httpx.AsyncClient,
) -> None:
    # The whole point of liveness is that it works with Postgres and Redis
    # down. This test passes with neither running, which is the assertion.
    response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "cache-gateway-test"}


async def test_healthz_echoes_inbound_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"X-Request-ID": "abc123"})

    assert response.headers["X-Request-ID"] == "abc123"


async def test_healthz_generates_request_id_when_absent(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")

    assert len(response.headers["X-Request-ID"]) == 32


async def test_readyz_reports_unready_when_dependencies_are_down(
    client: httpx.AsyncClient,
) -> None:
    # Lifespan never ran, so the engine and Redis client are uninitialised.
    # Readiness must report that as 503 rather than raising.
    response = await client.get("/readyz")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False
    assert {dep["name"] for dep in body["dependencies"]} == {"postgres", "redis"}
    assert all(dep["healthy"] is False for dep in body["dependencies"])


async def test_metrics_endpoint_serves_prometheus_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "cache_gateway_database_queries_total" in response.text


async def test_metrics_is_absent_from_the_openapi_contract(client: httpx.AsyncClient) -> None:
    spec = (await client.get("/openapi.json")).json()

    assert "/metrics" not in spec["paths"]
    assert "/healthz" in spec["paths"]
