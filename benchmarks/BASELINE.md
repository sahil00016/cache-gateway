# M2 Baseline — no cache

**Recorded:** 2026-09-21
**Commit:** the M2 branch tip
**Purpose:** the control. Every protection added at M3 to M7 is measured as a
delta against these numbers.

## Environment

| | |
|---|---|
| Host | 12 vCPU dev machine, **shared with other running containers** |
| Service | docker compose, gunicorn + **4 uvicorn workers**, python:3.12-slim |
| Database | PostgreSQL 16, containerised, `max_connections=100`, pool 10 + 15 overflow |
| Dataset | 1,000,000 products, 194 MB, seeded deterministically (rng seed 42), `ANALYZE`d |
| Cache | **none** — this is the point |
| Load | k6, constant arrival rate 400/s, 30s warm-up discarded, 60s measured |
| Runs | 3 per distribution, median reported |

## Results

**Zipfian** (skew 1.1 — the top 1% of keys take ~59% of traffic; the realistic case)

| run | rps | med | p95 | p99 | max | 404s |
|---|---|---|---|---|---|---|
| 1 | 399.6 | 2.53 | 3.60 | 6.73 | 428.64 | 0 |
| 2 | 399.5 | 2.53 | 3.58 | 14.60 | 463.60 | 0 |
| 3 | 400.0 | 2.54 | 3.48 | 4.75 | 229.58 | 0 |
| **median** | **399.6** | **2.53** | **3.58** | **6.73** | 428.64 | 0 |

**Uniform** (every key equally likely — worst case for any cache)

| run | rps | med | p95 | p99 | max | 404s |
|---|---|---|---|---|---|---|
| 1 | 400.0 | 2.55 | 3.38 | 4.40 | 309.53 | 0 |
| 2 | 400.0 | 2.51 | 3.58 | 4.97 | 283.84 | 0 |
| 3 | 400.0 | 2.53 | 3.45 | 4.57 | 261.93 | 0 |
| **median** | **400.0** | **2.53** | **3.45** | **4.57** | 283.84 | 0 |

Latencies in milliseconds.

## The number that matters

**Database queries: 35,963 / 35,957 / 36,002 for 35,963 / 35,957 / 36,002
requests. Exactly 1:1, every run.**

Every single read reaches Postgres. That is the baseline condition, and it is
the figure each protection exists to reduce.

## Reading these honestly

**The service already meets its latency targets without any cache.** N1 asks
for p99 under 5 ms on a hit and N2 for under 50 ms on a miss; the uncached
service delivers a p99 of 4.57–6.73 ms. A single-row primary-key lookup against
a warm 1M-row table is simply fast.

So the case for the cache here is **not** latency — it is **database load**.
At 400 req/s this service issues 400 queries/s. That is what breaks first as
traffic grows, and it is what every following milestone attacks. Any later
claim of a "10x speedup" would be dishonest; the honest claim is a large
reduction in queries reaching Postgres, with latency roughly unchanged until
the database is genuinely under pressure.

**Zipfian p99 is worse and noisier than uniform** (4.75–14.60 vs 4.40–4.97),
which is the opposite of the intuitive expectation — concentrated access should
benefit from Postgres's own buffer cache. The likeliest explanation is
measurement noise: the host is shared with other running containers. It is
recorded rather than explained away, and worth rechecking on a dedicated
instance before drawing conclusions from it.

**The max column (230–464 ms) is outliers, not typical behaviour** — a handful
of requests per 36,000. Likely checkpoint or autovacuum activity. Reported
because omitting inconvenient tail latency is how benchmarks become marketing.

## Two harness bugs found while producing this

Both are written up as ADRs, because both would have silently corrupted every
later comparison:

- **[ADR-0008](../docs/adr/0008-benchmark-harness-correctness.md)** — the first
  run reported 15.7 req/s and a p99 of 1.38 s. The service was fine; the Zipf
  sampler was building a 1,000,000-entry CDF inside the measured iteration,
  once per VU, across 600 VUs. The benchmark was measuring the benchmark.
- **[ADR-0007](../docs/adr/0007-metrics-under-multiple-workers.md)** — with 4
  gunicorn workers, `/metrics` reported whichever worker answered the scrape.
  Eight consecutive reads returned 688, 688, 346, 688, 688, 216, 688, 346. The
  headline metric of the project was showing roughly a quarter of the truth.

A third, smaller bug was caught by noticing 15 unexplained 404s in an otherwise
clean run: fixed-width buckets spanned 1,003,520 keys over a 1,000,000-key
dataset, so the top buckets addressed ids that do not exist. Small enough to
look like noise — which is exactly why it was worth chasing.

## Reproducing

```bash
task up
DB_ECHO=false PYTHONPATH=. .venv/bin/python -m scripts.seed_products --count 1000000
k6 run -e DIST=zipf    -e RATE=400 -e DURATION=60s -e RUN=1 benchmarks/scenarios/baseline.js
k6 run -e DIST=uniform -e RATE=400 -e DURATION=60s -e RUN=1 benchmarks/scenarios/baseline.js
```
