# ADR-0006: Cache Invalidation Strategy

**Status:** Accepted
**Date:** 2026-09-29
**Milestone:** M7

## Context

M7 adds write operations (PUT and DELETE) to the cache gateway. When a product is
updated or deleted in Postgres, the cached value becomes stale. Without
invalidation, reads serve the old value until TTL expires (up to 5-6 minutes with
jitter), which is unacceptable for correctness.

We must choose a strategy for keeping the cache consistent with the database after writes.

## Decision

**Cache-aside with delete-on-write**. After updating or deleting a product in
Postgres, the cache entry is immediately deleted (not updated). The next read
will miss the cache and fetch the current value from Postgres.

## Alternatives Considered

### 1. Update-on-write (cache-aside + update)

After writing to Postgres, update the cache with the new value.

**Trade-off:** This creates a write-write race:

```
T1: writes DB → value A
T2: writes DB → value B
T2: updates cache → B
T1: updates cache → A (stale!)
```

The cache ends up with T1's older value even though T2 is the latest in Postgres.
This is worse than having no cache at all.

**Verdict:** Rejected. The race creates silent correctness bugs.

### 2. Write-through

Write to both Postgres and cache atomically (or as close as possible). On success,
both are consistent.

**Trade-off:**
- **Pro:** No staleness on the write path. The cache always reflects the latest write.
- **Pro:** No race window between DB and cache updates.
- **Con:** Write latency includes cache latency (one more round trip).
- **Con:** Cache failure blocks writes. If Redis is down, writes fail even though
  Postgres is healthy. This makes the cache a single point of failure for a service
  whose purpose is protecting Postgres from load, which is backwards.
- **Con:** More complex: requires transactional semantics across two systems, or
  accept that a cache-write failure still leaves stale data.

**Verdict:** Rejected for now. The failure mode (cache outage blocks writes) is
worse than the small race window. Write-through becomes attractive when:
- Writes are rare and reads are frequent (already true)
- Consistency requirements are stricter than "eventually consistent within ~5ms"
  (not required for this project)
- Cache is as reliable as the database (not true for Redis vs Postgres)

### 3. TTL-only (no invalidation)

Let the cache expire naturally on TTL. Stale data persists for up to TTL duration.

**Trade-off:**
- **Pro:** Simplest implementation (no invalidation logic).
- **Con:** Stale reads for 5-6 minutes after a write (300s base TTL + 10% jitter).
  This is unacceptable for product updates (price changes, name corrections) where
  users expect immediate visibility.

**Verdict:** Rejected. The staleness window is too long for correctness.

### 4. Pessimistic locking (lock before read)

Acquire a distributed lock before every read to ensure consistency.

**Trade-off:**
- **Pro:** Guarantees consistency across all workers.
- **Con:** Adds round-trip latency to *every* read (not just writes), defeating the
  purpose of caching.
- **Con:** Lock holder failure blocks all reads until lease expiry.
- **Con:** Massive complexity for a problem we don't have.

**Verdict:** Rejected. The cure is worse than the disease.

## Chosen Approach: Delete-on-Write

### Implementation

```python
async def update(product_id: int, update_data: ProductUpdate) -> ProductRead:
    # 1. Write to source of truth (Postgres) first
    product = await repository.update_by_id(product_id, **update_data)
    if product is None:
        raise NotFoundError(...)

    # 2. Invalidate cache (fail-open: write already succeeded)
    await cache.delete(product_id)

    return ProductRead.model_validate(product)
```

**Why delete instead of update:**
- Delete is atomic. Worst case: next read is a cache miss.
- Update creates the write-write race described above.
- Delete is simpler: no need to serialize the updated value.

### The Race Window

There's a small window where:
1. Writer updates Postgres ✅
2. Writer deletes cache key ⏳
3. Concurrent reader checks cache, gets old value ❌

**Measured duration:** < 5ms (see `test_race_window_exists_but_is_small`).

This is acceptable because:
- The window is brief (< 5ms) compared to alternatives (TTL-only = 5-6 minutes).
- Stale reads during the window are rare (requires concurrent read+write on same key).
- The data is eventually consistent (next read after invalidation gets fresh value).

**When this becomes unacceptable:**
- Financial transactions where even brief staleness is unacceptable.
- Systems where concurrent write+read on the same key is common.
- Compliance requirements for strict read-after-write consistency.

In those cases, re-evaluate write-through or pessimistic locking despite their
latency/complexity costs.

### Failure Modes

**Cache delete fails (Redis down or timeout):**
- The write to Postgres already succeeded, so we don't fail the request.
- Cache entry becomes stale until TTL expires (5-6 minutes).
- This is the same as the TTL-only strategy, just less frequent.
- Logged as `cache_invalidation_failed` for alerting.

**Repository commit fails after delete (should not happen with current impl):**
- Current implementation commits in `update_by_id` before returning.
- If we moved to two-phase (update then commit), a commit failure after delete
  would leave the cache empty for a non-updated row.
- This is prevented by the current transactional scope.

## Consequences

### Positive
- **Simple:** One delete call after DB write. No complex transaction coordination.
- **Safe:** No write-write race. Worst case is a cache miss, not stale data.
- **Fail-open:** Cache outage degrades to uncached behavior, doesn't block writes.
- **Small race window:** < 5ms, measured and acceptable.

### Negative
- **Race window exists:** Brief staleness possible during concurrent write+read.
- **Next read pays a cache miss:** First read after update/delete hits the database.

### Neutral
- **Delete-on-write is industry standard:** This is the default pattern for
  cache-aside systems (Redis, Memcached, CDNs).
- **Benchmarks required:** The race window is measured in tests, but the stale-read
  percentage under realistic load (M7 consistency benchmarks) will quantify the
  real-world impact.

## Compliance with Project Goals

**M7 goal:** Add write operations while maintaining cache correctness.

This approach:
- ✅ Maintains correctness (no silent stale data beyond brief race window).
- ✅ Preserves fail-open behavior (cache outage doesn't block writes).
- ✅ Measurable (race window quantified in tests and benchmarks).
- ✅ Documents trade-offs (ADR explains when to reconsider).

## References

- Kleppmann, *Designing Data-Intensive Applications*, Chapter 5: Replication
  (discusses write-through vs cache-aside trade-offs).
- Redis best practices: DELETE on write, not UPDATE
  (https://redis.io/docs/manual/patterns/twitter-clone/).
- ADR-0003: Bloom filter trade-off (deletion problem is similar: cannot remove,
  but measured and acceptable).

## Future Work

If strict read-after-write consistency becomes required:
1. Evaluate write-through with compensating transactions for cache failures.
2. Measure write latency impact (cache round-trip per write).
3. Decide if consistency gain is worth latency and complexity cost.
