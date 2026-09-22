# Benchmarks

Numbers without a stated methodology are worthless, so the methodology is
fixed here before any number is produced.

## Method

Every run, without exception:

- the same instance size and the same worker count, both recorded in the result
- the same seeded dataset (1,000,000 products)
- 30 seconds of warm-up, discarded
- 3 runs, median reported
- **effective settings read back from `/v1/admin/stats`**, never the settings
  the operator believed they set (see ADR-0002)
- two key distributions, reported separately:
  - **Zipfian** -- realistic; a few keys are very hot
  - **Uniform** -- worst case for a cache; every key equally likely

## Layout

```
scenarios/    k6 scripts, one per failure mode
results/      raw JSON output, committed
graphs/       exported Grafana panels, committed
```

## Before believing any result

Check `iteration_duration` against `http_req_duration`. If the former
substantially exceeds the latter, **the harness is the bottleneck** and the
numbers describe the test, not the service. This rule exists because the first
baseline run reported 15.7 req/s against a service that comfortably sustains
400 — see [ADR-0008](../docs/adr/0008-benchmark-harness-correctness.md).

Corollaries, learned the same way:

- Any setup work in a k6 scenario belongs at **module scope**, not inside an
  iteration. k6 gives every VU its own JavaScript runtime, so lazy
  initialisation runs once per VU and lands in the measurement.
- Investigate small anomalies. Fifteen unexplained 404s in 36,000 requests
  looked like noise and was a real bug in the key sampler.
- Confirm `/metrics` aggregates across workers before trusting it
  ([ADR-0007](../docs/adr/0007-metrics-under-multiple-workers.md)).

## Reporting

Each failure mode produces one table and one graph:

| Metric | Before | After |
|---|---|---|
| Cache hit rate | | |
| **Database QPS** | | |
| p50 / p95 / p99 latency | | |
| Error rate | | |

Database QPS is the headline. Every protection in this service exists to move
that number, and latency improvements are a side effect of it.
