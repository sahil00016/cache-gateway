# 0004. TTL jitter for cache avalanche protection

**Status:** Accepted
**Date:** 2026-09-28
**Milestone:** M5

## Context

Cache avalanche occurs when many keys expire simultaneously, causing a sharp
spike in database load. This happens when keys are written around the same time
with an identical TTL — common during batch warm-up, post-deployment cache
rebuilds, or after a cache outage recovery.

**Example:** A deployment warms 10,000 hot keys with a 5-minute TTL. Five
minutes later, all 10,000 expire in the same second. Every subsequent request
for those keys becomes a cache miss, and Postgres takes 10,000 queries in one
second instead of the usual baseline.

Unlike a stampede (one hot key, many concurrent requests), an avalanche involves
many keys expiring together. The fix is different: decouple their expiry times.

## Decision

Add **uniform jitter** on top of the base TTL when writing keys to the cache:

```python
jitter = random.uniform(0, cache_ttl_jitter_seconds)
final_ttl = cache_ttl_seconds + jitter
```

**Why add rather than subtract:** Subtracting jitter shortens the minimum
freshness window. Adding jitter only spreads out *when* keys expire, which is
the whole point — it decorrelates the expiry of keys written around the same
time.

### Configuration

```
CACHE_TTL_SECONDS=300        # Base TTL (5 minutes)
CACHE_TTL_JITTER_PCT=10      # Jitter as % of base TTL
```

**Result:** Keys written in the same batch get TTLs between 300s and 330s
(spread over 30 seconds), instead of all expiring at exactly 300s.

## Why 10% and not 50%

**10% jitter is the standard recommendation** across Redis, Memcached, and AWS
ElastiCache documentation. It's small enough that freshness is barely affected
(users don't notice a 30s variance on a 5-minute TTL), but large enough to
meaningfully spread expiry.

**50% would be overkill** and erode the value of caching:
- A 300s TTL with 50% jitter → 300-450s variance
- Keys refresh anywhere from 5 to 7.5 minutes
- Stale data persists longer, cache hit rate drops

**Alternative considered: fixed absolute jitter** (e.g., always 30s regardless
of base TTL). Rejected because it doesn't scale with the TTL — 30s is 10% of a
5-minute TTL but only 0.5% of a 1-hour TTL. Percentage-based jitter scales
correctly.

## Measured results

### Avalanche scenario (1000 warm keys, 60s TTL, 400 RPS)

**Without jitter** (`CACHE_TTL_JITTER_PCT=0`):
- All 1000 keys expire at t=60s
- DB QPS spike: TBD queries/second (expect ~400 QPS = 100% of traffic)
- Spike duration: ~1-2 seconds
- p99 latency spike: TBD ms

**With 10% jitter** (`CACHE_TTL_JITTER_PCT=10`):
- Keys expire over 60-66s window (spread over 6 seconds)
- DB QPS: TBD queries/second (expect gradual rise, not a spike)
- Spike smoothed: TBD% reduction in peak QPS
- p99 latency: TBD ms (no spike)

*(Actual numbers to be filled after benchmark run)*

### Jitter bounds verification

From `test_set_ttl_never_exceeds_the_configured_jitter_ceiling`:
- Base TTL: 60s, jitter: 10% (6s ceiling)
- 20 consecutive writes all produced TTL between 60-66s ✓
- No writes exceeded the ceiling ✓
- No writes went below the base ✓

## Implementation

**Location:** `ProductCacheRepository._ttl_with_jitter()`

```python
def _ttl_with_jitter(self) -> int:
    jitter = random.uniform(0, self._settings.cache_ttl_jitter_seconds)
    return round(self._settings.cache_ttl_seconds + jitter)
```

**Used in:** `ProductCacheRepository.set()` on every cache write

**Toggleable:** Set `CACHE_TTL_JITTER_PCT=0` to disable (for demonstrating the
spike in benchmarks)

## Consequences

**Now.**
- Batch warm-ups no longer cause synchronized expiry spikes
- DB load smooths out over the jitter window instead of spiking
- Cache freshness variance is negligible (30s on a 300s TTL)
- Zero code changes needed at call sites — jitter is applied transparently

**Later.**
- If TTLs become dynamic (e.g., per-entity freshness requirements), jitter
  scales with them automatically because it's percentage-based
- At M6 (stampede protection), jitter does NOT solve the stampede problem —
  that's one hot key, many concurrent requests. Jitter only decorrelates expiry
  across different keys.

## Alternatives considered

**Exponential backoff on miss.** After a cache miss, write the value back with
an exponentially increasing TTL (e.g., 60s → 120s → 240s). This naturally
decorrelates TTLs over time. Rejected because it makes cache behavior
unpredictable — the same key can have wildly different TTLs depending on miss
timing, which complicates debugging and capacity planning.

**Deterministic jitter based on key hash.** Instead of `random.uniform()`, derive
jitter from `hash(key) % jitter_ceiling`. This makes TTLs reproducible for the
same key. Rejected as unnecessary complexity — reproducibility doesn't buy
anything here (the expiry time isn't user-visible), and randomness is simpler.

**No jitter, just stagger the warm-up.** Spread key writes over time during
warm-up instead of writing them all at once. Rejected because warm-ups aren't
the only source of synchronized writes — cache outages, deployments with full
restarts, and batch invalidations all cause the same problem. Jitter handles all
of them.

## References

- Redis best practices: https://redis.io/docs/manual/patterns/distributed-locks/#performance-crash-recovery-and-fsync
- AWS ElastiCache: "Add jitter to avoid the thundering herd"
- Design doc §5.2 (avalanche failure mode)
- `test_set_ttl_never_exceeds_the_configured_jitter_ceiling` (jitter bounds test)
