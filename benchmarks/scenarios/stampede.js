// Cache stampede scenario.
//
// Demonstrates what happens when one hot key expires under concurrent load. The
// scenario warms a single key, waits for it to expire, then fires hundreds of
// concurrent requests for that same key. Without coalescing, every request issues
// a duplicate DB query. With coalescing: 1 query per worker (still up to 4
// queries with 4 workers, not 1 — this is the per-worker limitation).
//
// This is M6's headline demonstration: the "before" run (coalescing disabled)
// shows N duplicate queries = N concurrent requests, and the "after" run
// (coalescing enabled) shows up to 4 queries (one per worker), regardless of
// concurrency level.
//
// Usage:
//   # Without coalescing (every request hits DB):
//   COALESCE_ENABLED=false
//   k6 run -e SCENARIO=stampede-no-coalesce -e HOT_KEY=42 benchmarks/scenarios/stampede.js
//
//   # With coalescing (1 query per worker):
//   COALESCE_ENABLED=true
//   k6 run -e SCENARIO=stampede-with-coalesce -e HOT_KEY=42 benchmarks/scenarios/stampede.js
//
// Env:
//   BASE_URL    default http://localhost:8010
//   SCENARIO    result label, default 'stampede'
//   HOT_KEY     the product ID to target, default 42
//   TTL         cache TTL (must match service config), default 60
//   RATE        sustained background load (RPS), default 50
//   BURST_VUS   number of VUs for the stampede burst, default 500

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8010';
const HOT_KEY = parseInt(__ENV.HOT_KEY || '42', 10);
const TTL = parseInt(__ENV.TTL || '60', 10);
const RATE = parseInt(__ENV.RATE || '50', 10);
const BURST_VUS = parseInt(__ENV.BURST_VUS || '500', 10);

const requests = new Counter('stampede_requests');
const readLatency = new Trend('stampede_read_ms', true);

export const options = {
  scenarios: {
    // Phase 1: Warm the hot key. Fire one request to populate the cache.
    warmup: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 1,
      tags: { phase: 'warmup' },
      exec: 'warm',
    },
    // Phase 2: Hold light background load for longer than TTL. This keeps the
    // service alive and allows us to observe the exact moment the key expires.
    background: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: `${TTL + 30}s`,
      startTime: '5s',
      preAllocatedVUs: 10,
      maxVUs: 20,
      tags: { phase: 'background' },
      exec: 'backgroundLoad',
    },
    // Phase 3: The stampede. At t=TTL+5s (after the hot key has expired), fire
    // BURST_VUS concurrent requests for the expired key. Without coalescing,
    // this produces BURST_VUS duplicate queries. With coalescing: up to 4
    // queries (one per worker).
    stampede: {
      executor: 'per-vu-iterations',
      vus: BURST_VUS,
      iterations: 1,
      startTime: `${TTL + 5}s`,
      maxDuration: '10s',
      tags: { phase: 'stampede' },
      exec: 'stampede',
    },
  },
  thresholds: {
    // No hard thresholds — the duplicate query count is what we're measuring,
    // and that's extracted from the DB's metrics, not from k6's counters.
    'http_req_failed{phase:stampede}': ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function warm() {
  // Warm the hot key so it's in the cache with a fresh TTL
  http.get(`${BASE_URL}/v1/products/${HOT_KEY}`, {
    tags: { name: 'GET /v1/products/:id (warm)' },
  });
}

export function backgroundLoad() {
  // Light background traffic on random keys to keep the service alive
  const randomId = Math.floor(Math.random() * 1000) + 1;
  http.get(`${BASE_URL}/v1/products/${randomId}`, {
    tags: { name: 'GET /v1/products/:id (background)' },
  });
}

export function stampede() {
  // Fire one request for the hot key. This is timed to happen immediately after
  // the key expires, so with BURST_VUS concurrent requests, we expect:
  // - Without coalescing: BURST_VUS DB queries
  // - With coalescing: up to 4 DB queries (one per worker)
  requests.add(1);

  const res = http.get(`${BASE_URL}/v1/products/${HOT_KEY}`, {
    tags: { name: 'GET /v1/products/:id (stampede)' },
  });

  readLatency.add(res.timings.duration);

  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}

export function handleSummary(data) {
  const run = __ENV.RUN || '1';
  const scenario = __ENV.SCENARIO || 'stampede';
  const m = data.metrics;

  const stampedeRequests = m.stampede_requests ? m.stampede_requests.values.count : 0;

  const out = {
    scenario: scenario,
    run: Number(run),
    recorded_at: new Date().toISOString(),
    config: {
      base_url: BASE_URL,
      hot_key: HOT_KEY,
      ttl: TTL,
      background_rate: RATE,
      burst_vus: BURST_VUS,
    },
    results: {
      stampede_requests: stampedeRequests,
      total_requests: m.http_reqs ? m.http_reqs.values.count : null,
      rps: m.http_reqs ? Number(m.http_reqs.values.rate.toFixed(1)) : null,
      error_rate: m.http_req_failed ? m.http_req_failed.values.rate : null,
      latency_ms: m.http_req_duration ? {
        avg: Number(m.http_req_duration.values.avg.toFixed(2)),
        med: Number(m.http_req_duration.values.med.toFixed(2)),
        p95: Number(m.http_req_duration.values['p(95)'].toFixed(2)),
        p99: Number(m.http_req_duration.values['p(99)'].toFixed(2)),
        max: Number(m.http_req_duration.values.max.toFixed(2)),
      } : null,
    },
    note: 'The key metric is DB query count during the stampede phase, extracted from Postgres/Prometheus metrics, not from k6.',
  };

  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [`benchmarks/results/${scenario}-run${run}.json`]: JSON.stringify(out, null, 2) + '\n',
  };
}
