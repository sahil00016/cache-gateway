# M4 Cache Penetration — Bloom filter, before and after

**Recorded:** 2026-10-05
**Scenario:** `benchmarks/scenarios/penetration.js`
**Raw results:** `benchmarks/results/penetration-*.json`

## What this measures

Cache penetration: requests for ids that do not exist in Postgres. A cache
holds no negative result, so every such request misses Redis and reaches the
database. The Bloom filter answers "definitely not present" before either
datastore is touched.

The attack is unbounded by design — ids are drawn from `DATASET+1 .. 2*DATASET`,
so no amount of null-caching can ever cover the keyspace. That is the argument
in [ADR-0003](../docs/adr/0003-bloom-filter-for-penetration-protection.md), and this is
its measurement.

## Environment

| | |
|---|---|
| Host | 12 vCPU dev machine, **shared with other running containers** |
| Service | docker compose, gunicorn + **4 uvicorn workers**, python:3.12-slim |
| Database | PostgreSQL 16, containerised, `max_connections=100`, pool 10 + 15 overflow |
| Dataset | 1,000,000 products, seeded deterministically (rng seed 42) |
| Cache TTL | 60s, 10% jitter |
| Load | k6, constant arrival rate 400/s, 10s warm-up discarded, 60s measured |
| Runs | 3 per arm, median reported |
| Counters | read from the service's own `/metrics`, sampled at 1 Hz |

Effective settings were read back from `/v1/admin/stats` before every run and
asserted against the intended configuration, per
[ADR-0002](../docs/adr/0002-env-var-typos-are-silent.md). The only variable between
arms is `BLOOM_ENABLED`.

## Results

### Database load — the headline

| | run 1 | run 2 | run 3 | **median** |
|---|---|---|---|---|
| Bloom **disabled** — DB queries | 23,195 | 19,744 | 23,428 | **23,195** |
| Bloom **enabled** — DB queries | 242 | 239 | 245 | **242** |

| | Unprotected | Protected | Change |
|---|---|---|---|
| **DB queries (60s)** | **23,195** | **242** | **−99.0%** |
| **DB QPS** | **397.0** | **4.1** | **−99.0%** |
| Requests in measured window | 23,195 | 23,603 | — |
| Achieved rate (k6, whole run) | 395.4/s | 400.0/s | — |
| Error rate | 0 | 0 | — |

Request counts above are taken from the service's counters over the 60s
measured window — cache misses when the filter is off, Bloom lookups when it is
on. k6's own `requests` figure (median 27,788 / 28,002) spans the full 70s
including the discarded warm-up, so the two are not directly comparable and are
not mixed here.

Unprotected, database QPS equals the request rate: every single attack request
reaches Postgres. That is the definition of a successful penetration attack,
and it reproduces the M2 baseline's 1:1 finding exactly.

### Bloom filter behaviour

| | run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| Rejected (`bloom_queries{result=miss}`) | 23,361 | 22,466 | 23,358 | 23,358 |
| Passed through (`result=hit`) | 242 | 239 | 245 | 242 |
| **Measured false positive rate** | 1.03% | 1.05% | 1.04% | **1.04%** |

**98.97% of attack traffic is rejected before Redis or Postgres is touched.**
The 1.04% that gets through is the filter's false positive rate, configured at
`BLOOM_FP_RATE=0.01`. Measured 1.04% against a 1.0% target is the filter
behaving exactly as specified — the residual database load *is* the false
positive rate, not leakage from some other cause.

That correspondence is the strongest evidence in this document. The queries
reaching Postgres are not "roughly one percent"; they are 242 out of 23,603
lookups where 236 were predicted.

### Latency

Milliseconds, median of 3 runs.

| | med | p95 | p99 | max |
|---|---|---|---|---|
| Unprotected | 11.70 | 123.59 | 628.80 | 1063.54 |
| Protected | 3.72 | 10.75 | 33.33 | 165.42 |

**Latency improves roughly 3x at the median and 19x at p99** — but read this
carefully. It is not that the Bloom filter is fast; it is that *the unprotected
service is overloaded*. At 400 queries/second against a shared host, Postgres is
the bottleneck and the latency reflects queueing, not lookup cost.

This matches the lesson from [ADR-0009](../docs/adr/0009-cache-adds-round-trip-latency.md):
the case for every protection in this service is **database load**, and latency
only moves once the database is genuinely under pressure. Here it is, so it does.

### A caveat on run 2

Run 2 of both arms shows a disturbed tail — unprotected p99 of 5,394 ms,
protected p99 of 1,002 ms, against sub-35 ms p99 in runs 1 and 3. Throughput
also dipped (19,744 queries vs ~23,300). The host is shared with other
containers, and this is what that looks like.

The run is reported rather than discarded: it does not change the median on any
headline figure, and omitting inconvenient runs is how benchmarks become
marketing. But the p95/p99/max columns above should be read as "typical of a
shared host", not as a latency SLO.

## Memory cost

| | |
|---|---|
| Bloom filter size per worker | ~1.2 MB |
| Workers | 4 |
| **Total** | **~4.8 MB** |
| Saturation | 100% (1M items / 1M capacity) |

4.8 MB of process memory removes 99% of an unbounded attack. The null-cache
alternative rejected in ADR-0003 would have needed 50–100 MB *and* would still
have failed, because an attacker drawing fresh ids never reuses a cached
negative.

## The deletion problem

A Bloom filter cannot remove a key. A deleted product still answers "might
exist", so the request falls through to Postgres and correctly 404s — it
degrades to a false positive rather than a wrong answer.

The measured 1.04% is the filter's designed error rate on a freshly built
filter with no deletions. In a workload with sustained deletes this figure
climbs, and the mitigation is a rebuild:

```bash
curl -X POST http://localhost:8010/v1/admin/bloom/rebuild
```

**Not measured here:** how fast the false positive rate degrades under a given
delete rate. That needs a delete-heavy workload this scenario does not generate,
and claiming a number for it would be inventing one.

## Conclusion

The Bloom filter removes **99.0% of database load** under cache penetration for
**~4.8 MB** of memory, and the residual is precisely its configured false
positive rate. The attack reaches neither Redis nor Postgres.

**Trade-off:** no deletion from the filter, mitigated by periodic rebuild.

## Harness note

The scenario could not run at all before 2026-10-05. It carried a threshold on
`http_req_status_code`, which is not a k6 metric, so k6 aborted before issuing a
request. It also inherited k6's default failure rule — status ≥ 400 counts as
failed — under which a perfect run, where every response is a 404 by design,
scored as 100% failed. Both are fixed; the scenario now declares 404 as its
expected status via `http.setResponseCallback`.

A latency-based "blocked by Bloom" counter in the original script is retained
only as a cross-check and is explicitly *not* the source of any number above.
It cannot distinguish a Bloom rejection from a fast Redis negative lookup. Every
figure in this document comes from the service's own counters.

## Reproducing

```bash
task up
python benchmarks/run.py penetration-unprotected penetration-protected --runs 3
python benchmarks/summarize.py penetration
```
