"""Tests for the Redis-backed product cache.

A hand-rolled fake stands in for ``redis.asyncio.Redis`` rather than a real
connection or a third-party fake server: the repository only ever calls
``get`` and ``set``, so the fake's surface is intentionally that small. A
second fake that always raises exercises the fail-open contract -- a cache
outage must degrade to M2's uncached behaviour, never break the request.
"""

from datetime import UTC, datetime

import pytest
from redis.exceptions import RedisError

from src.common.settings import Settings
from src.repository.product_cache import ProductCacheRepository
from src.schema.product import ProductRead


def _product(product_id: int = 1) -> ProductRead:
    now = datetime.now(UTC)
    return ProductRead(
        id=product_id,
        sku=f"SKU-{product_id:09d}",
        name="a product",
        description=None,
        price_cents=1999,
        category="books",
        created_at=now,
        updated_at=now,
    )


class FakeRedis:
    """In-memory stand-in for the two calls the cache repository makes."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ex))


class BrokenRedis:
    """Simulates an unreachable Redis: every call raises."""

    async def get(self, key: str) -> str | None:
        raise RedisError("connection refused")

    async def set(self, key: str, value: str, ex: int) -> None:
        raise RedisError("connection refused")


@pytest.fixture
def cache_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "cache_ttl_seconds": 60,
            "cache_ttl_jitter_pct": 0,
            "cache_key_prefix": "product:v1",
        }
    )


async def test_get_is_a_miss_when_the_key_is_absent(cache_settings: Settings) -> None:
    cache = ProductCacheRepository(FakeRedis(), cache_settings)  # type: ignore[arg-type]

    assert await cache.get(1) is None


async def test_set_then_get_round_trips_the_product(cache_settings: Settings) -> None:
    redis = FakeRedis()
    cache = ProductCacheRepository(redis, cache_settings)  # type: ignore[arg-type]
    product = _product(42)

    await cache.set(42, product)
    result = await cache.get(42)

    assert result == product


async def test_set_uses_the_configured_key_prefix(cache_settings: Settings) -> None:
    redis = FakeRedis()
    cache = ProductCacheRepository(redis, cache_settings)  # type: ignore[arg-type]

    await cache.set(7, _product(7))

    assert "product:v1:7" in redis.store


async def test_set_applies_the_configured_ttl_with_zero_jitter(cache_settings: Settings) -> None:
    redis = FakeRedis()
    cache = ProductCacheRepository(redis, cache_settings)  # type: ignore[arg-type]

    await cache.set(1, _product(1))

    key, _value, ttl = redis.set_calls[0]
    assert key == "product:v1:1"
    assert ttl == 60


async def test_set_ttl_never_exceeds_the_configured_jitter_ceiling(settings: Settings) -> None:
    jittered = settings.model_copy(update={"cache_ttl_seconds": 60, "cache_ttl_jitter_pct": 10})
    redis = FakeRedis()
    cache = ProductCacheRepository(redis, jittered)  # type: ignore[arg-type]

    for product_id in range(20):
        await cache.set(product_id, _product(product_id))

    ttls = [ttl for _key, _value, ttl in redis.set_calls]
    assert all(60 <= ttl <= 66 for ttl in ttls)


async def test_a_get_against_an_unreachable_redis_is_treated_as_a_miss(
    cache_settings: Settings,
) -> None:
    cache = ProductCacheRepository(BrokenRedis(), cache_settings)  # type: ignore[arg-type]

    assert await cache.get(1) is None


async def test_a_set_against_an_unreachable_redis_does_not_raise(
    cache_settings: Settings,
) -> None:
    cache = ProductCacheRepository(BrokenRedis(), cache_settings)  # type: ignore[arg-type]

    await cache.set(1, _product(1))


async def test_a_value_that_does_not_match_the_schema_is_treated_as_a_miss(
    cache_settings: Settings,
) -> None:
    # Simulates a stale entry left over from a schema change, without needing
    # to actually change ProductRead to produce one.
    redis = FakeRedis()
    redis.store["product:v1:1"] = '{"unexpected": "shape"}'
    cache = ProductCacheRepository(redis, cache_settings)  # type: ignore[arg-type]

    assert await cache.get(1) is None
