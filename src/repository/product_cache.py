"""Redis-backed cache for product reads.

Cache-aside (M3): the service checks here first, and on a miss falls through
to :class:`~src.repository.product.ProductRepository` and populates this cache
before returning. Cache-aside vs write-through is formalised in ADR-0006 when
M7 adds write invalidation -- there are no writes yet, so that choice has
nothing to justify until then. See the project design doc for the four
failure modes this milestone does not yet address -- thundering herd on a
cold key, stampede on expiry, a negative-cache gap, and TTL avalanche. Each
gets a dedicated milestone and its own ADR.

Redis is treated as strictly best-effort. A cache outage must degrade the
service to M2's uncached behaviour, not take it down -- so every Redis call is
caught, logged, and counted, never propagated. The alternative (letting a
timeout raise) would make the cache a new single point of failure for a
service whose entire purpose is protecting Postgres from load, which is
backwards.
"""

import logging
import random

from pydantic import ValidationError as PydanticValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

from src.common.settings import Settings
from src.core.metrics import cache_operations_total
from src.schema.product import ProductRead

logger = logging.getLogger(__name__)


class ProductCacheRepository:
    """Cache-aside reads for products, backed by Redis."""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        """Bind the cache to a Redis client and the settings that shape it.

        Args:
            redis: The process-wide Redis client.
            settings: Validated application settings.
        """
        self._redis = redis
        self._settings = settings

    def _key(self, product_id: int) -> str:
        return f"{self._settings.cache_key_prefix}:{product_id}"

    def _ttl_with_jitter(self) -> int:
        # Uniform jitter added on top of the base TTL, never subtracted --
        # subtracting would shorten the *minimum* freshness window, while
        # adding only spreads out *when* keys expire. That spread is the whole
        # point: it decorrelates the expiry of keys written around the same
        # time, which is what an avalanche needs to happen. See ADR pending
        # for failure mode 2.
        jitter = random.uniform(0, self._settings.cache_ttl_jitter_seconds)  # noqa: S311
        return round(self._settings.cache_ttl_seconds + jitter)

    async def get(self, product_id: int) -> ProductRead | None:
        """Return a cached product, or None on a miss or cache failure.

        Args:
            product_id: The product's id.

        Returns:
            The cached product, or None if absent, expired, unreadable, or the
            cache could not be reached.
        """
        try:
            raw = await self._redis.get(self._key(product_id))
        except RedisError:
            logger.warning("cache_read_failed", extra={"product_id": product_id})
            cache_operations_total.labels(outcome="error").inc()
            return None

        if raw is None:
            cache_operations_total.labels(outcome="miss").inc()
            return None

        try:
            product = ProductRead.model_validate_json(raw)
        except PydanticValidationError:
            # A stored value that no longer matches ProductRead -- most likely
            # a schema change deployed without a cache_key_prefix bump. Treated
            # as a miss rather than a crash: the request still succeeds, via
            # Postgres, and the stale entry is overwritten on the way out.
            logger.warning("cache_value_unreadable", extra={"product_id": product_id})
            cache_operations_total.labels(outcome="error").inc()
            return None

        cache_operations_total.labels(outcome="hit").inc()
        return product

    async def set(self, product_id: int, product: ProductRead) -> None:
        """Populate the cache after a database read.

        Args:
            product_id: The product's id.
            product: The value read from Postgres.
        """
        try:
            await self._redis.set(
                self._key(product_id),
                product.model_dump_json(),
                ex=self._ttl_with_jitter(),
            )
        except RedisError:
            # The request already has its answer from Postgres; a failed
            # write just means the next request pays the same query.
            logger.warning("cache_write_failed", extra={"product_id": product_id})
