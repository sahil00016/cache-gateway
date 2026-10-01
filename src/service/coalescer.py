"""Request coalescing for stampede protection.

Python has no built-in equivalent to Go's `singleflight`, so this is hand-built.
The pattern: when multiple concurrent requests ask for the same key, one becomes
the leader and executes the expensive operation, while the others wait for its
result. The leader's result (or exception) is shared with every waiter.

**The three edge cases that break if wrong:**

1. **Cancelled waiter must not cancel the leader.** If one waiter is cancelled
   (e.g., client disconnect), the leader must continue for the others. Solution:
   `asyncio.shield` on the waiter path.

2. **Leader exception must propagate to every waiter.** If the leader raises, all
   waiters must see the same exception, not hang forever. Solution: catch the
   exception, `fut.set_exception(exc)`, then re-raise.

3. **Cleanup must happen on all paths.** If the future leaks in the dict, the key
   is poisoned forever — every subsequent request waits on a stale future.
   Solution: `finally` block that pops the key unconditionally.

**Per-worker limitation.** The coalescer lives in process memory. With N uvicorn
workers there are N independent coalescers, so a hot-key expiry yields **up to N
duplicate DB queries** (one per worker), not 1. This is measured directly in M6.
The Redis distributed lock is the cross-worker upgrade, but it adds latency and
a new failure mode — see ADR-0005 for when it is worth its cost.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.core.metrics import coalesced_requests_total

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RequestCoalescer:
    """In-process request coalescing (singleflight pattern).

    This is a per-worker singleton. Each uvicorn worker has its own coalescer,
    so deduplication only works within one worker. A hot-key expiry with 4
    workers yields up to 4 duplicate queries, not 1.
    """

    def __init__(self) -> None:
        """Initialize an empty coalescer."""
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    async def coalesce(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute loader for this key, or await an in-flight execution.

        If no execution is in flight, this request becomes the leader and runs
        the loader. If an execution is already in flight, this request becomes
        a waiter and awaits the leader's result.

        Args:
            key: The deduplication key (e.g., cache key, product ID).
            loader: An async callable that produces the value. Called at most
                once per key, regardless of concurrent request count.

        Returns:
            The result of the loader.

        Raises:
            BaseException: Whatever the loader raises. If the leader raises, every
                waiter sees the same exception.
        """
        # Check if there's already an in-flight request for this key
        fut = self._inflight.get(key)
        if fut is not None:
            # Waiter path: another request is already loading this key
            coalesced_requests_total.inc()
            logger.debug(
                "coalescing request",
                extra={"key": key, "role": "waiter"},
            )
            # Shield: if THIS task is cancelled, the leader must not be
            # cancelled. One disconnected client doesn't kill the load for
            # everyone else.
            return await asyncio.shield(fut)

        # Leader path: this is the first request for this key
        logger.debug(
            "leading request",
            extra={"key": key, "role": "leader"},
        )

        # Create a future and register it. Every subsequent request for this
        # key will await this future until we resolve it.
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._inflight[key] = fut

        try:
            # Execute the loader. This is the actual expensive operation (e.g.,
            # a database query).
            result = await loader()
        except BaseException as exc:
            # The load failed. Set the exception on the future so every waiter
            # sees it, then re-raise so the leader sees it too.
            fut.set_exception(exc)
            raise
        else:
            # Success: resolve the future with the result so every waiter gets
            # the same value.
            fut.set_result(result)
            return result
        finally:
            # Cleanup: remove the future from the dict on EVERY path (success,
            # exception, cancellation). If we don't, the key is poisoned forever
            # — every subsequent request for this key waits on a stale future.
            self._inflight.pop(key, None)


# Global singleton instance, initialized during app startup
_coalescer: RequestCoalescer | None = None


def init_coalescer() -> RequestCoalescer:
    """Initialize the process-wide request coalescer singleton.

    Call this once during app startup.

    Returns:
        The initialized coalescer instance.
    """
    global _coalescer  # noqa: PLW0603
    _coalescer = RequestCoalescer()
    return _coalescer


def get_coalescer() -> RequestCoalescer:
    """Return the process-wide request coalescer singleton.

    Raises:
        RuntimeError: If the coalescer has not been initialized yet.

    Returns:
        The coalescer instance.
    """
    if _coalescer is None:
        raise RuntimeError("Request coalescer not initialized. Call init_coalescer during startup.")
    return _coalescer


__all__ = [
    "RequestCoalescer",
    "get_coalescer",
    "init_coalescer",
]
