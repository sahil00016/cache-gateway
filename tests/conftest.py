"""Shared pytest fixtures.

Unit tests here build the app with overridden settings and never touch a real
Postgres or Redis. Integration tests (marked ``integration``) spin up real
containers; those fixtures arrive with the milestone that needs them.
"""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from src.common.settings import Settings, get_settings

_TEST_ENV = {
    "APP_NAME": "cache-gateway-test",
    "ENVIRONMENT": "test",
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "REDIS_URL": "redis://localhost:6379/15",
    "OTEL_ENABLED": "false",
    "DB_ECHO": "false",
}


@pytest.fixture(autouse=True)
def _test_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Pin the environment for every test and clear the settings cache.

    The settings singleton is cached with ``lru_cache``, so it must be cleared
    both before and after each test or one test's configuration leaks into the
    next.
    """
    for key, value in _TEST_ENV.items():
        monkeypatch.setenv(key, value)
    # A developer's .env must not leak into the test run, or the suite passes
    # locally and fails in CI (or worse, the reverse).
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    """Return settings built from the pinned test environment."""
    return get_settings()


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """Return an HTTP client wired to the app, with lifespan NOT run.

    Skipping lifespan keeps unit tests free of real Postgres and Redis. Tests
    that need initialised dependencies are integration tests.
    """
    # Imported lazily and deliberately: src.main builds the app at module
    # level, so a top-level import here would run at collection time -- before
    # the env fixture has patched the environment -- and would read the
    # developer's .env instead of the pinned test values.
    from src.main import create_app  # noqa: PLC0415

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
