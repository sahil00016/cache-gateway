"""Admin endpoints: effective configuration and operational controls.

See ADR-0002 and :class:`~src.schema.admin.EffectiveSettings` for why the stats
endpoint exists: an env var name that does not match a declared setting is
silently ignored, so a benchmark's configuration must be verified at measurement
time, not assumed from what an operator believed they set.

The bloom/rebuild endpoint is the mitigation for the Bloom filter deletion
problem (see ADR-0003): when too many deleted products have accumulated as
false positives, rebuild the filter from the current truth in Postgres.
"""

from datetime import UTC, datetime

from fastapi import APIRouter

from src.api.deps import SessionDep, SettingsDep
from src.core.envelope import SuccessResponse
from src.core.metrics import MULTIPROCESS_ENABLED, build_scrape_registry
from src.schema.admin import (
    BloomRebuildResult,
    BloomStats,
    EffectiveSettings,
    MetricSample,
    MetricsSnapshot,
)
from src.service.bloom import get_bloom_filter

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.get(
    "/stats",
    response_model=SuccessResponse[EffectiveSettings],
    summary="Effective configuration of the running process",
)
async def stats(settings: SettingsDep) -> SuccessResponse[EffectiveSettings]:
    """Return the configuration this process actually loaded.

    Args:
        settings: Injected application settings.

    Returns:
        The effective settings, wrapped in the standard success envelope.
    """
    return SuccessResponse[EffectiveSettings](
        data=EffectiveSettings(
            environment=settings.environment.value,
            web_concurrency=settings.web_concurrency,
            write_strategy=settings.write_strategy.value,
            cache_invalidate_on_write=settings.cache_invalidate_on_write,
            cache_ttl_seconds=settings.cache_ttl_seconds,
            cache_ttl_jitter_pct=settings.cache_ttl_jitter_pct,
            cache_ttl_jitter_seconds=settings.cache_ttl_jitter_seconds,
            cache_key_prefix=settings.cache_key_prefix,
            bloom_enabled=settings.bloom_enabled,
            bloom_expected_items=settings.bloom_expected_items,
            bloom_fp_rate=settings.bloom_fp_rate,
            coalesce_enabled=settings.coalesce_enabled,
            redis_lock_enabled=settings.redis_lock_enabled,
            redis_lock_ttl_ms=settings.redis_lock_ttl_ms,
        )
    )


@router.get(
    "/bloom/stats",
    response_model=SuccessResponse[BloomStats],
    summary="Current Bloom filter statistics",
)
async def bloom_stats() -> SuccessResponse[BloomStats]:
    """Return current Bloom filter statistics.

    Includes capacity, saturation, target and measured false positive rates,
    and lookup counts. Used by the dashboard to display filter health.

    Returns:
        Bloom filter stats, wrapped in the standard success envelope.
    """
    bloom = get_bloom_filter()
    stats = bloom.get_stats()
    return SuccessResponse[BloomStats](data=BloomStats(**stats))


@router.post(
    "/bloom/rebuild",
    response_model=SuccessResponse[BloomRebuildResult],
    summary="Rebuild the Bloom filter from Postgres",
)
async def rebuild_bloom(session: SessionDep) -> SuccessResponse[BloomRebuildResult]:
    """Rebuild the Bloom filter from all live products in Postgres.

    This is the recovery mechanism for the deletion problem: a Bloom filter
    cannot remove keys, so deleted products remain as false positives until the
    filter is rebuilt. This endpoint measures and returns the rebuild cost.

    **Warning:** This operation blocks while reading every product ID from
    Postgres. On a 1M-row table the rebuild takes ~1-2 seconds. During rebuild
    the filter continues serving lookups from its old state, so traffic is not
    affected, but the operation consumes a database connection for its duration.

    Args:
        session: Injected database session.

    Returns:
        Rebuild statistics (count, duration), wrapped in the standard success
        envelope.
    """
    bloom = get_bloom_filter()
    result = await bloom.build_from_db(session)
    return SuccessResponse[BloomRebuildResult](data=BloomRebuildResult(**result))


@router.get(
    "/metrics",
    response_model=SuccessResponse[MetricsSnapshot],
    summary="Current metric values as JSON",
)
async def metrics_json() -> SuccessResponse[MetricsSnapshot]:
    """Return every metric sample as JSON, for browser clients.

    ``/metrics`` already exposes these numbers, but in Prometheus exposition
    format, which a dashboard can only consume by shipping a text parser to the
    browser and keeping it correct as the format evolves. This endpoint reads
    the same registry -- so it inherits the cross-worker aggregation of
    ADR-0007 and cannot drift from the Prometheus output.

    Returns:
        A snapshot of every sample in the scrape registry.
    """
    samples = [
        MetricSample(name=sample.name, labels=dict(sample.labels), value=sample.value)
        for metric in build_scrape_registry().collect()
        for sample in metric.samples
    ]
    return SuccessResponse[MetricsSnapshot](
        data=MetricsSnapshot(
            scraped_at=datetime.now(UTC).isoformat(),
            multiprocess=MULTIPROCESS_ENABLED,
            samples=samples,
        )
    )
