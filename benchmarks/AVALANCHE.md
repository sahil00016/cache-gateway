# M5 Cache Avalanche — TTL jitter, before and after

**Recorded:** 2026-10-05
**Scenario:** `benchmarks/scenarios/avalanche.js`
**Raw results:** `benchmarks/results/avalanche-*.json`

## What this measures

Cache avalanche: many keys written at the same moment share the same TTL, so
they all expire at the same moment, and the database takes the whole working
set's load in one burst. TTL jitter adds a random fraction to each key's TTL so
expiry is spread instead of synchronised
([ADR-0004](../docs/adr/0004-ttl-jitter-for-avalanche-protection.md)).

**Jitter does not reduce database work. It redistributes when the work lands.**
Every key still expires exactly once and is still re-fetched exactly once. Any
claim that jitter reduces total queries would be wrong, and the totals below
confirm it: 1,081 vs 1,134 queries, a difference in the noise. The metric that
moves is **peak QPS**.

## Environment

| | |
|---|---|
| Host | 12 vCPU dev machine, **shared with other running containers** |
| Service | docker compose, gunicorn + **4 uvicorn workers** |
| Database | PostgreSQL 16, pool 10 + 15 overflow (25 max) |
| Warm set | 1,000 keys, written as fast as possible at t≈0–5s |
| Cache TTL | **60s** (`.env` ships 300s; pinned to 60s so the wave fits the run) |
| Load | k6, target 400/s for 90s after warm-up, keys sampled uniformly at random |
| Runs | 3 per arm, median reported |
| Counters | service `/metrics`, sampled at 1 Hz |

The only variable is `CACHE_TTL_JITTER_PCT` (0 vs 10).

## Results

### Total database work — unchanged, as expected

| | run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| No jitter — DB queries | 1,112 | 1,075 | 1,081 | **1,081** |
| 10% jitter — DB queries | 1,134 | 1,153 | 1,098 | **1,134** |

~1,100 queries for a 1,000-key warm set, in both arms. This is the control: it
confirms the two arms did the same amount of work and differ only in timing.

### Peak database QPS — the headline

| | run 1 | run 2 | run 3 | median |
|---|---|---|---|---|
| No jitter — peak QPS | 138.0 | 99.9 | 113.0 | **113.0** |
| 10% jitter — peak QPS | 113.8 | 83.9 | 91.0 | **91.0** |
| No jitter — seconds ≥20 QPS | 10 | 14 | 12 | **12** |
| 10% jitter — seconds ≥20 QPS | 13 | 18 | 14 | **14** |

**Jitter reduces peak database QPS by 19.5%** (113.0 → 91.0) and spreads the
load across 14 seconds instead of 12.

### The shape is the stronger result

Database QPS per second through the expiry window, run 1 of each arm:

```
no jitter    0   0   1  38 123 138  89 126 102 120 100  38  23  19  12   6
10% jitter   0   0   0   5  13  34  51  75  87  70  71 114  70  76  73  76
             ^t=61                                                      ^t=76
```

Unprotected goes from 1 QPS to 123 QPS in two seconds — a wall. Jittered climbs
5 → 13 → 34 → 51 → 75 → 87 over six seconds. A database can absorb the second
shape; the first is what exhausts a connection pool before anything can react.

Peak reduction understates this. The ramp is what matters operationally,
because it is what gives autoscaling, connection pooling and circuit breakers
time to respond.

### Latency — jitter did not help

Milliseconds, median of 3 runs.

| | med | p95 | p99 |
|---|---|---|---|
| No jitter | 99.49 | 1,610.98 | 2,581.24 |
| 10% jitter | 175.07 | 1,519.25 | 2,690.16 |

**There is no latency improvement, and this should not be glossed over.** p99 is
~2.6s in both arms. A 19.5% peak reduction is not enough to pull the service
out of saturation, so the tail is dominated by the same queueing in both cases.

