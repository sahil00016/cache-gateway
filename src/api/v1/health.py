"""Liveness, readiness and metrics endpoints.

The distinction matters operationally and is worth stating explicitly:

``/healthz`` answers "is this process alive?". It touches nothing. A failing
liveness probe means the container should be restarted.

``/readyz`` answers "can this process serve traffic?". It probes every
dependency. A failing readiness probe means take this instance out of the load
balancer, but do not restart it -- the fault is probably downstream.

Conflating the two causes restart storms: Postgres hiccups, every instance
fails liveness, every instance restarts, and the thundering herd of reconnects
makes the original problem worse.
"""

import time

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from src.common.settings import Settings, get_settings
from src.core.metrics import build_scrape_registry
from src.db.engine import get_engine
from src.db.redis import get_redis
from src.schema.health import DependencyStatus, LivenessResult, ReadinessResult

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=LivenessResult, summary="Liveness probe")
async def healthz() -> LivenessResult:
    """Report that the process is alive.

    Deliberately touches no dependency.

    Returns:
        A static liveness result.
    """
    settings: Settings = get_settings()
    return LivenessResult(status="ok", service=settings.app_name)


async def _probe_postgres() -> DependencyStatus:
    """Probe Postgres with a trivial query.

    Returns:
        The dependency's status.
    """
    started = time.perf_counter()
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- a probe must never raise
        return DependencyStatus(name="postgres", healthy=False, error=str(exc))
    return DependencyStatus(
        name="postgres",
        healthy=True,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


async def _probe_redis() -> DependencyStatus:
    """Probe Redis with PING.

    Returns:
        The dependency's status.
    """
    started = time.perf_counter()
    try:
        await get_redis().ping()
    except Exception as exc:  # noqa: BLE001 -- a probe must never raise
        return DependencyStatus(name="redis", healthy=False, error=str(exc))
    return DependencyStatus(
        name="redis",
        healthy=True,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )


@router.get("/readyz", response_model=ReadinessResult, summary="Readiness probe")
async def readyz(response: Response) -> ReadinessResult:
    """Report whether every dependency is reachable.

    Args:
        response: Injected so the status code can be set to 503 when not ready.

    Returns:
        The readiness result, listing every dependency.
    """
    dependencies = [await _probe_postgres(), await _probe_redis()]
    ready = all(dep.healthy for dep in dependencies)
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResult(ready=ready, dependencies=dependencies)


@router.get("/metrics", include_in_schema=False, summary="Prometheus metrics")
async def metrics() -> Response:
    """Expose metrics in Prometheus text format.

    Excluded from the OpenAPI schema: it is an operational endpoint, not part
    of the service's contract with clients.

    Returns:
        The rendered metrics payload, aggregated across every live worker when
        running multi-process.
    """
    return Response(
        content=generate_latest(build_scrape_registry()),
        media_type=CONTENT_TYPE_LATEST,
    )
