"""Effective-configuration endpoint.

See ADR-0002 and :class:`~src.schema.admin.EffectiveSettings` for why this
exists: an env var name that does not match a declared setting is silently
ignored, so a benchmark's configuration must be verified at measurement time,
not assumed from what an operator believed they set.
"""

from fastapi import APIRouter

from src.api.deps import SettingsDep
from src.core.envelope import SuccessResponse
from src.schema.admin import EffectiveSettings

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
