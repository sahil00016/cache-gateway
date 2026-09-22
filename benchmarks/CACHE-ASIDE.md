# M3 — cache-aside with Redis

**Recorded:** 2026-09-22
**Commit:** the M3 branch tip
**Compares against:** `benchmarks/BASELINE.md` (M2, no cache). Same methodology,
same dataset, same load profile — see ADR-0008 for why that must not change
between runs.

## Environment

Identical to the M2 baseline, plus:

| | |
|---|---|
| Cache | Redis 7, cache-aside, TTL 60s + 10% jitter |
| Redis pool | 50 connections, shared across 4 gunicorn workers |
| Cache key | `product:v1:{id}` |
| Runs | 3 per distribution, median reported, cold cache at the start of each |

TTL was set to 60s for this run (default in `.env.example` is 300s) so
staleness would be visible within a single benchmark window, and the
**effective value was read back from the running service** via
`/v1/admin/stats` before each run rather than assumed — see ADR-0002.

## Results

**Zipfian** (skew 1.1)

| run | rps | med | p95 | p99 | max | DB queries | cache hit rate |
|---|---|---|---|---|---|---|---|
| 1 | 398.6 | 3.46 | 5.76 | 9.57 | 751.92 | 21,379 | 40.4% |
| 2 | 400.0 | 3.35 | 5.93 | 12.69 | 564.90 | 19,928 | 44.6% |
| 3 | 400.0 | 3.40 | 6.27 | 12.45 | 160.54 | 20,088 | 44.2% |
| **median** | **400.0** | **3.40** | **5.93** | **12.45** | 564.90 | ~20,088 | **44.2%** |

**Uniform**

| run | rps | med | p95 | p99 | max | DB queries | cache hit rate |
|---|---|---|---|---|---|---|---|
| 1 | 398.5 | 3.94 | 7.50 | 119.82 | 603.81 | 35,156 | 2.0% |
| 2 | 399.4 | 3.86 | 6.61 | 13.52 | 455.05 | 35,010 | 2.6% |
| 3 | 400.0 | 3.83 | 6.60 | 15.63 | 374.01 | 35,086 | 2.5% |
| **median** | **399.4** | **3.86** | **6.61** | **15.63** | 455.05 | ~35,086 | **2.5%** |

Latencies in milliseconds. Zero 404s in every run.

## Read this honestly, against the M2 baseline

| | M2 (no cache), p99 | M3 (cache-aside), p99 | change |
|---|---|---|---|
| zipf | 6.73 ms | 12.45 ms | **+85%** |
| uniform | 4.57 ms | 15.63 ms | **+242%** |

**Tail latency got worse, not better.** Every request — hit or miss — now
pays a Redis round trip; a miss pays that plus the Postgres query plus a
Redis write. Median latency moved only slightly (2.53 ms → ~3.4-3.9 ms),
consistent with one extra local network hop, but the tail widened well beyond
that. The cause of the disproportionate tail-latency increase (network,
connection-pool contention under the shared 50-connection pool, or something
else) has not been root-caused yet — recorded as a real, measured cost rather
than investigated further here. See ADR-0009.

**Database queries dropped roughly in proportion to the hit rate**, which is
mechanically guaranteed — a hit costs zero queries, a miss costs exactly one:

- Zipfian: ~44% fewer queries reaching Postgres (36,000 → ~20,000).
- Uniform: ~2.5% fewer queries. Functionally no benefit.

**The zipfian hit rate (44%) is lower than the "top 1% of keys take 59% of
traffic" figure from ADR-0008 might suggest**, and this was checked rather
than assumed: a control run with the TTL raised to 6000s (effectively never
expiring) produced 39.1%, barely different from the 60s-TTL runs. TTL length
is not the limiting factor at this rate and duration — the limiting factor is
that even the "hot" 1% of keys is still 10,000 distinct keys, each seeing on
the order of one request per 40 seconds at 400 req/s. That is not enough
repetition within a 90-second run to turn most of that traffic into hits. A
longer run, or a smaller/hotter keyspace, would raise the hit rate; this run's
methodology (fixed by ADR-0008) does not.

## The honest conclusion

This validates the M2 thesis and sharpens it: **caching wins here on database
load under a skewed access pattern, and only under a skewed access pattern.**
Against a uniform workload — a real possibility for some catalogs — cache-aside
adds latency for essentially no benefit. A production deployment of this
service would need to know its actual key-access distribution before this
milestone's cache is worth deploying at all, and the disproportionate tail
latency cost needs investigating before this is worth deploying under a
latency-sensitive SLO even where the hit rate is good.

M4-M7 add machinery on top of this same path — a Bloom filter, request
coalescing, a distributed lock, jittered TTLs, write invalidation. Each one
needs measuring for cost as well as benefit, the same way this milestone
measured the base cache's.

## Reproducing

```bash
task up
# TTL override for this run only -- edit CACHE_TTL_SECONDS in .env, or:
CACHE_TTL_SECONDS=60 docker compose up -d --build api
curl -s http://localhost:8010/v1/admin/stats  # verify before trusting the run
docker compose exec redis redis-cli FLUSHALL  # start cold, like M2's cold Postgres

k6 run -e DIST=zipf    -e SCENARIO=cache-aside -e RATE=400 -e DURATION=60s benchmarks/scenarios/baseline.js
k6 run -e DIST=uniform -e SCENARIO=cache-aside -e RATE=400 -e DURATION=60s benchmarks/scenarios/baseline.js
```
