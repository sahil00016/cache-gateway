"""Tests for the runtime flag overlay.

The overlay is what makes the M9 dashboard's live toggles possible, so the
behaviour worth pinning is not "a setter sets" but the two things that would
silently break a demo: a rejected field doing nothing, and state leaking
between requests.
"""

import pytest

from src.common.settings import Settings
from src.core.runtime_flags import (
    get_runtime_flags,
    init_runtime_flags,
    reset_runtime_flags,
    update_runtime_flags,
)


def test_flags_seed_from_settings(settings: Settings) -> None:
    flags = init_runtime_flags(settings)

    assert flags.bloom_enabled == settings.bloom_enabled
    assert flags.coalesce_enabled == settings.coalesce_enabled
    assert flags.cache_invalidate_on_write == settings.cache_invalidate_on_write
    assert flags.cache_ttl_jitter_pct == settings.cache_ttl_jitter_pct


def test_reading_before_init_seeds_rather_than_raising() -> None:
    # Any code path that builds an app without running the lifespan hook -- the
    # unit test client does exactly this -- must still get usable flags, not an
    # AttributeError from deep inside a request.
    reset_runtime_flags()

    assert get_runtime_flags() is not None


def test_update_changes_only_the_named_field(settings: Settings) -> None:
    init_runtime_flags(settings.model_copy(update={"bloom_enabled": True}))

    flags = update_runtime_flags(bloom_enabled=False)

    assert flags.bloom_enabled is False
    assert flags.coalesce_enabled == settings.coalesce_enabled


def test_update_rejects_an_unknown_field(settings: Settings) -> None:
    # ADR-0002's lesson applied to the overlay: a name that does not match a
    # flag must fail loudly. Silently ignoring it produces a demo where the
    # toggle appears to work and the behaviour never changes.
    init_runtime_flags(settings)

    with pytest.raises(ValueError, match="Not a runtime-mutable flag"):
        update_runtime_flags(bloom_enabledd=False)


def test_update_rejects_out_of_range_jitter(settings: Settings) -> None:
    init_runtime_flags(settings)

    with pytest.raises(ValueError, match="between 0 and 100"):
        update_runtime_flags(cache_ttl_jitter_pct=101)


def test_update_does_not_apply_any_change_when_one_is_invalid(settings: Settings) -> None:
    init_runtime_flags(settings.model_copy(update={"bloom_enabled": True}))

    with pytest.raises(ValueError, match="Not a runtime-mutable flag"):
        update_runtime_flags(bloom_enabled=False, nonsense=1)

    # Validation happens before any assignment, so a partially applied update
    # cannot leave the service in a state the caller never asked for.
    assert get_runtime_flags().bloom_enabled is True
