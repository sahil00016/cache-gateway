"""Application settings, loaded from the environment and validated at boot.

Every setting is read exactly once, at import of :func:`get_settings`, and the
process refuses to start if any value is invalid. Failing at boot rather than
on the first request is deliberate: a misconfigured cache is worse than a
missing one, because it fails silently under load rather than loudly at start.
"""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.common.enums import Environment, LogLevel, WriteStrategy


class Settings(BaseSettings):
    """Runtime configuration for the Cache Gateway service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    # ----------------------------------------------------------------- app --
    app_name: str = "cache-gateway"
    environment: Environment = Environment.LOCAL
    log_level: LogLevel = LogLevel.INFO
    debug: bool = False

    # ------------------------------------------------------------ datastore --
    database_url: PostgresDsn
    db_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    db_max_overflow: Annotated[int, Field(ge=0, le=100)] = 15
    db_echo: bool = False
    """Log every emitted SQL statement.

    Kept as a first-class setting rather than a debug afterthought: this project
    is about controlling database load, so the queries must stay visible.
    """

    redis_url: RedisDsn
    redis_max_connections: Annotated[int, Field(ge=1, le=500)] = 50

    # ---------------------------------------------------------------- cache --
    cache_ttl_seconds: Annotated[int, Field(ge=1)] = 300
    cache_ttl_jitter_pct: Annotated[int, Field(ge=0, le=100)] = 10
    """Avalanche protection. Zero disables jitter, which is how the
    before/after comparison for failure mode 2 is produced."""

    cache_key_prefix: str = "product:v1"
    """Version segment exists so the entire cache can be invalidated by bumping
    it, without issuing a single DELETE."""

    write_strategy: WriteStrategy = WriteStrategy.CACHE_ASIDE

    # ---------------------------------------------------- failure-mode flags --
    bloom_enabled: bool = True
    bloom_expected_items: Annotated[int, Field(ge=1)] = 1_000_000
    bloom_fp_rate: Annotated[float, Field(gt=0.0, lt=1.0)] = 0.01

    coalesce_enabled: bool = True
    """In-process request coalescing. Per worker, by construction."""

    redis_lock_enabled: bool = False
    """Cross-worker stampede protection. Off by default so the per-worker
    limitation of coalescing alone can be measured first."""

    redis_lock_ttl_ms: Annotated[int, Field(ge=100)] = 5_000

    # ------------------------------------------------------------- runtime --
    web_concurrency: Annotated[int, Field(ge=1, le=64)] = 4
    """uvicorn worker count. Not merely a deployment detail: the Bloom filter
    and the coalescer are per-process, so this number changes what those
    protections can guarantee."""

    # ------------------------------------------------------- observability --
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_service_name: str = "cache-gateway"
    metrics_enabled: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def docs_url(self) -> str | None:
        """Swagger UI path, disabled outside development.

        Returns:
            The docs path, or None when running production-like.
        """
        return None if self.environment.is_production_like else "/docs"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cache_ttl_jitter_seconds(self) -> float:
        """Maximum jitter added to a cache TTL, in seconds.

        Returns:
            The absolute jitter ceiling derived from the configured percentage.
        """
        return self.cache_ttl_seconds * (self.cache_ttl_jitter_pct / 100)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that configuration is parsed and validated exactly once per
    process, and so tests can clear the cache to inject overrides.

    Returns:
        The validated settings instance.
    """
    return Settings()


__all__ = ["Environment", "LogLevel", "Settings", "WriteStrategy", "get_settings"]
