// Cache stampede scenario.
//
// A stampede is many concurrent requests for ONE key that has just expired.
// Without coalescing each of them issues its own identical database query;
// with coalescing one request per worker queries and the rest wait on it.
//
// ---------------------------------------------------------------------------
// Why this scenario was redesigned
// ---------------------------------------------------------------------------
// The original version fired a one-shot burst: 500 VUs, one iteration each,
// scheduled 5s after a 60s TTL expired. It never produced a stampede, and the
// measurements said so plainly -- the protected and unprotected arms came out
// at 57 and 64 database queries, a difference inside the noise, and across a
// whole protected run only 23 requests ever waited on a leader.
//
// The reason is arithmetic, not a coding mistake. A stampede can only form
// inside the window where the key is missing from the cache: from the first
// miss until the first query writes the value back. For a single-row primary
// key lookup that window is about 2.5ms (see BASELINE.md). The number of
// duplicate queries is therefore roughly
//
//     arrival rate x miss window
//
// and NOT the number of clients. k6 needs seconds to start 500 VUs, so the
// burst arrived spread over seconds and almost nothing landed inside a 2.5ms
// window. Reaching 500 duplicates against a 2.5ms query would need ~200,000
// requests/second, which this hardware cannot generate.
//
// Two changes make the effect observable without pretending otherwise:
//
//   1. CONCURRENCY INSTEAD OF A BURST. Constant VUs with no sleep, all on one
//      key. Every VU has a request in flight at all times, so the instant the
//      key expires there are BURST_VUS requests already in the miss window.
//      Concurrency, not arrival rate, becomes the stampede width -- which is
//      the variable the coalescer actually acts on.
//
//   2. MANY EXPIRIES INSTEAD OF ONE. A 5s TTL over a 60s run gives ~12 expiry
//      events per run rather than one, so the result is an average over a
//      dozen stampedes instead of a single sample.
//
// Background traffic on other keys is gone: it contributed most of what the
// original 10s measurement window was counting.
//
// Usage:
//   # Without coalescing (every concurrent miss queries the database):
//   COALESCE_ENABLED=false
//   k6 run -e SCENARIO=stampede-no-coalesce benchmarks/scenarios/stampede.js
//
//   # With coalescing (one query per worker per expiry):
//   COALESCE_ENABLED=true
//   k6 run -e SCENARIO=stampede-with-coalesce benchmarks/scenarios/stampede.js
//
// The database query count comes from the service's own counters, captured by
// benchmarks/run.py. k6 cannot see it.
//
// Env:
//   BASE_URL    default http://localhost:8010
//   SCENARIO    result label
//   HOT_KEY     the product id every request targets, default 42
//   TTL         service cache TTL in seconds, must match config, default 5
//   BURST_VUS   concurrent requests in flight, default 200
//   DURATION    measured window, default 60s

import http from 'k6/http';
import { check } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8010';
const HOT_KEY = parseInt(__ENV.HOT_KEY || '42', 10);
const TTL = parseInt(__ENV.TTL || '5', 10);
const BURST_VUS = parseInt(__ENV.BURST_VUS || '200', 10);
const DURATION = __ENV.DURATION || '60s';

const requests = new Counter('stampede_requests');
const readLatency = new Trend('stampede_read_ms', true);

export const options = {
  scenarios: {
    // Constant VUs with no sleep: each VU keeps exactly one request in flight,
    // so concurrency is pinned at BURST_VUS for the whole run and every expiry
    // is met by BURST_VUS simultaneous misses.
    hammer: {
      executor: 'constant-vus',
      vus: BURST_VUS,
      duration: DURATION,
      exec: 'hammer',
    },
  },
  thresholds: {
    // The duplicate query count is the result, not a pass/fail criterion.
    // Only transport failures are a failure.
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function hammer() {
  const res = http.get(`${BASE_URL}/v1/products/${HOT_KEY}`, {
    tags: { name: 'GET /v1/products/:id (hot key)' },
  });

  requests.add(1);
  readLatency.add(res.timings.duration);

  check(res, { 'status is 200': (r) => r.status === 200 });
}

export function handleSummary(data) {
  const run = __ENV.RUN || '1';
  const scenario = __ENV.SCENARIO || 'stampede';
  const m = data.metrics;

  const total = m.stampede_requests ? m.stampede_requests.values.count : 0;
  const durationS = parseInt(DURATION, 10);
  const expiries = TTL > 0 ? Math.floor(durationS / TTL) : 0;

  const out = {
    scenario: scenario,
    run: Number(run),
    recorded_at: new Date().toISOString(),
    config: {
      base_url: BASE_URL,
      hot_key: HOT_KEY,
      ttl_seconds: TTL,
      concurrent_vus: BURST_VUS,
      duration: DURATION,
      expected_expiry_events: expiries,
    },
    results: {
      requests: total,
      rps: m.http_reqs ? Number(m.http_reqs.values.rate.toFixed(1)) : null,
      error_rate: m.http_req_failed ? m.http_req_failed.values.rate : null,
      latency_ms: m.http_req_duration
        ? {
            avg: Number(m.http_req_duration.values.avg.toFixed(2)),
            med: Number(m.http_req_duration.values.med.toFixed(2)),
            p95: Number(m.http_req_duration.values['p(95)'].toFixed(2)),
            p99: Number(m.http_req_duration.values['p(99)'].toFixed(2)),
            max: Number(m.http_req_duration.values.max.toFixed(2)),
          }
        : null,
    },
    note:
      'Duplicate query count is the headline and comes from ' +
      'cache_gateway_database_queries_total in the companion *-metrics.json, not from k6.',
  };

  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [`benchmarks/results/${scenario}-run${run}.json`]: JSON.stringify(out, null, 2) + '\n',
  };
}
