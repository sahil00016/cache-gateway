"""Tests for the request coalescer.

The coalescer is the hand-built singleflight implementation for stampede
protection. These tests verify the three edge cases that break if wrong:

1. Cancelled waiter does not cancel the leader
2. Leader exception propagates to all waiters
3. Dict cleanup happens on all paths (no leaks)
"""

import asyncio

import pytest

from src.service.coalescer import RequestCoalescer


@pytest.fixture
def coalescer() -> RequestCoalescer:
    """Return a fresh coalescer for each test."""
    return RequestCoalescer()


async def test_single_request_executes_once(coalescer: RequestCoalescer) -> None:
    """A single request with no concurrency just executes the loader."""
    call_count = 0

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        return "result"

    result = await coalescer.coalesce("key1", loader)

    assert result == "result"
    assert call_count == 1


async def test_concurrent_requests_coalesce_to_one_execution(coalescer: RequestCoalescer) -> None:
    """Multiple concurrent requests for the same key execute the loader once."""
    call_count = 0

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        # Simulate slow DB query
        await asyncio.sleep(0.05)
        return "result"

    # Fire 10 concurrent requests for the same key
    results = await asyncio.gather(*[coalescer.coalesce("key1", loader) for _ in range(10)])

    # All get the same result, but loader ran only once
    assert all(r == "result" for r in results)
    assert call_count == 1


async def test_different_keys_do_not_coalesce(coalescer: RequestCoalescer) -> None:
    """Concurrent requests for different keys execute independently."""
    call_count = 0

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        # Capture the count at the time of this execution
        this_call = call_count
        await asyncio.sleep(0.05)
        return f"result-{this_call}"

    # Fire requests for different keys
    results = await asyncio.gather(
        coalescer.coalesce("key1", loader),
        coalescer.coalesce("key2", loader),
        coalescer.coalesce("key3", loader),
    )

    # Each key got a different result, loader ran 3 times
    assert len(set(results)) == 3
    assert call_count == 3


async def test_cancelled_waiter_does_not_cancel_leader(coalescer: RequestCoalescer) -> None:
    """Edge case 1: If one waiter is cancelled, the leader must still complete.

    Without asyncio.shield, cancelling one waiter would cancel the leader's
    await, breaking every other waiter.
    """
    call_count = 0
    leader_completed = asyncio.Event()

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.1)
        leader_completed.set()
        return "result"

    # Start the leader (this will block in loader for 0.1s)
    leader_task = asyncio.create_task(coalescer.coalesce("key1", loader))
    await asyncio.sleep(0.01)  # Let it become the leader

    # Start a waiter, then cancel it
    waiter_task = asyncio.create_task(coalescer.coalesce("key1", loader))
    await asyncio.sleep(0.01)  # Let it register as a waiter
    waiter_task.cancel()

    # Wait for cancellation to propagate
    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    # The leader must still complete successfully
    result = await leader_task
    assert result == "result"
    assert call_count == 1
    assert leader_completed.is_set()


async def test_leader_exception_propagates_to_all_waiters(coalescer: RequestCoalescer) -> None:
    """Edge case 2: If the leader raises, every waiter must see the same exception.

    Without set_exception, waiters would hang forever when the leader fails.
    """

    async def failing_loader() -> str:
        await asyncio.sleep(0.05)
        raise ValueError("DB is down")

    # Fire 5 concurrent requests for the same key
    tasks = [asyncio.create_task(coalescer.coalesce("key1", failing_loader)) for _ in range(5)]

    # All tasks must fail with the same exception
    for task in tasks:
        with pytest.raises(ValueError, match="DB is down"):
            await task


async def test_dict_cleanup_happens_on_success(coalescer: RequestCoalescer) -> None:
    """Edge case 3a: The future is removed from the dict after success.

    Without cleanup, the key is poisoned — every subsequent request waits on
    a stale future.
    """

    async def loader() -> str:
        return "result"

    # First request succeeds and cleans up
    await coalescer.coalesce("key1", loader)
    assert "key1" not in coalescer._inflight

    # Second request for the same key should execute fresh, not wait on a stale future
    result = await coalescer.coalesce("key1", loader)
    assert result == "result"


async def test_dict_cleanup_happens_on_exception(coalescer: RequestCoalescer) -> None:
    """Edge case 3b: The future is removed from the dict even when the leader raises.

    Without cleanup on the exception path, the key is poisoned forever.
    """
    call_count = 0

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("first attempt fails")
        return "success on retry"

    # First request fails
    with pytest.raises(ValueError, match="first attempt fails"):
        await coalescer.coalesce("key1", loader)

    # Dict must be cleaned up
    assert "key1" not in coalescer._inflight

    # Second request should succeed (not wait on the stale failed future)
    result = await coalescer.coalesce("key1", loader)
    assert result == "success on retry"
    assert call_count == 2


async def test_dict_cleanup_happens_on_cancellation(coalescer: RequestCoalescer) -> None:
    """Edge case 3c: The future is removed even when the leader is cancelled.

    Without cleanup on the cancellation path, the key leaks.
    """

    async def loader() -> str:
        await asyncio.sleep(10)  # Long enough to cancel before completion
        return "result"

    # Start a request and cancel it while it's executing
    task = asyncio.create_task(coalescer.coalesce("key1", loader))
    await asyncio.sleep(0.01)  # Let it register
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Dict must be cleaned up
    assert "key1" not in coalescer._inflight


async def test_sequential_requests_each_execute(coalescer: RequestCoalescer) -> None:
    """Sequential requests (not concurrent) should not coalesce.

    Each completes before the next starts, so each is the leader.
    """
    call_count = 0

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        return f"result-{call_count}"

    # Execute three requests sequentially
    result1 = await coalescer.coalesce("key1", loader)
    result2 = await coalescer.coalesce("key1", loader)
    result3 = await coalescer.coalesce("key1", loader)

    # Each executed independently
    assert result1 == "result-1"
    assert result2 == "result-2"
    assert result3 == "result-3"
    assert call_count == 3
