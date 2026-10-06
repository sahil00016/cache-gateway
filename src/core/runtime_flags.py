"""Mutable overlay over the four failure-mode demo flags.

Why this exists
---------------
``Settings`` is loaded once per process and cached with ``lru_cache``, which is
correct: configuration should not shift under a running request. But the four
protection flags are not ordinary configuration. They exist so a failure mode
can be *demonstrated*, and a demonstration that needs a container restart
between the "before" and the "after" is not a demonstration -- on this service a
restart costs 15-20 seconds, most of it rebuilding the Bloom filter from a
million rows.

So those four, and only those four, get a mutable overlay. Everything else
still comes from ``Settings`` and still cannot change at runtime.

Per-worker, by construction
---------------------------
These flags live in process memory, so with four gunicorn workers there are
four copies. A PATCH reaches whichever worker answers it; the other three keep
their old value until they are asked too. That is the same per-process
limitation the Bloom filter and the coalescer already have (ADR-0003, ADR-0005),
and it is visible rather than hidden: the admin endpoint reports how many
workers it believes exist, and the dashboard repeats a toggle until the reported
state is uniform.

Sharing these across workers would mean Redis, which would put a network call in
the read path of every request in order to make a demo button feel tidier. That
trade is not worth making.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, fields

from src.common.settings import Settings


@dataclass
class RuntimeFlags:
    """The subset of settings a demo is allowed to change at runtime."""

    bloom_enabled: bool
    coalesce_enabled: bool
    cache_invalidate_on_write: bool
    cache_ttl_jitter_pct: int


#: Bounds mirror the Field(ge=0, le=100) constraint on Settings.cache_ttl_jitter_pct.
#: The overlay must not accept a value the configuration itself would reject.
_JITTER_PCT_MIN = 0
_JITTER_PCT_MAX = 100

_lock = threading.Lock()
_flags: RuntimeFlags | None = None


def init_runtime_flags(settings: Settings) -> RuntimeFlags:
    """Seed the overlay from configuration at process start.

    Args:
        settings: The process settings to take initial values from.

    Returns:
        The initialised flags.
    """
    global _flags  # noqa: PLW0603 -- one per process, deliberately
    with _lock:
        _flags = RuntimeFlags(
            bloom_enabled=settings.bloom_enabled,
            coalesce_enabled=settings.coalesce_enabled,
            cache_invalidate_on_write=settings.cache_invalidate_on_write,
            cache_ttl_jitter_pct=settings.cache_ttl_jitter_pct,
        )
        return _flags


def get_runtime_flags() -> RuntimeFlags:
    """Return the live flags, seeding them from settings if not yet initialised.

    The fallback matters for tests and for any code path that builds an app
    without running the lifespan hook: returning None here would turn a missing
    initialisation into an AttributeError deep inside a request.

    Returns:
        The live flags for this process.
    """
    if _flags is None:
        from src.common.settings import get_settings  # noqa: PLC0415 -- avoids an import cycle

        return init_runtime_flags(get_settings())
    return _flags


def update_runtime_flags(**changes: bool | int) -> RuntimeFlags:
    """Apply a partial update to the live flags.

    Args:
        **changes: Field names and new values. Unknown names are rejected
            rather than silently ignored, for the same reason ADR-0002 exists:
            a typo that quietly does nothing produces a demo that appears to
            toggle and does not.

    Returns:
        The flags after the update.

    Raises:
        ValueError: If a field name is not one of the four mutable flags, or a
            jitter percentage falls outside 0-100.
    """
    valid = {f.name for f in fields(RuntimeFlags)}
    unknown = set(changes) - valid
    if unknown:
        raise ValueError(f"Not a runtime-mutable flag: {', '.join(sorted(unknown))}")

    pct = changes.get("cache_ttl_jitter_pct")
    if pct is not None and not (_JITTER_PCT_MIN <= int(pct) <= _JITTER_PCT_MAX):
        raise ValueError("cache_ttl_jitter_pct must be between 0 and 100.")

    flags = get_runtime_flags()
    with _lock:
        for key, value in changes.items():
            setattr(flags, key, value)
        return flags


def reset_runtime_flags() -> None:
    """Drop the overlay so the next read re-seeds from settings. Tests only."""
    global _flags  # noqa: PLW0603 -- see init_runtime_flags
    with _lock:
        _flags = None
