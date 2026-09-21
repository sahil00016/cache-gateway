// M2 baseline: reads served straight from Postgres, with no cache at all.
//
// This run is the control. Every protection added at M3 to M7 is measured as a
// delta against these numbers, so the methodology here is fixed and must not
// change later -- a "before" and an "after" measured differently prove nothing.
//
//   k6 run -e DIST=zipf benchmarks/scenarios/baseline.js
//
// Env:
//   BASE_URL  default http://localhost:8010
//   DIST      zipf (default) | uniform | absent
//   DATASET   number of seeded products, default 1000000
//   SKEW      Zipf exponent, default 1.1
//   RATE      target requests/second, default 400
//   DURATION  measured window after warm-up, default 60s

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { initZipf, pickKey } from '../lib/keys.js';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8010';
const DIST = __ENV.DIST || 'zipf';
const DATASET = parseInt(__ENV.DATASET || '1000000', 10);
const SKEW = parseFloat(__ENV.SKEW || '1.1');
const RATE = parseInt(__ENV.RATE || '400', 10);
const DURATION = __ENV.DURATION || '60s';

// Module scope on purpose. k6 runs this in its init context, once per VU,
// OUTSIDE iteration timing -- see the note at the top of lib/keys.js for the
// benchmark this mistake invalidated.
initZipf(DATASET, SKEW);

const found = new Counter('product_found');
const missing = new Counter('product_missing');
const readLatency = new Trend('product_read_ms', true);

export const options = {
  scenarios: {
    // Warm-up is a separate scenario rather than a sleep so its requests are
    // tagged and can be excluded. Connection setup and Postgres plan caching
    // both distort the first seconds, and leaving them in inflates p99.
    warmup: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: '30s',
      preAllocatedVUs: 50,
      maxVUs: 300,
      tags: { phase: 'warmup' },
      exec: 'read',
    },
    measured: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      startTime: '30s',
      preAllocatedVUs: 50,
      maxVUs: 300,
      tags: { phase: 'measured' },
      exec: 'read',
    },
  },
  thresholds: {
    // Targets from the design document, asserted rather than eyeballed.
    // The baseline is EXPECTED to fail the cache-hit threshold -- there is no
    // cache yet. A failing threshold here is the problem being documented.
    'http_req_failed{phase:measured}': ['rate<0.01'],
    'product_read_ms{phase:measured}': ['p(99)<50'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function read() {
  const id = pickKey(DIST, DATASET);
  const res = http.get(`${BASE_URL}/v1/products/${id}`, {
    tags: { name: 'GET /v1/products/:id' },
  });

  readLatency.add(res.timings.duration);
  if (res.status === 200) found.add(1);
  else if (res.status === 404) missing.add(1);

  check(res, {
    'status is 200 or 404': (r) => r.status === 200 || r.status === 404,
  });
}

// Results are written to disk, not just printed. A benchmark that only exists
// in a terminal cannot be compared against the next run, which is the entire
// point of taking a baseline.
export function handleSummary(data) {
  const run = __ENV.RUN || '1';
  const label = `baseline-${DIST}`;
  const m = data.metrics;
  const out = {
    scenario: label,
    run: Number(run),
    recorded_at: new Date().toISOString(),
    config: { base_url: BASE_URL, distribution: DIST, dataset: DATASET, skew: SKEW, rate: RATE, duration: DURATION },
    results: {
      requests: m.http_reqs ? m.http_reqs.values.count : null,
      rps: m.http_reqs ? Number(m.http_reqs.values.rate.toFixed(1)) : null,
      error_rate: m.http_req_failed ? m.http_req_failed.values.rate : null,
      latency_ms: m.http_req_duration ? {
        avg: Number(m.http_req_duration.values.avg.toFixed(2)),
        med: Number(m.http_req_duration.values.med.toFixed(2)),
        p95: Number(m.http_req_duration.values['p(95)'].toFixed(2)),
        p99: Number(m.http_req_duration.values['p(99)'].toFixed(2)),
        max: Number(m.http_req_duration.values.max.toFixed(2)),
      } : null,
      found: m.product_found ? m.product_found.values.count : 0,
      missing: m.product_missing ? m.product_missing.values.count : 0,
    },
  };
  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [`benchmarks/results/${label}-run${run}.json`]: JSON.stringify(out, null, 2) + '\n',
  };
}
