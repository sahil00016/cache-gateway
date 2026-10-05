# M6 Cache Stampede — request coalescing, before and after

**Recorded:** 2026-10-05
**Scenario:** `benchmarks/scenarios/stampede.js`
**Raw results:** `benchmarks/results/stampede-*.json`

## What this measures

Cache stampede: many concurrent requests for **one** key that has just expired.
Without protection each issues its own identical database query. With
coalescing one request per worker queries and the rest wait on its result
([ADR-0005](../docs/adr/0005-request-coalescing-for-stampede-protection.md)).

## Environment

| | |
|---|---|
| Host | 12 vCPU dev machine, **shared with other running containers** |
| Service | docker compose, gunicorn + **4 uvicorn workers** |
| Database | PostgreSQL 16, pool 10 + 15 overflow |
| Load | 200 VUs, no think time, **all on product id 42** |
| Cache TTL | **5s, jitter 0** — ~12 expiry events per 60s run |
| Duration | 60s |
| Runs | 3 per arm |
| Counters | service `/metrics`, sampled at 1 Hz |

The only variable is `COALESCE_ENABLED`.

## Results

### Database queries

| | run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| No coalescing | 404 | 210 | 224 | **224** |
| With coalescing | 23 | 33 | 30 | **30** |

Achieved throughput varied between runs (126–462 req/s) because 200 VUs with no
think time saturate the service, so the absolute counts partly track how much
load the client delivered. Normalising removes that:

| | run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| No coalescing — DB queries per 1,000 requests | 14.49 | 13.64 | 29.33 | **14.49** |
| With coalescing — DB queries per 1,000 requests | 1.47 | 1.60 | 1.44 | **1.47** |

**89.9% of duplicate queries eliminated**, and the normalised protected figure
is stable to ±0.08 across runs where the raw counts swing 23–33.

### Per stampede event

With ~12 expiries per run:

| | queries per expiry |
|---|---|
| No coalescing | **~18.7** |
| With coalescing | **~2.5** |

**~2.5 against a 4-worker ceiling is exactly what ADR-0005 predicts.** The
coalescer behaves as designed — it is below 4 because not every worker happens
to receive a request inside each miss window.

### Direct evidence the mechanism engages

`cache_gateway_coalesced_requests_total` over the measured window:

| | run 1 | run 2 | run 3 |
|---|---|---|---|
| No coalescing | 0 | 0 | 0 |
| With coalescing | 527 | 430 | 468 |

Roughly 470 requests per run waited on a leader instead of issuing their own
query. This is the coalescer counting its own work, not an inference from
timing.

### Latency — coalescing stabilises the tail

Milliseconds.

| | med (median of runs) | p99 range |
|---|---|---|
| No coalescing | 400.93 | 1,372 – **23,852** |
| With coalescing | 573.03 | 1,051 – 1,150 |

The unprotected p99 is not merely worse, it is *unstable* — one run reached
23.9 seconds. Every duplicate query takes a connection from a 25-connection
pool, so a stampede on a saturated service queues everything behind it.
Coalescing holds p99 in a 100 ms band across all three runs.

Median latency is slightly *higher* with coalescing (573 ms vs 401 ms), which is
expected: waiters block on the leader rather than racing it. That is the trade —
a slightly slower median for a tail that does not collapse.

## The claim this scenario could not support

ADR-0005 states 500 concurrent requests produce 500 duplicate queries, reduced
to 4 by coalescing — a 99.2% reduction. **The 500 figure is not reachable, and
the original scenario could not have measured it.**

A stampede only forms inside the window where the key is absent from cache:
from the first miss until the first query writes the value back. For a
single-row primary key lookup that is ~2.5 ms (see `BASELINE.md`). So

```
duplicate queries  ≈  arrival rate × miss window
```

— a function of **arrival rate**, not of client count. Producing 500 duplicates
against a 2.5 ms window needs ~200,000 req/s. This hardware does not reach it,
and no amount of harness quality changes that.

The first design fired 500 VUs with one iteration each. k6 needs seconds to
start 500 VUs, so the burst arrived spread across seconds and almost nothing
landed inside a 2.5 ms window. Measured result: **57 vs 64 queries** for the
protected and unprotected arms — a difference inside the noise, with only 23
requests coalescing across an entire run. It measured background traffic.

The redesign trades the one-shot burst for sustained concurrency (200 VUs always
in flight, so every expiry meets 200 simultaneous misses) and one expiry for
twelve (5 s TTL). That produces a measurable stampede — just a ~19-deep one
rather than a 500-deep one.

**Corrected claim: 86.6% fewer duplicate queries (224 → 30), or 89.9%
normalised per request — not 99.2%.** The protected side of the original
prediction was right; the unprotected side was overstated by more than an order
of magnitude.

## Conclusion

Request coalescing eliminates **89.9%** of duplicate database queries during a
cache stampede and reduces per-event duplicates from ~18.7 to ~2.5, within the
4-worker ceiling the design predicts. It also keeps p99 latency inside a 100 ms
band where the unprotected service ranges to 23.9 seconds.

The cost is a slightly higher median latency, because waiters block on a leader.

**The headline reduction is 86–90%, not 99.2%.** The difference is entirely in
the baseline: a real stampede on a fast query is about 19 duplicates deep, not
500. Coalescing is still clearly worth its ~160 lines.

## Harness note

This scenario was redesigned on 2026-10-05 because the original could not
produce a stampede at all — see **The claim this scenario could not support**
above. Numbers from before that date describe background traffic on unrelated
keys, not coalescing.

Database query counts come from the service's own counters. k6 cannot observe
them, and the original script's note saying so was correct.

## Reproducing

```bash
task up
python benchmarks/run.py stampede-no-coalesce stampede-with-coalesce --runs 3
python benchmarks/summarize.py stampede
```
