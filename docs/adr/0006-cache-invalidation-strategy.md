# ADR-0006: Cache Invalidation Strategy

**Status:** Accepted
**Date:** 2026-09-29
**Milestone:** M7
**Amended:** 2026-10-05 — benchmarked. Two claims in the original text did not
survive measurement: that the worst case is a cache miss rather than stale data,
and that the race window is bounded at ~5ms. Both are corrected in place below,
with the original claims left visible rather than quietly rewritten. The
decision itself is unchanged.

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

**Measured duration:** < 5ms for *this* window
(`test_race_window_exists_but_is_small`) — but that is a unit test of the
writer-side gap, and it is not the window that matters most. See
**Measured under load** below: the end-to-end p99 staleness is 12.4 seconds,
not 5 milliseconds.

The writer-side window itself is acceptable because:
- It is brief compared to the alternatives (TTL-only = a full TTL).
- It requires a concurrent read and write on the same key.
- The data is eventually consistent.

### The window this ADR missed

The sequence above is writer-ordered: update Postgres, delete the key, and a
reader slipping between the two sees the old value. That reader then moves on
and the stale value is gone.

There is a second interleaving, reader-ordered, which is worse because the
stale value *persists*:

```
reader                          writer
------                          ------
GET key -> miss
SELECT -> V_old
                                UPDATE -> V_new
                                DELETE key        <- invalidation runs here
SET key = V_old                                   <- and is then undone
```

The reader's cache fill lands *after* the writer's delete and reinstates the
superseded value for a full TTL. Invalidation ran correctly and was simply
overwritten. Delete-on-write does not close this; nothing in the decision above
addresses it.

Closing it requires either a compare-and-set on a monotonic version (reject a
write-back older than the current value) or a short lock around the read's
cache fill. Neither is implemented.

### Measured under load

Measured 2026-10-05, 50 reader VUs and 50 writer VUs over 100 hot keys for 60s,
4 workers, 60s TTL. Full method and raw data in
[benchmarks/CONSISTENCY.md](../../benchmarks/CONSISTENCY.md).

| | No invalidation | Delete-on-write |
|---|---|---|
| Writer cannot see own write | 99.48% | **1.27%** |
| Value age — median | 29,700 ms | **474 ms** |
| Value age — p99 | 64,014 ms | **12,356 ms** |
| Value age — max | 67,959 ms | **28,301 ms** |
| Cache hit rate | 99.51% | **66.95%** |
| DB QPS | 122–170 | **329–347** |

Two results contradict claims made earlier in this ADR:

1. **Staleness is not bounded by a 5ms window.** p99 is 12.4s and max is 28.3s,
   caused by the reader-ordered interleaving above. The improvement over no
   invalidation is large and real — 63x at the median — but it is a reduction,
   not an elimination.

2. **Invalidation roughly triples database load.** Hit rate falls from 99.5% to
   67% because every write deletes a key that is about to be read again. At this
   workload's 1:2 write:read ratio the cache substantially stops working. That
   cost was not stated anywhere in the original decision, and it is the main
   thing a reader deciding between these strategies needs to know.

The decision itself stands: delete-on-write still beats update-on-write, whose
write-write race produces *silently wrong* data rather than merely stale data.
But it is a large improvement, not a correctness guarantee.

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
- **No write-write race:** unlike update-on-write, a slow writer cannot clobber
  a newer value with an older one.
- **Fail-open:** Cache outage degrades to uncached behavior, doesn't block writes.
- **Large staleness reduction, measured:** median value age 29,700ms → 474ms,
  read-your-own-writes failures 99.48% → 1.27%.

### Negative
- **Stale reads are reduced, not eliminated:** p99 staleness measured at 12.4s,
  max 28.3s, via the reader-ordered interleaving documented above. The earlier
  claim that the "worst case is a cache miss, not stale data" is wrong.
- **Database load roughly triples:** cache hit rate 99.5% → 67%, DB QPS
  122–170 → 329–347 under a 1:2 write:read workload. The more write-heavy the
  workload, the less the cache does.
- **Next read pays a cache miss:** first read after update/delete hits the database.

### Neutral
- **Delete-on-write is industry standard:** This is the default pattern for
  cache-aside systems (Redis, Memcached, CDNs).
- **Benchmarked 2026-10-05:** the stale-read percentage under load is now
  measured, not assumed — see **Measured under load** above and
  [benchmarks/CONSISTENCY.md](../../benchmarks/CONSISTENCY.md). The benchmark
  that was supposed to produce this could not previously detect staleness at
  all: its detector shared state across k6 VUs, which have isolated runtimes,
  so it reported 0% stale for every configuration including ones with no
  invalidation. See ADR-0008 for the same class of harness bug.

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
