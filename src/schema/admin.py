"""Schema for the effective-configuration endpoint."""

from pydantic import BaseModel, Field


class EffectiveSettings(BaseModel):
    """Configuration as the running process actually loaded it.

    Exists because of ADR-0002: an environment variable name that does not
    match a declared field is silently ignored rather than rejected, so a
    typo produces a plausible-looking benchmark of the wrong configuration.
    This is read back from the live :class:`~src.common.settings.Settings`
    instance, never assumed from what an operator meant to set.

    Deliberately excludes ``database_url`` and ``redis_url`` -- both carry
    credentials, and neither affects how a benchmark result should be read.
    """

    environment: str = Field(description="Deployment environment.")
    web_concurrency: int = Field(
        description="Worker count. Bounds what per-process protections can guarantee."
    )
    write_strategy: str = Field(description="How writes reconcile the cache with Postgres.")

    cache_ttl_seconds: int = Field(description="Base TTL for a cached product.")
    cache_ttl_jitter_pct: int = Field(description="Jitter as a percentage of the base TTL.")
    cache_ttl_jitter_seconds: float = Field(description="Jitter ceiling, in seconds.")
    cache_key_prefix: str = Field(description="Cache key namespace, including its version.")

    bloom_enabled: bool
    bloom_expected_items: int
    bloom_fp_rate: float

    coalesce_enabled: bool = Field(description="In-process request coalescing.")
    redis_lock_enabled: bool = Field(description="Cross-worker stampede lock.")
    redis_lock_ttl_ms: int
