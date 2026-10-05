// Cache consistency scenario.
//
// Measures how stale the data a reader receives actually is, while writers are
// concurrently updating the same keys. Without invalidation a write updates
// Postgres and the cache keeps serving the superseded value until its TTL
// expires. With delete-on-write (M7) the entry is removed, so the next read
// repopulates from Postgres and staleness collapses to the invalidation race
// window.
//
// ---------------------------------------------------------------------------
// Why this scenario was rewritten
// ---------------------------------------------------------------------------
// The original version kept a module-scope `recentWrites` Map, written by the
// writer VUs and read by the reader VUs to decide whether a read was stale.
// k6 gives every VU its own isolated JavaScript runtime, so a reader's Map was
// permanently empty and the staleness branch was unreachable. It reported
// "Stale reads: 0" for every configuration -- including configurations with no
// invalidation at all, where the correct answer is "almost all of them". The
// detector was blind, not the service clean. This is the same per-VU isolation
// trap recorded in ADR-0008.
//
// The fix removes the need for shared state: the writer encodes its own write
// timestamp *in the value it writes*, so any reader can date the value it got
// without knowing anything about who wrote it.
//
// Two independent measurements result:
//
//   1. value age (readers)     -- Date.now() minus the timestamp embedded in
//                                 the value. The population-level answer to
//                                 "how old is the data users see?"
//   2. read-after-write (writers) -- each writer re-reads the key it just wrote,
//                                 in its own VU, and checks whether it can see
//                                 its own write. This needs no shared state and
//                                 measures the invalidation race window directly.
//
// Usage:
//   # Without invalidation (stale until TTL):
//   CACHE_INVALIDATE_ON_WRITE=false
//   k6 run -e SCENARIO=consistency-no-invalidation benchmarks/scenarios/consistency.js
//
//   # With delete-on-write (M7):
//   CACHE_INVALIDATE_ON_WRITE=true
//   k6 run -e SCENARIO=consistency-with-invalidation benchmarks/scenarios/consistency.js
//
// Env:
//   BASE_URL   default http://localhost:8010
//   SCENARIO   result label
//   DURATION   measured window, default 60s
//   HOT_KEYS   number of keys readers and writers contend over, default 100
//   RUN        run number for the result filename, default 1

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import { textSummary } from 'https://jslib.k6.io/k6-summary/0.1.0/index.js';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8010';
const DURATION = __ENV.DURATION || '60s';
const HOT_KEY_COUNT = parseInt(__ENV.HOT_KEYS || '100', 10);

// A value at or above this is a timestamp written by this scenario; anything
// below it is an original seeded price that no writer has touched yet. Without
// this guard those rows would be dated to 1970 and swamp the age statistics.
const TIMESTAMP_FLOOR = 1_600_000_000_000; // 2020-09-13

// A value older than this, in a workload that rewrites every key several times
// a second, is being served from a cache that has not noticed a write.
const STALE_THRESHOLD_MS = parseInt(__ENV.STALE_THRESHOLD_MS || '1000', 10);

const HOT_KEYS = Array.from({ length: HOT_KEY_COUNT }, (_, i) => i + 1);

const totalReads = new Counter('consistency_total_reads');
const staleReads = new Counter('consistency_stale_reads');
const unwrittenReads = new Counter('consistency_unwritten_reads');
const valueAge = new Trend('consistency_value_age_ms', true);

const rawWrites = new Counter('consistency_writes');
const rawStale = new Counter('consistency_read_after_write_stale');
const timeToFresh = new Trend('consistency_time_to_fresh_ms', true);

