// Cache penetration attack scenario.
//
// Fires requests for IDs that do NOT exist in Postgres. Without protection,
// every request reaches the database because the cache never holds a negative
// result. With the Bloom filter, those requests are rejected before Redis or
// Postgres is touched.
//
// This is M4's headline demonstration: the "before" run shows DB QPS = request
// rate (100%), and the "after" run shows DB QPS ≈ 1% (only false positives).
//
// Usage:
//   # Without Bloom filter (penetration succeeds):
//   k6 run -e SCENARIO=penetration-unprotected benchmarks/scenarios/penetration.js
//
//   # With Bloom filter enabled (penetration blocked):
//   k6 run -e SCENARIO=penetration-protected benchmarks/scenarios/penetration.js
//
// Env:
//   BASE_URL  default http://localhost:8010
//   SCENARIO  result label, default 'penetration'
//   DATASET   number of seeded products (IDs will be > DATASET), default 1000000
//   RATE      target requests/second, default 400
//   DURATION  measured window after warm-up, default 60s

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { absentKey } from '../lib/keys.js';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8010';
const DATASET = parseInt(__ENV.DATASET || '1000000', 10);
const RATE = parseInt(__ENV.RATE || '400', 10);
const DURATION = __ENV.DURATION || '60s';

const requests = new Counter('penetration_requests');
const blocked = new Counter('penetration_blocked');
const readLatency = new Trend('penetration_read_ms', true);

export const options = {
  scenarios: {
    warmup: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: '10s',
      preAllocatedVUs: 50,
      maxVUs: 300,
      tags: { phase: 'warmup' },
      exec: 'attack',
    },
    measured: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      startTime: '10s',
      preAllocatedVUs: 50,
      maxVUs: 300,
      tags: { phase: 'measured' },
      exec: 'attack',
    },
  },
  thresholds: {
    // All requests MUST 404 -- that is the point of the scenario. Any 200
    // means we asked for an ID that actually exists, which invalidates the test.
    'http_req_failed{phase:measured}': ['rate==0'],
    'http_req_status_code{phase:measured}': ['rate==404'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function attack() {
  // Generate an ID outside the seeded range. These IDs definitely do not exist
  // in Postgres, so every one is a penetration attempt.
  const id = absentKey(DATASET);
  const res = http.get(`${BASE_URL}/v1/products/${id}`, {
    tags: { name: 'GET /v1/products/:id (absent)' },
  });

  requests.add(1);
  readLatency.add(res.timings.duration);

  // All responses MUST be 404. If any are 200, the scenario is misconfigured
  // (DATASET is wrong, or the service has more data than we think).
  check(res, {
    'status is 404 (penetration attempt)': (r) => r.status === 404,
  });

  // Bloom filter rejects will be fast. Track them separately.
  if (res.timings.duration < 2.0) {
    blocked.add(1);
  }
}

export function handleSummary(data) {
  const run = __ENV.RUN || '1';
  const scenario = __ENV.SCENARIO || 'penetration';
  const m = data.metrics;

  const totalRequests = m.penetration_requests ? m.penetration_requests.values.count : 0;
  const blockedCount = m.penetration_blocked ? m.penetration_blocked.values.count : 0;
  const blockedPct = totalRequests > 0 ? ((blockedCount / totalRequests) * 100).toFixed(1) : 0;

  const out = {
    scenario: scenario,
    run: Number(run),
    recorded_at: new Date().toISOString(),
    config: { base_url: BASE_URL, dataset: DATASET, rate: RATE, duration: DURATION },
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
      penetration: {
        total_attempts: totalRequests,
        blocked_by_bloom: blockedCount,
        blocked_pct: Number(blockedPct),
        reached_db_pct: Number((100 - blockedPct).toFixed(1)),
      },
    },
  };

  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [`benchmarks/results/${scenario}-run${run}.json`]: JSON.stringify(out, null, 2) + '\n',
  };
}