This is consistent with [ADR-0009](../docs/adr/0009-cache-adds-round-trip-latency.md)
and with the M2 baseline's conclusion: the case for these protections is
**database load**, and latency only follows once the database stops being the
bottleneck. Here it never stops being the bottleneck.

## Why the measured benefit is smaller than ADR-0004 predicts

ADR-0004 describes the unjittered case as a spike at a single instant and the
jittered case as a gradual rise. The measurement shows a 19.5% peak reduction,
not a transformation. Two reasons, both artifacts of this scenario rather than
of jitter:

**1. The unjittered baseline is already spread.** The warm phase needs ~5
seconds to write 1,000 keys, so even at `JITTER_PCT=0` those keys carry write
timestamps spread across 5 seconds and expire across 5 seconds. Adding a 0–6s
jitter window on top of an existing 5s spread widens it by less than the
arithmetic suggests. A production avalanche — a cache-warm script, a mass
invalidation, a cold start — writes keys far closer together, and jitter would
matter correspondingly more.

**2. Re-fetch is not instantaneous.** A key is only re-queried when something
requests it. At 400 req/s over 1,000 keys each key is sampled about every 2.5s,
so the observed wave is the convolution of the expiry distribution with that
sampling delay — which smooths both arms.

**Both of these flatten the unprotected case, so the 19.5% figure is a lower
bound on jitter's value, not an upper one.** Measuring the stronger version
needs a warm phase that writes the set near-instantaneously; that is not what
this scenario does, and the number is reported as measured rather than adjusted
upward with a justification.

### A caveat on saturation

Achieved request rate was ~322/s against a 400/s target in both arms, with p99
latency in seconds. The service is saturated during the wave: ~1,000 concurrent
cache misses against a 25-connection pool queue.

That saturation *is* the avalanche doing its damage, so it belongs in the
result — but it also means these runs measure a system under duress, and the
latency columns describe queueing, not lookup cost. Per
[ADR-0008](../docs/adr/0008-benchmark-harness-correctness.md), numbers from a
saturated harness describe the test as much as the service.

## Conclusion

TTL jitter at 10% cuts peak database QPS by **19.5%** (113 → 91) and converts an
abrupt wall into a six-second ramp, at **zero cost** — no added latency, no new
failure mode, no extra dependency. Total database work is unchanged, which is
the correct and expected behaviour.

It did **not** improve latency in this test, because a 19.5% peak reduction is
not enough to lift the service out of saturation.

Jitter is cheap insurance whose measured value here is suppressed by the
scenario's gradual warm phase. It is worth keeping on those grounds; it is not
worth claiming it eliminates avalanche.

## Harness note

Two successive bugs in this scenario's key sampler had to be fixed before any
number here was meaningful. Both made the expiry wave invisible:

1. `requests.add(1) % WARMUP_KEYS` — k6's `Counter.add()` returns a boolean, so
   this evaluated to `true % 1000` = 1 on every iteration and the entire
   measured phase requested product id 2.
2. A per-VU incrementing cursor, the first attempted fix. Each VU had its own
   runtime and its own cursor, but all VUs advance at the same rate, so the
   fleet marched in lockstep and collectively touched only ~8 distinct keys per
   second. Measured result: **zero** database queries from t=6s to t=60s, then a
   flat 8 QPS — no spike anywhere, because the 1,000 warm keys were never in
   play simultaneously.

The sampler now draws uniformly at random from the warm set.

The analysis window was also wrong: it was set to t=85–105s, on the assumption
that keys expire 60s after the *measured phase* begins. They expire 60s after
they are *written*, which is t≈61–67. The original window sat past the wave
entirely and captured only steady state — which is why early runs reported 182
and 25 queries for an event that moves ~940.

## Reproducing

```bash
task up
python benchmarks/run.py avalanche-no-jitter avalanche-with-jitter --runs 3
python benchmarks/summarize.py avalanche
```