export const options = {
  scenarios: {
    readers: {
      executor: 'constant-vus',
      vus: 50,
      duration: DURATION,
      exec: 'reader',
    },
    writers: {
      executor: 'constant-vus',
      vus: 50,
      duration: DURATION,
      exec: 'writer',
    },
  },
  thresholds: {
    // No pass/fail on staleness: the staleness level is the result, and the
    // "before" arm is supposed to be bad. Only transport errors are a failure.
    http_req_failed: ['rate<0.01'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(95)', 'p(99)', 'max'],
};

export function reader() {
  const productId = HOT_KEYS[Math.floor(Math.random() * HOT_KEYS.length)];
  const res = http.get(`${BASE_URL}/v1/products/${productId}`, {
    tags: { name: 'GET /v1/products/:id' },
  });

  check(res, { 'read status 200': (r) => r.status === 200 });

  if (res.status === 200) {
    totalReads.add(1);
    const value = JSON.parse(res.body).data.price_cents;

    if (value < TIMESTAMP_FLOOR) {
      // Nobody has written this key yet in this run. Not stale, just untouched.
      unwrittenReads.add(1);
    } else {
      const age = Date.now() - value;
      valueAge.add(age);
      if (age > STALE_THRESHOLD_MS) staleReads.add(1);
    }
  }

  sleep(0.1);
}

export function writer() {
  const productId = HOT_KEYS[Math.floor(Math.random() * HOT_KEYS.length)];
  const url = `${BASE_URL}/v1/products/${productId}`;

  // The value IS the timestamp. That is what makes cross-VU staleness
  // measurable without cross-VU state.
  const written = Date.now();
  const res = http.put(url, JSON.stringify({ price_cents: written }), {
    headers: { 'Content-Type': 'application/json' },
    tags: { name: 'PUT /v1/products/:id' },
  });

  check(res, { 'write status 200': (r) => r.status === 200 });

  if (res.status === 200) {
    rawWrites.add(1);

    // Read-after-write, from the same VU, so no shared state is needed.
    // A value >= our own write is fresh: either it is ours, or another writer
    // has since written something newer, which is equally not-stale. A value
    // strictly older than ours is the cache serving a superseded entry.
    const readBack = http.get(url, { tags: { name: 'GET /v1/products/:id (read-after-write)' } });
    if (readBack.status === 200) {
      const seen = JSON.parse(readBack.body).data.price_cents;
      if (seen < written) {
        rawStale.add(1);
      } else {
        timeToFresh.add(Date.now() - written);
      }
    }
  }

  sleep(0.2);
}

export function handleSummary(data) {
  const run = __ENV.RUN || '1';
  const scenario = __ENV.SCENARIO || 'consistency';
  const m = data.metrics;

  const count = (name) => (m[name] ? m[name].values.count : 0);
  const trend = (name) =>
    m[name]
      ? {
          avg: Number(m[name].values.avg.toFixed(2)),
          med: Number(m[name].values.med.toFixed(2)),
          p95: Number(m[name].values['p(95)'].toFixed(2)),
          p99: Number(m[name].values['p(99)'].toFixed(2)),
          max: Number(m[name].values.max.toFixed(2)),
        }
      : null;

  const reads = count('consistency_total_reads');
  const stale = count('consistency_stale_reads');
  const unwritten = count('consistency_unwritten_reads');
  const dated = reads - unwritten;
  const writes = count('consistency_writes');
  const rawStaleCount = count('consistency_read_after_write_stale');

  const out = {
    scenario: scenario,
    run: Number(run),
    recorded_at: new Date().toISOString(),
    config: {
      base_url: BASE_URL,
      duration: DURATION,
      hot_keys: HOT_KEY_COUNT,
      stale_threshold_ms: STALE_THRESHOLD_MS,
      readers: 50,
      writers: 50,
    },
    results: {
      rps: m.http_reqs ? Number(m.http_reqs.values.rate.toFixed(1)) : null,
      error_rate: m.http_req_failed ? m.http_req_failed.values.rate : null,
      latency_ms: m.http_req_duration ? trend('http_req_duration') : null,
      reads: {
        total: reads,
        dated: dated,
        unwritten: unwritten,
        stale: stale,
        stale_pct: dated > 0 ? Number(((stale / dated) * 100).toFixed(2)) : null,
        value_age_ms: trend('consistency_value_age_ms'),
      },
      read_after_write: {
        writes: writes,
        stale_readbacks: rawStaleCount,
        stale_pct: writes > 0 ? Number(((rawStaleCount / writes) * 100).toFixed(3)) : null,
        time_to_fresh_ms: trend('consistency_time_to_fresh_ms'),
      },
    },
  };

  return {
    stdout: textSummary(data, { indent: ' ', enableColors: false }),
    [`benchmarks/results/${scenario}-run${run}.json`]: JSON.stringify(out, null, 2) + '\n',
  };
}
