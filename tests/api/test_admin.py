"""Tests for the effective-configuration endpoint.

See ADR-0002: this endpoint exists so a benchmark can verify what the running
process actually loaded, rather than trusting what an operator believed they
set via the environment.
"""

import httpx


async def test_stats_reports_the_effective_cache_ttl(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/admin/stats")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["cache_ttl_seconds"] > 0


async def test_stats_never_exposes_the_datastore_urls(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/admin/stats")

    keys = response.json()["data"].keys()
    assert "database_url" not in keys
    assert "redis_url" not in keys
