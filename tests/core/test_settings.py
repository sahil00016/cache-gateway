"""Tests for settings validation and derived values."""

import pytest
from pydantic import ValidationError

from src.common.enums import Environment
from src.common.settings import Settings, get_settings


def test_settings_are_cached_per_process() -> None:
    assert get_settings() is get_settings()


def test_docs_are_exposed_outside_production(settings: Settings) -> None:
    assert settings.docs_url == "/docs"


@pytest.mark.parametrize("environment", [Environment.UAT, Environment.PROD])
def test_docs_are_hidden_in_production_like_environments(
    monkeypatch: pytest.MonkeyPatch, environment: Environment
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment.value)
    get_settings.cache_clear()

    assert get_settings().docs_url is None


def test_jitter_seconds_is_derived_from_the_percentage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CACHE_TTL_SECONDS", "300")
    monkeypatch.setenv("CACHE_TTL_JITTER_PCT", "10")
    get_settings.cache_clear()

    assert get_settings().cache_ttl_jitter_seconds == 30.0


def test_invalid_fp_rate_is_rejected_at_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    # Failing here rather than on the first request is the point: a bad cache
    # config fails silently under load, which is far worse.
    monkeypatch.setenv("BLOOM_FP_RATE", "1.5")
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        get_settings()


def test_a_typo_in_an_env_var_name_is_silently_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Document a real hazard, so nobody assumes protection that is not there.

    ``extra="forbid"`` rejects unexpected keys passed to ``__init__``; it does
    NOT reject unknown environment variables, because pydantic-settings only
    looks up names matching declared fields. Everything else in the environment
    is ignored by design -- otherwise PATH and HOME would be validation errors.

    The consequence for this service is sharp: every failure-mode protection is
    an env toggle, so ``CACHE_TTL_SECOND`` (missing the S) silently keeps the
    default and you demo a fix that was never switched on.

    See docs/adr/0002 for the decision on whether to close this.
    """
    monkeypatch.setenv("CACHE_TTL_SECOND", "60")  # note the typo
    get_settings.cache_clear()

    settings = get_settings()

    # No error was raised, and the default is in force.
    assert settings.cache_ttl_seconds == 300


def test_settings_are_frozen(settings: Settings) -> None:
    with pytest.raises(ValidationError):
        settings.cache_ttl_seconds = 999  # type: ignore[misc]
