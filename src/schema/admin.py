"""Schema for admin endpoints: effective configuration and Bloom filter stats."""

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


class BloomStats(BaseModel):
    """Current Bloom filter statistics.

    Used by the dashboard to display filter health and the measured false
    positive rate vs. the predicted rate.
    """

    enabled: bool = Field(description="Whether penetration protection is active.")
    expected_items: int = Field(description="The n in the sizing formula.")
    items_added: int = Field(description="Count of keys added to the filter.")
    saturation: float = Field(
        description="Fraction of capacity in use (items_added / expected_items)."
    )
    target_fp_rate: float = Field(description="The p in the sizing formula.")
    measured_fp_rate: float = Field(
        description="Empirical false positive rate from observed lookups."
    )
    total_lookups: int = Field(description="Count of filter queries since startup or rebuild.")
    false_positives: int = Field(
        description="Count of lookups that said 'might exist' but Postgres said no."
    )
    m_bits: int = Field(description="Filter size in bits.")
    k_hashes: int = Field(description="Count of hash functions.")


class BloomRebuildResult(BaseModel):
    """Result of rebuilding the Bloom filter from Postgres.

    The rebuild duration is the measured cost of recovering from the deletion
    problem. This number appears in ADR-0003 and the README.
    """

    count: int = Field(description="Count of product IDs loaded into the filter.")
    duration_seconds: float = Field(description="Time spent rebuilding, in seconds.")
