"""Async SQLAlchemy engine and session factory.

The engine is created once at startup and disposed at shutdown. Sessions are
per-request and are never shared across tasks: an ``AsyncSession`` is not
concurrency-safe, and sharing one is the most common way to corrupt state in an
asyncio service.
"""

import logging
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.common.settings import Settings
from src.core.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(settings: Settings) -> AsyncEngine:
    """Create the process-wide engine and session factory.

    Args:
        settings: Validated application settings.

    Returns:
        The created engine.
    """
    global _engine, _session_factory  # noqa: PLW0603 -- process-wide singleton

    _engine = create_async_engine(
        str(settings.database_url),
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        # The pool ceiling is a deliberate backpressure mechanism: under a
        # stampede it is what makes the failure visible as queueing rather
        # than as an unbounded pile of connections.
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info("engine_initialised", extra={"pool_size": settings.db_pool_size})
    return _engine


async def dispose_engine() -> None:
    """Close every pooled connection and drop the engine."""
    global _engine, _session_factory  # noqa: PLW0603 -- process-wide singleton

    if _engine is not None:
        await _engine.dispose()
        logger.info("engine_disposed")
    _engine = None
    _session_factory = None


def get_engine() -> AsyncEngine:
    """Return the initialised engine.

    Returns:
        The process-wide engine.

    Raises:
        ConfigurationError: If called before :func:`init_engine`.
    """
    if _engine is None:
        raise ConfigurationError("Database engine used before initialisation.")
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a request-scoped session, as a FastAPI dependency.

    Yields:
        A session that is closed when the request ends.

    Raises:
        ConfigurationError: If called before :func:`init_engine`.
    """
    if _session_factory is None:
        raise ConfigurationError("Session factory used before initialisation.")
    async with _session_factory() as session:
        yield session
