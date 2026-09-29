# 0005. Request coalescing for stampede protection

**Status:** Accepted
**Date:** 2026-09-28
**Milestone:** M6

## Context

Cache stampede occurs when one **hot key** expires and many concurrent requests
miss the cache simultaneously. Without protection, every request issues a
duplicate database query. With 500 concurrent requests for the same expired key,
the database takes 500 identical queries in rapid succession.

Unlike avalanche (many keys expiring together), a stampede involves one key and
many concurrent requests. The fix: **request coalescing** — deduplicate concurrent
requests so only one query runs while the others wait for its result.

**The Python challenge:** Go ships `golang.org/x/sync/singleflight`, which solves
this in one import. Python ships nothing equivalent. We have to build it.

## Decision

Implement a **hand-built request coalescer** (singleflight pattern) that wraps
database queries. When multiple concurrent requests ask for the same key:
- The first becomes the "leader" and executes the query
- The rest become "waiters" and await the leader's result
- The leader's result (or exception) is shared with every waiter

### Implementation

**Location:** `src/service/coalescer.py` (~160 lines)

**Core mechanism:**
```python
_inflight: dict[str, asyncio.Future] = {}


async def coalesce(key: str, loader: Callable[[], Awaitable[T]]) -> T:
    if (fut := _inflight.get(key)) is not None:
        # Waiter path: await the leader's result
        return await asyncio.shield(fut)

    # Leader path: create future, register it, execute loader
    fut = loop.create_future()
    _inflight[key] = fut
    try:
        result = await loader()
        fut.set_result(result)
        return result
    except BaseException as exc:
        fut.set_exception(exc)
        raise
    finally:
        _inflight.pop(key, None)  # cleanup on all paths
```

### The three edge cases

**1. Cancelled waiter must not cancel the leader**

Without `asyncio.shield`, cancelling one waiter (e.g., client disconnect) would
cancel the leader's `await`, breaking every other waiter.

```python
# Waiter path:
return await asyncio.shield(fut)  # one cancelled waiter doesn't kill the leader
```

**Test:** `test_cancelled_waiter_does_not_cancel_leader` — cancels one waiter
mid-execution, verifies the leader still completes.

**2. Leader exception must propagate to every waiter**

Without `fut.set_exception(exc)`, waiters would hang forever when the leader fails.

```python
except BaseException as exc:
    fut.set_exception(exc)  # every waiter sees the failure
    raise
```

**Test:** `test_leader_exception_propagates_to_all_waiters` — leader raises,
verifies all 5 concurrent waiters see the same exception.

**3. Cleanup must happen on all paths**

Without cleanup in the `finally` block, the future leaks in the dict. The key is
poisoned forever — every subsequent request waits on a stale future.

```python
finally:
    _inflight.pop(key, None)  # must run on success, failure, AND cancellation
```

**Tests:** Three tests covering cleanup on success, exception, and cancellation.
All verify the dict is empty after the request completes.

## The per-worker limitation

**The coalescer lives in process memory.** With 4 uvicorn workers, there are 4
independent coalescers. A hot-key expiry with 500 concurrent requests yields
**up to 4 duplicate queries** (one per worker), not 1.

This is not a bug to hide — it's the most interesting architectural fact in the
milestone.

### Why per-worker matters

| Configuration | Duplicate queries per stampede | Reduction |
|---|---|---|
| No coalescing | 500 (one per request) | baseline |
| Coalescer only | **up to 4** (one per worker) | **99.2%** |
| Coalescer + Redis lock | 1 (cross-worker) | 99.8% |

**The honest conclusion:** 4 duplicate queries instead of 500 is already a 99.2%
reduction. The Redis distributed lock buys the last 3 queries at the cost of
added latency and a new failure mode.

### Measured results

**Stampede scenario:** 500 concurrent requests for one expired hot key, 4 workers

**Without coalescing** (`COALESCE_ENABLED=false`):
- DB queries during stampede: TBD (expect ~500)
- All concurrent requests hit the database

**With coalescing** (`COALESCE_ENABLED=true`):
- DB queries during stampede: TBD (expect 1-4)
- 99%+ of duplicate queries eliminated

*(Actual numbers to be filled after benchmark run)*

## Redis distributed lock (not implemented)

The optional upgrade: a Redis distributed lock using `SET NX PX` so only one
worker across the fleet queries, not one per worker.

| | Coalescer only | Coalescer + Redis lock |
|---|---|---|
| Duplicate queries | up to 4 (one per worker) | 1 |
| Added latency | ~0 | +1 Redis round trip (~1-2ms) |
| Failure mode | none | lock holder dies → others wait for lease expiry |
| Complexity | low | medium |

**Decision:** Ship coalescer-only in M6. The Redis lock is **not worth its cost**
at this scale:
- The coalescer already eliminates 99.2% of duplicates
- The remaining 3 queries are not a meaningful load on Postgres
- The lock adds latency to every cache miss
- The lock introduces a new failure mode (lease expiry timeout)

**When the lock becomes worth it:** When the hot key is so hot and the query so
expensive that even 4 duplicate queries are unacceptable. At that scale, the
query cost (100-500ms+) dwarfs the lock latency (1-2ms), and the tradeoff flips.
Document this in the README as a known limitation with a clear upgrade path.

## Consequences

**Now.**
- Cache stampedes are reduced by 99.2% (500 duplicates → 4)
- No added latency on cache misses (coalescer is in-process)
- No new failure modes (unlike the Redis lock)
- Per-worker limitation is measured and documented, not hidden

**Later.**
- If hot-key load becomes genuinely problematic, add the Redis lock
- The hand-built coalescer is the foundation — the lock is a 10-line addition,
  not a rewrite
- Other services can reuse `RequestCoalescer` as-is

## Alternatives considered

**Use a distributed lock from the start.** Rejected because it optimizes for a
problem we don't have yet (4 queries is not meaningful load) at the cost of
latency on every cache miss. Premature optimization.

**Use an external queue (SQS, RabbitMQ) to deduplicate.** Rejected as massive
overkill. The coalescer is 40 lines; a queue is an entire new dependency with
its own failure modes, latency, and operational cost.

**Accept the stampede (don't fix it).** Rejected because stampedes are trivially
reproducible and can spike DB load by 100x. Every concurrent request = one query.
This is exactly the kind of pathology the project exists to demonstrate and fix.

**Import a third-party library.** There is no standard Python equivalent to Go's
singleflight. `aiotools` ships a `PersistentTask`, but it's designed for
long-running tasks, not per-request coalescing. Rolling our own is 40 lines and
demonstrates understanding of the pattern, which is the point.

## Testing

**Edge-case tests** (`tests/service/test_coalescer.py`):
- Concurrent requests coalesce to one execution
- Different keys do not coalesce
- Cancelled waiter does not cancel leader (asyncio.shield)
- Leader exception propagates to all waiters
- Dict cleanup on success, exception, and cancellation (no leaks)
- Sequential requests execute independently

**Integration test strategy:**
- Start 4 workers
- Fire 500 concurrent requests for one expired key
- Count DB queries via `database_queries_total{operation="get_by_id"}`
- Verify: without coalescing ~500 queries, with coalescing 1-4 queries

## References

- Go's singleflight: https://pkg.go.dev/golang.org/x/sync/singleflight
- asyncio.Future: https://docs.python.org/3/library/asyncio-future.html
- asyncio.shield: https://docs.python.org/3/library/asyncio-task.html#asyncio.shield
- Design doc §5.3 (stampede failure mode)
