# Cache Consistency Protection — Before and After

**Milestone:** M7
**Date:** TBD
**Measured by:** TBD

## What this measures

Cache consistency: when a product is updated or deleted, does the cache serve stale
data? Cache-aside with delete-on-write has a small race window between the DB write
and cache invalidation where concurrent reads can fetch and cache the old value.

This benchmark measures:
1. **Stale read percentage** — how often readers get outdated data
2. **Stale duration** — how long stale data persists

## Methodology

**Scenario:** `benchmarks/scenarios/consistency.js`
**VUs:** 100 (50 readers + 50 writers)
**Hot keys:** 100 products (high contention)
**Reader rate:** 500 RPS (10 reads/sec per VU)
**Writer rate:** 250 WPS (5 writes/sec per VU)
**Duration:** 60 seconds
**Runs per configuration:** 3 (median reported)

All runs use the same instance, dataset, and load. The only variable is the
invalidation strategy.

## Configuration

### Run 1: No invalidation (TTL-only baseline)

Let stale data persist until TTL expires naturally.

```bash
# Disable cache invalidation (baseline)
# Note: This requires a code change to skip the delete() call
WRITE_STRATEGY=ttl_only \
k6 run -e SCENARIO=consistency-no-invalidation benchmarks/scenarios/consistency.js
```

### Run 2: Delete-on-write (M7)

After each write to Postgres, immediately delete the cache entry.

```bash
# With cache invalidation (M7)
WRITE_STRATEGY=cache_aside \
k6 run -e SCENARIO=consistency-with-invalidation benchmarks/scenarios/consistency.js
```

## Results

### Without invalidation (TTL-only)

| Metric | Value |
|---|---|
| Total reads | TBD |
| Stale reads | TBD |
| **Stale read %** | **TBD% (expect high - any read during TTL sees old value)** |
| Median stale duration | TBD ms |
| p95 stale duration | TBD ms (expect long - up to 300-360s TTL) |

### With delete-on-write (M7)

| Metric | Value |
|---|---|
| Total reads | TBD |
| Stale reads | TBD |
| **Stale read %** | **TBD% (expect < 1% - brief race window only)** |
| Median stale duration | TBD ms (expect < 5ms) |
| p95 stale duration | TBD ms (expect < 10ms) |

## Analysis

**Expected findings:**

1. **TTL-only strategy:**
   - Stale reads are common (any reader during the TTL window sees old value)
   - Stale duration is long (up to full TTL of 300-360s)
   - Unacceptable for production (users see outdated prices for minutes)

2. **Delete-on-write (M7):**
   - Stale reads are rare (< 1%) — requires concurrent read+write on same key
   - Stale duration is brief (< 5ms) — the race window between DB commit and
     cache delete
   - Acceptable trade-off: brief, rare staleness vs. complexity/latency of
     write-through or pessimistic locking

**The race window:**

Delete-on-write has a small window where:
1. Writer updates Postgres ✅
2. Writer deletes cache key ⏳ (in flight)
3. Concurrent reader checks cache, gets old value ❌

This is measured in `tests/service/test_consistency.py::test_race_window_exists_but_is_small`
and quantified here under realistic load.

## Trade-offs Measured

| Strategy | Stale read % | Stale duration | Write latency | Complexity |
|---|---|---|---|---|
| **TTL-only** | High (TBD%) | Long (TBD s) | Low | Low |
| **Delete-on-write** | Low (TBD%) | Brief (TBD ms) | Low | Low |
| Write-through | 0% | 0 ms | High (+cache RT) | Medium |
| Pessimistic lock | 0% | 0 ms | Very high (+lock RT) | High |

**Verdict:** Delete-on-write strikes the best balance for this project's requirements.
The brief race window (< 5ms, < 1% of reads) is acceptable given the simplicity and
fail-open behavior.

**When to reconsider:**
- Financial transactions where even brief staleness is unacceptable
- Systems where concurrent write+read on the same key is common (> 10% of requests)
- Compliance requirements for strict read-after-write consistency

See ADR-0006 for detailed trade-off analysis.

## Notes

**Why measure TTL-only as baseline?**

It's the simplest strategy (no invalidation logic) and the obvious alternative to
delete-on-write. Measuring it proves that cache invalidation is necessary, not just
nice-to-have. If TTL-only showed < 1% stale reads, we wouldn't need M7 at all.

**Why not benchmark write-through?**

Write-through would show 0% stale reads but isn't implemented. The trade-off
(cache outage blocks writes) is already disqualifying for this project's fail-open
requirement. See ADR-0006 for the decision rationale.

**Why track stale duration?**

Duration measures the *quality* of staleness. A 5ms window is tolerable; a 5-minute
window is not. This metric separates brief races (acceptable) from long staleness
(unacceptable).

## Conclusion

TBD after running benchmarks.

Expected: Delete-on-write reduces stale reads from X% (TTL-only) to < 1% with a
median stale duration < 5ms. The race window is brief and rare, making it an
acceptable trade-off for simplicity and fail-open behavior.

See ADR-0006 for the full decision context.
