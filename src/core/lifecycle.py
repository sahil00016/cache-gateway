"""Application startup and shutdown.

Ordering is deliberate. Logging is configured first so that every later step is
observable; dependencies are then created; teardown runs in reverse.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.settings import get_settings
from src.core.logging import configure_logging
from src.db.engine import dispose_engine, get_engine, init_engine
from src.db.redis import dispose_redis, init_redis
from src.service.bloom import init_bloom_filter

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

    # Initialize Bloom filter and build from database
    bloom = init_bloom_filter(
        expected_items=settings.bloom_expected_items,
        fp_rate=settings.bloom_fp_rate,
        enabled=settings.bloom_enabled,
    )

    if settings.bloom_enabled:
        # Build the filter from Postgres at startup
        engine = get_engine()
        async with AsyncSession(engine) as session:
            stats = await bloom.build_from_db(session)
            logger.info(
                "Bloom filter built from database",
                extra=stats,
            )

    try:
        yield
    finally:
        bloom.close()
        await dispose_redis()
        await dispose_engine()
        logger.info("stopped")
