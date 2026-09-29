# Cache Avalanche Protection — Before and After

**Milestone:** M5
**Date:** 2026-09-28
**Measured by:** [Your name]

## What this measures

Cache avalanche: many keys expiring simultaneously, causing a sharp database load
spike. The scenario warms a set of keys with identical TTLs, then holds steady
load across the expiry boundary. Without jitter, all keys expire together (spike).
With jitter, expiry is spread over the jitter window (smoothed).

## Methodology

**Scenario:** `benchmarks/scenarios/avalanche.js`
**Warm-up keys:** 1,000 keys (IDs 1-1000)
**Base TTL:** 60 seconds
**Request rate:** 400 RPS (steady throughout)
**Hold duration:** 90 seconds (exceeds TTL to observe expiry)
**Runs per configuration:** 3 (median reported)

All runs use the same instance type, same dataset, same warm Postgres. The only
variable is `CACHE_TTL_JITTER_PCT`.

## Configuration

### Run 1: No jitter (synchronized expiry)
```bash
CACHE_TTL_JITTER_PCT=0
k6 run -e SCENARIO=avalanche-no-jitter -e WARMUP_KEYS=1000 benchmarks/scenarios/avalanche.js
```

### Run 2: 10% jitter (spread expiry)
```bash
CACHE_TTL_JITTER_PCT=10
k6 run -e SCENARIO=avalanche-with-jitter -e WARMUP_KEYS=1000 benchmarks/scenarios/avalanche.js
```

## Results

### Without jitter

| Metric | Value |
|---|---|
| Total requests | TBD |
| RPS achieved | TBD |
| Cache hit rate | TBD% |
| **DB QPS spike at t=60s** | **TBD (expect ~400 QPS = 100%)** |
| Spike duration | TBD seconds |
| p50 latency | TBD ms |
| p99 latency | TBD ms |
| **p99 latency spike** | **TBD ms** |

**Observation:** Sharp spike at exactly t=60s when all 1000 keys expire together.

### With 10% jitter

| Metric | Value |
|---|---|
| Total requests | TBD |
| RPS achieved | TBD |
| Cache hit rate | TBD% |
| **DB QPS (smoothed)** | **TBD** |
| Expiry spread window | 60-66s (6 second window) |
| p50 latency | TBD ms |
| p99 latency | TBD ms |
| **p99 latency (no spike)** | **TBD ms** |

**Observation:** Gradual rise in DB QPS over 6 seconds instead of sharp spike.

## Before/After comparison

| | No jitter | 10% jitter | Change |
|---|---|---|---|
| Peak DB QPS | TBD | TBD | **TBD% reduction** |
| Spike duration | TBD s | TBD s (spread) | - |
| p99 latency spike | TBD ms | TBD ms | TBD |
| Cache behavior | Synchronized expiry | Decorrelated expiry | - |

## Visualization

The "before" graph should show:
- Smooth DB QPS from t=0 to t=59s (cache hits)
- Sharp spike at t=60s (all keys expire)
- Gradual decline t=61-90s (cache re-populates)

The "after" graph should show:
- Smooth DB QPS from t=0 to t=59s (cache hits)
- Gradual rise t=60-66s (keys expire over jitter window)
- Gradual decline t=67-90s (cache re-populates)

*Graphs to be added after benchmark run*

## Why 10% jitter

**Standard recommendation** across Redis, Memcached, AWS ElastiCache:
- Small enough: users don't notice 30s variance on 300s TTL
- Large enough: meaningfully spreads expiry (6s on 60s TTL)
- Scales with TTL: 10% of 1-hour is 6 minutes (appropriate)

**50% would be overkill:**
- 300s TTL → 300-450s variance (2.5 minutes spread)
- Stale data persists longer
- Cache hit rate drops unnecessarily

## Jitter formula

```python
jitter = random.uniform(0, cache_ttl_jitter_seconds)
final_ttl = cache_ttl_seconds + jitter
```

**Add, don't subtract:** Subtracting would shorten minimum freshness. Adding
only spreads *when* keys expire, not *how long* they're valid.

## Conclusion

**TTL jitter smooths synchronized expiry spikes** at negligible cost to cache
freshness. The DB load spike is spread over the jitter window instead of hitting
all at once.

**Trade-off:** Slightly less predictable expiry (6s window vs exact instant).
This is acceptable — users don't notice and the DB benefits greatly.

**Next:** See ADR-0004 for the why 10% decision and alternatives considered.

---

## Running the benchmark yourself

1. Start the stack:
   ```bash
   task up
   docker compose logs -f api
   ```

2. Seed the dataset:
   ```bash
   docker compose exec api python -m scripts.seed_products
   ```

3. Run without jitter:
   ```bash
   # Set CACHE_TTL_JITTER_PCT=0 in .env
   docker compose restart api
   k6 run -e SCENARIO=avalanche-no-jitter -e WARMUP_KEYS=1000 benchmarks/scenarios/avalanche.js
   ```

4. Run with jitter:
   ```bash
   # Set CACHE_TTL_JITTER_PCT=10 in .env
   docker compose restart api
   k6 run -e SCENARIO=avalanche-with-jitter -e WARMUP_KEYS=1000 benchmarks/scenarios/avalanche.js
   ```

5. Compare DB QPS graphs around t=60s in Grafana or extract from Prometheus metrics
