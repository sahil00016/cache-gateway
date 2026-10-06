"""Tests for the runtime flag admin endpoints."""

import httpx
import pytest


@pytest.mark.anyio
async def test_get_flags_reports_the_answering_worker(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/admin/flags")

    assert response.status_code == 200
    data = response.json()["data"]
    # The PID and worker count are in the payload on purpose: these flags are
    # per-process, and a caller needs to know how many workers it must convince.
    assert data["worker_pid"] > 0
    assert data["web_concurrency"] >= 1


@pytest.mark.anyio
async def test_patch_changes_a_flag_and_returns_the_new_state(
    client: httpx.AsyncClient,
) -> None:
    before = (await client.get("/v1/admin/flags")).json()["data"]["bloom_enabled"]

    response = await client.patch("/v1/admin/flags", json={"bloom_enabled": not before})

    assert response.status_code == 200
    assert response.json()["data"]["bloom_enabled"] is (not before)


@pytest.mark.anyio
async def test_patch_leaves_omitted_flags_alone(client: httpx.AsyncClient) -> None:
    before = (await client.get("/v1/admin/flags")).json()["data"]

    response = await client.patch("/v1/admin/flags", json={"cache_ttl_jitter_pct": 25})

    data = response.json()["data"]
    assert data["cache_ttl_jitter_pct"] == 25
    assert data["coalesce_enabled"] == before["coalesce_enabled"]
    assert data["bloom_enabled"] == before["bloom_enabled"]


@pytest.mark.anyio
async def test_empty_patch_is_rejected(client: httpx.AsyncClient) -> None:
    # An empty body almost always means the caller built the request wrong.
    # Returning 200 would report success for a no-op.
    response = await client.patch("/v1/admin/flags", json={})

    assert response.status_code == 400


@pytest.mark.anyio
async def test_out_of_range_jitter_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.patch("/v1/admin/flags", json={"cache_ttl_jitter_pct": 101})

    # 422 from pydantic's own bounds, which is the right place for it to fail.
    assert response.status_code == 422
