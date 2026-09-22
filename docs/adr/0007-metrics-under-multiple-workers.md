# 0007. Metrics must aggregate across gunicorn workers

**Status:** Accepted
**Date:** 2026-09-21

## Context

Found during the M2 baseline run, not by reasoning about it beforehand.

`prometheus_client` keeps counters in **process memory**. The service runs
under gunicorn with 4 uvicorn workers, so there were 4 independent registries,
and `/metrics` was answered by whichever worker the scrape happened to reach.

Eight consecutive scrapes during a benchmark returned:

```
688  688  346  688  688  216  688  346
```

A counter cannot decrease. Three different workers were answering, each
reporting only what it had personally seen.

The consequence was worse than a wrong number. `database_queries_total` is the
**headline metric of this entire project** — every protection added at M3 to
M7 exists to make it smaller. Reading roughly a quarter of the truth, sampled
from an arbitrary worker, would have made every before/after comparison
meaningless while still producing numbers that looked plausible.

## Decision

Use `prometheus_client`'s multiprocess mode.

- `PROMETHEUS_MULTIPROC_DIR` is set in `docker/gunicorn.conf.py` at module
  scope — in the master, before any worker is forked and before the app
  imports `prometheus_client`.
- Each scrape builds a **fresh** `CollectorRegistry` populated by
  `MultiProcessCollector`, which reads every live worker's memory-mapped files
  and aggregates them. Rendering the module-level registry would reproduce the
  original bug.
- `on_starting` wipes the directory: stale files belong to dead PIDs from a
  previous run and would inflate every counter at boot.
- `child_exit` calls `mark_process_dead(worker.pid)`: a worker recycled by
  `max_requests` would otherwise have its counters double-counted against its
  replacement's.
- Gauges declare `multiprocess_mode="livesum"`. Counters aggregate by summing
  automatically; gauges do not, and an unspecified mode reports one arbitrary
  worker's value. `livesum` is also the semantically correct choice here:
  with N workers there genuinely *are* N Bloom filters and N coalescer maps,
  and the sum is what the service is really holding.
- Single-process runs (uvicorn in development, and tests) keep the ordinary
  in-memory registry, which is both correct and simpler there.

## Alternatives considered

**Run a single worker.** Removes the problem by removing the concurrency. But
the per-worker nature of the Bloom filter and the coalescer is the most
interesting fact in this project, and failure mode 3 exists to measure it.
Rejected.

**Push metrics to a gateway instead of being scraped.** Works, but adds a
component to run and obscures the per-worker reality rather than aggregating
it honestly. Rejected as disproportionate.

**Track the counter in Redis.** Would aggregate correctly, but puts a network
round trip on the hot path of the very metric measuring hot-path behaviour.
Rejected.

## Consequences

**Verified.** After the fix, 200 requests produced `200.0` on eight consecutive
scrapes; 50 more produced exactly `250.0`.

**Cost.** Histogram quantiles are unavailable in multiprocess mode — only
`_sum` and `_count` aggregate. Latency percentiles therefore come from k6 and
from the traces, not from `/metrics`. Acceptable: the service-side histogram
was never the source of the published p99.

**Generalises.** The Scheduler and the Vault inherit this skeleton and the same
constraint. This ADR travels with it.
