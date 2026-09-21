"""Application startup and shutdown.

Ordering is deliberate. Logging is configured first so that every later step is
observable; dependencies are then created; teardown runs in reverse.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.common.settings import get_settings
from src.core.logging import configure_logging
from src.db.engine import dispose_engine, init_engine
from src.db.redis import dispose_redis, init_redis

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage the application's startup and shutdown.

    Args:
        app: The FastAPI application. Unused here, but required by the
            lifespan protocol.

    Yields:
        Control back to the server while the application serves traffic.
    """
    del app  # part of the protocol, not needed

    settings = get_settings()
    configure_logging(settings)
    logger.info(
        "starting",
        extra={
            "environment": settings.environment.value,
            "web_concurrency": settings.web_concurrency,
            "bloom_enabled": settings.bloom_enabled,
            "coalesce_enabled": settings.coalesce_enabled,
            "redis_lock_enabled": settings.redis_lock_enabled,
        },
    )

    init_engine(settings)
    init_redis(settings)

    try:
        yield
    finally:
        await dispose_redis()
        await dispose_engine()
        logger.info("stopped")
