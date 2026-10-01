# 0009. Cache-aside made tail latency worse, not better

**Status:** Accepted
**Date:** 2026-09-22

## Context

M2's baseline already established that the uncached service meets its latency
targets — a primary-key lookup on a warm table is fast, and the honest case
for a cache here is database load, not latency (see `benchmarks/BASELINE.md`).
M3 measured what actually happens when a cache-aside layer is added, rather
than assuming it.

Three runs each, identical methodology to M2, against the same 400 req/s,
1M-row dataset:

| | p99 (M2, no cache) | p99 (M3, cache-aside) |
|---|---|---|
| zipf | 6.73 ms | **12.45 ms** |
| uniform | 4.57 ms | **15.63 ms** |

Median moved less (2.53 ms → ~3.4-3.9 ms), consistent with one extra local
Redis round trip on every request. Tail latency roughly doubled to tripled.

Database query reduction tracked the cache hit rate exactly, as it must — a
hit costs zero queries, a miss costs exactly one:

| | hit rate | DB queries / requests |
|---|---|---|
| zipf (skew 1.1) | 44.2% (median) | ~20,000 / ~35,900 |
| uniform | 2.5% (median) | ~35,100 / ~36,000 |

## Why the hit rate is lower than the earlier "top 1% = 59%" figure suggested

That figure (recorded in ADR-0008 while validating the key sampler) describes
the *share of total request volume* landing on the top 1% of keys, not how
often any single one of those keys repeats within one TTL window. At 400
req/s spread across a ~90 second run, the "hot" 1% is still 10,000 distinct
keys, each seeing on the order of one request per 40 seconds on average —
barely more than once per TTL window, which is not enough repetition to turn
most of that traffic into hits.

**Verified rather than assumed:** a control run with the TTL raised to 6000s
(effectively never expiring within the run) produced a nearly identical hit
rate (39.1%) to the 60s-TTL runs (40.4-44.6%). TTL length is not the limiting
factor here — the limiting factor is how many total requests land in the run
window relative to the size of the effectively-hot key pool. A longer
benchmark window, or a higher request rate against the same keyspace, would
raise the hit rate; the 60s TTL chosen for this run does not meaningfully
constrain it at this rate and duration.

## Decision

Report both numbers plainly rather than only the flattering one. Cache-aside
at M3:

- **Reduces database load** on a skewed access pattern, roughly in proportion
  to the hit rate — the intended effect, and real.
- **Does nothing useful, and costs something, on a uniform access pattern** —
  a 2.5% query reduction bought with roughly tripled p99 latency. This is a
  legitimate result to publish, not a bug to hide: a uniformly-accessed 1M-row
  keyspace is close to the worst case for any cache, cache-aside included, and
  a portfolio project that only shows the flattering workload is not
  demonstrating engineering judgment, just marketing.
- **The per-request Redis round trip is a real, measured cost**, paid by every
  request including hits. It is not investigated further here — the *cause*
  (network hop vs. connection-pool contention vs. something else) is a
  separate question from the *fact*, and the fact is what this milestone's
  benchmark exists to establish.

No code changes follow from this ADR. It exists so the M3 numbers are read
correctly: the win is database load, exactly as M2 predicted, and the cost is
real and worth knowing before M4-M7 add more machinery on top of this path.

## Alternatives considered

**Re-run with a longer window or higher rate to chase a flattering hit-rate
number before publishing.** Rejected — the point of a benchmark methodology
fixed in ADR-0008 is that it does not get adjusted after seeing an unflattering
result. 400 req/s for 60 measured seconds is the standing methodology; this
result is what it produces, and the explanation belongs in the write-up, not
in retuning the test until the number improves.

**Investigate the tail-latency cause now, before publishing M3.** Deferred.
Diagnosing it properly needs profiling that is out of scope for a milestone
whose job is to measure the cache-aside baseline, not to optimise it. It is
recorded here so it is not forgotten, and it is a natural candidate for
attention if a future milestone's numbers look worse than they should.

## Consequences

**Sets the bar for M4 onward.** Bloom filters, coalescing, and locking each
add their own machinery on this same request path. Each one's cost, not just
its benefit, needs measuring the same way this ADR measured the base cache's
cost — the pattern established here should repeat.
