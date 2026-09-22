# 0008. The benchmark harness is part of the system under test

**Status:** Accepted
**Date:** 2026-09-21

## Context

The first M2 baseline run reported:

| | |
|---|---|
| Achieved throughput | **15.7 req/s** against a target of 400 |
| p99 latency | **1.38 s** |
| Dropped iterations | **34,311** |
| `iteration_duration` avg | **14.5 s** |

Read naively, that says the service collapses under load and the whole design
needs rethinking.

It was entirely false. Three probes established it:

1. **Sequential, no k6** — 200 requests over one connection: median **2.56 ms**,
   p99 **3.89 ms**.
2. **Same k6 script, `DIST=uniform`** — **300.0 req/s**, exactly the target,
   p99 3.93 ms, zero drops.
3. `iteration_duration` averaged 14.5 s while no HTTP request ever exceeded
   1.55 s. The time was being spent *outside* the request.

The cause was the Zipf sampler in the harness. It built an exact CDF with one
entry per key — 1,000,000 `Math.pow` calls — **lazily, on first use, inside the
measured iteration**. k6 gives every VU an isolated JavaScript runtime, so that
ran once per VU, 600 times, in an interpreted engine, inside the measurement.

**The benchmark measured the benchmark.**

## Decision

Two changes, both necessary:

1. **Bucketed CDF.** 4096 buckets instead of 1,000,000 entries. A bucket is
   chosen by inverse-CDF binary search, then a key uniformly within it. Init
   drops from ~1e6 operations to ~4e3.
2. **Initialise at module scope.** Each scenario calls `initZipf()` at the top
   level, which k6 executes in its init context — excluded from iteration
   timing by construction, so setup cost can never contaminate a measurement
   again.

The approximation is stated rather than hidden: keys within one bucket are
equally likely, so with 1M keys the ~244 hottest keys share the top rank
instead of one key dominating. That is fine for hit-rate and database-load
work. The stampede scenario at M6 needs a single genuinely hot key and will use
a dedicated generator.

## Consequences

**Verified.** The same scenario now sustains 398 req/s at p99 5.91 ms with 168
dropped iterations, against 15.7 req/s and 34,311 drops before.

**A rule, adopted for every scenario from here on:** before believing any
regression, check `iteration_duration` against `http_req_duration`. If the
former substantially exceeds the latter, the harness is the bottleneck and the
numbers describe the test, not the service.

**Why this matters beyond one bug.** Published benchmark numbers are a primary
deliverable of this project. A harness that is wrong in a plausible direction
is more dangerous than one that is obviously broken — these numbers would have
been believed, and would have prompted redesigning a service that was already
meeting its targets.
