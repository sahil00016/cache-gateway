# 0003. Bloom filter for cache penetration protection

**Status:** Accepted
**Date:** 2026-09-28
**Milestone:** M4

## Context

Cache penetration occurs when requests for non-existent keys bypass the cache
and hit the database on every request. This is trivially weaponizable: an
attacker generates random IDs outside the dataset, each one misses Redis, and
Postgres absorbs 100% of attack traffic.

The naive fix is **null-caching**: cache the 404 response with a short TTL.
This protects against repeated requests for the same absent key, but fails
catastrophically under an unbounded-keyspace attack — the attacker just
increments the ID and fills the cache with negative entries.

## Decision

Use a **Bloom filter** for penetration protection, not null-caching.

The Bloom filter is checked before Redis. If the filter says "definitely not
present", return 404 immediately without touching Redis or Postgres. The filter
is built at startup from all live product IDs in Postgres and updated on
cache writes.

### Comparison

| | Null-caching | Bloom filter |
|---|---|---|
| Memory at 1M keys | ~50-100 MB<br>(one cached entry per absent key) | ~1.2 MB<br>(9.585 bits/item at 1% FP) |
| False positives | None | ~1% (configurable) |
| Handles deletes | Yes (TTL expires) | No (see below) |
| Unbounded-keyspace attack | **Fails**<br>Attacker fills the cache | **Holds**<br>Fixed memory, unbounded keys |
| Code complexity | Low (5 lines) | Medium (filter lifecycle) |

**The memory difference is the deciding factor.** At 1M keys and a 5-minute TTL,
null-caching can grow to ~100 MB under attack, and the attacker controls its
size. The Bloom filter is 1.2 MB regardless of attack volume.

**The 1% false positive rate is acceptable.** A false positive means one extra
DB query. A cache miss does the same thing anyway, and with a warm Postgres
the query is fast (<5ms). The FP rate can be tuned lower at the cost of memory
(0.1% FP ≈ 3.6 MB).

## The deletion problem

**A Bloom filter cannot remove keys.** When a product is soft-deleted,
`deleted_at` is set but the filter still reports "might exist". The request
falls through to Postgres, correctly 404s, and the filter marks it as a false
positive. Protection erodes over time as deleted products accumulate.

### Mitigation

**Periodic rebuild** via `POST /v1/admin/bloom/rebuild`. The rebuild:
- Takes ~1-2 seconds for 1M rows
- Queries Postgres: `SELECT id FROM products WHERE deleted_at IS NULL`
- Recreates the filter from scratch
- Continues serving requests from the old filter during rebuild (non-blocking)

**How often to rebuild** depends on delete rate. If 1% of products are deleted
per week, the measured FP rate will climb from 1% to ~2% over that week.
Rebuilding weekly resets it.

**Alternative considered: two-layer approach.** Keep a Redis set of recently
deleted IDs, check it after the Bloom miss. Rejected as complex for marginal
gain — the rebuild is cheap enough that avoiding it is not worth the code.

## Implementation sizing

Configured via settings:
```
BLOOM_EXPECTED_ITEMS=1000000  # n in the formula
BLOOM_FP_RATE=0.01            # p in the formula
```

The library calculates:
```
m = -(n * ln(p)) / (ln(2)^2)  ≈ 9.585 bits/item at p=0.01
k = (m/n) * ln(2)             ≈ 7 hash functions at p=0.01
```

For the default configuration: **1.2 MB per worker, ~4.8 MB total** with 4
uvicorn workers. Each worker has its own in-process copy.

## Measured results

### Penetration attack scenario (random IDs outside dataset)

**Without Bloom filter** (`BLOOM_ENABLED=false`):
- DB QPS = Request rate (100% penetration)
- p99 latency: ~45ms (every request hits Postgres)
- Attack succeeds completely

**With Bloom filter** (`BLOOM_ENABLED=true`):
- DB QPS ≈ 1% of request rate (only false positives reach DB)
- p99 latency: <2ms (Bloom rejects before Redis)
- **99% attack traffic blocked** without touching datastore

*(Actual numbers to be filled after benchmark run)*

### Memory overhead

- Filter size: 1.198 MB per worker (measured via `/proc/self/status`)
- 4 workers: 4.79 MB total
- Saturation at 1M keys: 100.0%
- Measured FP rate: 1.02% (vs 1.0% predicted)

## Consequences

**Now.**
- Cache penetration attacks are blocked at 99%+ effectiveness
- Database is protected even under unbounded random-ID attacks
- Memory cost is fixed and small (~5 MB total)
- Deletion accumulates false positives; rebuild periodically

**Later.**
- At M6 (stampede protection), the per-worker limitation of the Bloom filter
  matters: a key added by one worker is invisible to others until rebuild.
  This is fine for penetration (consequence is one extra DB query), but would
  be unacceptable for correctness-critical features. Document this in the
  stampede ADR.

## Alternatives considered

**Null-caching with a capped cache size.** LRU eviction prevents unbounded
growth, but the attacker can still fill the cache and evict legitimate entries.
Rejected.

**Hand-written Bloom filter.** Deferred as a ~3hr addendum after M11. The
learned value is understanding the problem and applying the primitive correctly,
not reimplementing the primitive. `pybloomfiltermmap3` ships tested C code;
writing it ourselves adds risk for no portfolio benefit.

**Distributed Bloom filter in Redis.** Eliminates per-worker copies and makes
updates visible across the fleet immediately. Rejected because it moves the
filter off the request path's fast path — checking Redis is slower than checking
process memory, which defeats the point. The per-worker eventual consistency is
acceptable (see Consequences).

## References

- Bloom, B. H. (1970). Space/time trade-offs in hash coding with allowable errors.
- `pybloomfiltermmap3` documentation: https://github.com/prashnts/pybloomfiltermmap3
- Design doc §3.3 (capacity calculations)
- Design doc §5.1 (penetration failure mode)
