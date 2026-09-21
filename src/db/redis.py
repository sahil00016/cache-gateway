"""Redis client lifecycle.

One connection pool per process, shared by every request in that worker.
"""

import logging

from redis.asyncio import ConnectionPool, Redis

from src.common.settings import Settings
from src.core.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_client: Redis | None = None


def init_redis(settings: Settings) -> Redis:
    """Create the process-wide Redis pool and client.

    Args:
        settings: Validated application settings.

    Returns:
        The created client.
    """
    global _pool, _client  # noqa: PLW0603 -- process-wide singleton

    _pool = ConnectionPool.from_url(
        str(settings.redis_url),
        max_connections=settings.redis_max_connections,
        decode_responses=True,
    )
    _client = Redis(connection_pool=_pool)
    logger.info("redis_initialised", extra={"max_connections": settings.redis_max_connections})
    return _client


async def dispose_redis() -> None:
    """Close the Redis client and its pool."""
    global _pool, _client  # noqa: PLW0603 -- process-wide singleton

    if _client is not None:
        await _client.aclose()
        logger.info("redis_disposed")
    _client = None
    _pool = None


def get_redis() -> Redis:
    """Return the initialised Redis client.

    Returns:
        The process-wide client.

    Raises:
        ConfigurationError: If called before :func:`init_redis`.
    """
    if _client is None:
        raise ConfigurationError("Redis client used before initialisation.")
    return _client
