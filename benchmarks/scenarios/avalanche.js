// Cache avalanche scenario.
//
// Demonstrates what happens when many keys expire simultaneously. The scenario
// warms a set of hot keys with identical TTLs, then holds steady load across
// the expiry boundary. Without jitter, all warm keys expire in the same second
// and the database takes the full load at once -- a sharp spike. With jitter,
// expiry is spread over the jitter window and the spike is smoothed.
//
// This is M5's headline demonstration: the "before" run (jitter disabled) shows
// a sharp DB QPS spike at t=TTL, and the "after" run (jitter enabled) shows a
// gradual rise instead.
//
// Usage:
//   # Without jitter (synchronized expiry):
//   CACHE_TTL_JITTER_PCT=0
//   k6 run -e SCENARIO=avalanche-no-jitter -e WARMUP_KEYS=1000 benchmarks/scenarios/avalanche.js
//
//   # With jitter (spread expiry):
//   CACHE_TTL_JITTER_PCT=10
//   k6 run -e SCENARIO=avalanche-with-jitter -e WARMUP_KEYS=1000 benchmarks/scenarios/avalanche.js
//
// Env:
//   BASE_URL      default http://localhost:8010
//   SCENARIO      result label, default 'avalanche'
//   WARMUP_KEYS   how many keys to warm with synchronized TTL, default 1000
//   TTL_SECONDS   cache TTL (must match service config), default 60
//   RATE          target requests/second during measured phase, default 400
//   HOLD_DURATION how long to hold load after warm-up, default 90s (must exceed TTL)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8010';
const WARMUP_KEYS = parseInt(__ENV.WARMUP_KEYS || '1000', 10);
const TTL_SECONDS = parseInt(__ENV.TTL_SECONDS || '60', 10);
const RATE = parseInt(__ENV.RATE || '400', 10);
const HOLD_DURATION = __ENV.HOLD_DURATION || '90s';

// Pick a contiguous range of keys for warming. Using 1-WARMUP_KEYS so they're
// guaranteed to exist in the seeded dataset.
const WARM_START = 1;
const WARM_END = WARMUP_KEYS;

const requests = new Counter('avalanche_requests');
const cache_hits = new Counter('avalanche_cache_hits');
const cache_misses = new Counter('avalanche_cache_misses');
const readLatency = new Trend('avalanche_read_ms', true);

export const options = {
  scenarios: {
    // Phase 1: Warm WARMUP_KEYS keys as fast as possible so they all get
    // approximately the same TTL. This phase is NOT measured -- it's setup.
    warmup: {
      executor: 'per-vu-iterations',
      vus: 50,
      iterations: Math.ceil(WARMUP_KEYS / 50),
      maxDuration: '30s',
      tags: { phase: 'warmup' },
      exec: 'warm',
    },
    // Phase 2: Hold steady load for longer than the TTL. The interesting window
    // is around t=TTL_SECONDS, when the warm keys start expiring. With zero
    // jitter they all expire in the same second (spike). With jitter they're
    // spread over jitter_pct * TTL (smoothed).
    measured: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: HOLD_DURATION,
      startTime: '30s',
      preAllocatedVUs: 50,
      maxVUs: 300,
      tags: { phase: 'measured' },
      exec: 'read',
    },
  },
  thresholds: {
    // No hard thresholds here -- the spike itself is what we're demonstrating,
    // not a pass/fail criterion. The before/after comparison happens in the
    // results analysis, not in the scenario.
    'http_req_failed{phase:measured}': ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

// Warm phase: each VU warms a slice of the keyspace as fast as it can, so all
// warm keys get approximately the same write timestamp (and therefore the same
// expiry instant, modulo jitter).
export function warm() {
  // Divide WARMUP_KEYS evenly among VUs. __VU is 1-indexed, __ITER is 0-indexed.
  const vus = 50;
  const keysPerVu = Math.ceil(WARMUP_KEYS / vus);
  const start = WARM_START + (__VU - 1) * keysPerVu;
  const end = Math.min(start + keysPerVu, WARM_END);

  for (let id = start; id < end; id++) {
    http.get(`${BASE_URL}/v1/products/${id}`, {
      tags: { name: 'GET /v1/products/:id (warm)' },
    });
  }
}

// Measured phase: request keys from the warm set. As keys expire, they become
// cache misses and hit the database. The spike timing and shape depend on jitter.
export function read() {
  // Round-robin through the warm set so every key gets roughly equal traffic.
  const offset = requests.add(1) % WARMUP_KEYS;
  const id = WARM_START + offset;

  const res = http.get(`${BASE_URL}/v1/products/${id}`, {
    tags: { name: 'GET /v1/products/:id' },
  });

  readLatency.add(res.timings.duration);

  // Heuristic: a response faster than 5ms is almost certainly a cache hit
  // (Bloom + Redis). Slower is likely a DB query. This is imperfect but good
  // enough to visualize the expiry wave in the results.
  if (res.timings.duration < 5.0) {
    cache_hits.add(1);
  } else {
    cache_misses.add(1);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}

export function handleSummary(data) {
  const run = __ENV.RUN || '1';
  const scenario = __ENV.SCENARIO || 'avalanche';
  const m = data.metrics;

  const totalRequests = m.avalanche_requests ? m.avalanche_requests.values.count : 0;
  const hits = m.avalanche_cache_hits ? m.avalanche_cache_hits.values.count : 0;
  const misses = m.avalanche_cache_misses ? m.avalanche_cache_misses.values.count : 0;
  const hitRate = totalRequests > 0 ? ((hits / totalRequests) * 100).toFixed(1) : 0;

  const out = {
    scenario: scenario,
    run: Number(run),
    recorded_at: new Date().toISOString(),
    config: {
      base_url: BASE_URL,
      warmup_keys: WARMUP_KEYS,
      ttl_seconds: TTL_SECONDS,
      rate: RATE,
      hold_duration: HOLD_DURATION,
    },
    results: {
      requests: totalRequests,
      rps: m.http_reqs ? Number(m.http_reqs.values.rate.toFixed(1)) : null,
      error_rate: m.http_req_failed ? m.http_req_failed.values.rate : null,
      latency_ms: m.http_req_duration ? {
        avg: Number(m.http_req_duration.values.avg.toFixed(2)),
        med: Number(m.http_req_duration.values.med.toFixed(2)),
        p95: Number(m.http_req_duration.values['p(95)'].toFixed(2)),
        p99: Number(m.http_req_duration.values['p(99)'].toFixed(2)),
        max: Number(m.http_req_duration.values.max.toFixed(2)),
      } : null,
      cache: {
        hits: hits,
        misses: misses,
        hit_rate_pct: Number(hitRate),
      },
    },
  };

  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [`benchmarks/results/${scenario}-run${run}.json`]: JSON.stringify(out, null, 2) + '\n',
  };
}
