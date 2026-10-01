/**
 * M7 Consistency Scenario — Cache staleness under concurrent reads and writes.
 *
 * PROBLEM: A write updates Postgres but the cache serves stale data until
 * invalidation or TTL expires. Cache-aside with delete-on-write has a small
 * race window between DB write and cache invalidation where concurrent reads
 * can fetch and cache the old value.
 *
 * THIS SCENARIO:
 * - 100 VUs: 50 readers + 50 writers on the same 100 hot keys
 * - Readers continuously GET products
 * - Writers continuously PUT updates
 * - Measure: stale read percentage, race window duration
 *
 * RUN:
 *   # Without invalidation (baseline - let TTL handle it)
 *   WRITE_STRATEGY=ttl_only k6 run -e SCENARIO=consistency-no-invalidation benchmarks/scenarios/consistency.js
 *
 *   # With delete-on-write (M7)
 *   WRITE_STRATEGY=cache_aside k6 run -e SCENARIO=consistency-with-invalidation benchmarks/scenarios/consistency.js
 *
 * EXPECTED:
 * - Without invalidation: High stale read % (up to TTL duration)
 * - With delete-on-write: Low stale read % (brief race window only)
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
const SCENARIO = __ENV.SCENARIO || 'consistency-with-invalidation';

// Custom metrics
const staleReads = new Counter('stale_reads');
const totalReads = new Counter('total_reads');
const staleDuration = new Trend('stale_duration_ms');

// Hot keys for concurrent access
const HOT_KEYS = Array.from({ length: 100 }, (_, i) => i + 1);

export const options = {
  scenarios: {
    readers: {
      executor: 'constant-vus',
      vus: 50,
      duration: '60s',
      exec: 'reader',
    },
    writers: {
      executor: 'constant-vus',
      vus: 50,
      duration: '60s',
      exec: 'writer',
      startTime: '0s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'], // < 1% errors
    http_req_duration: ['p(95)<100'], // 95th percentile < 100ms
  },
};

// Track recent writes to detect stale reads
const recentWrites = new Map(); // key -> {value: X, timestamp: T}

export function reader() {
  const productId = HOT_KEYS[Math.floor(Math.random() * HOT_KEYS.length)];
  const url = `${BASE_URL}/v1/products/${productId}`;

  const readStart = Date.now();
  const res = http.get(url);

  check(res, {
    'read status 200': (r) => r.status === 200,
  });

  if (res.status === 200) {
    totalReads.add(1);

    const body = JSON.parse(res.body);
    const readValue = body.data.price_cents;
    const readTime = Date.now();

    // Check if we read stale data
    const recentWrite = recentWrites.get(productId);
    if (recentWrite) {
      const writeValue = recentWrite.value;
      const writeTime = recentWrite.timestamp;

      if (readValue !== writeValue && readTime > writeTime) {
        // We read old value after write completed - stale!
        staleReads.add(1);
        const staleDurationMs = readTime - writeTime;
        staleDuration.add(staleDurationMs);
      }
    }
  }

  sleep(0.1); // 10 reads/sec per VU -> 500 RPS total from 50 readers
}

export function writer() {
  const productId = HOT_KEYS[Math.floor(Math.random() * HOT_KEYS.length)];
  const url = `${BASE_URL}/v1/products/${productId}`;

  // Write a new price (timestamp-based to detect staleness)
  const newPrice = Date.now() % 100000; // Use timestamp as unique value
  const payload = JSON.stringify({
    price_cents: newPrice,
  });

  const writeStart = Date.now();
  const res = http.put(url, payload, {
    headers: { 'Content-Type': 'application/json' },
  });

  check(res, {
    'write status 200': (r) => r.status === 200,
  });

  if (res.status === 200) {
    // Record this write so readers can detect staleness
    recentWrites.set(productId, {
      value: newPrice,
      timestamp: Date.now(),
    });

    // Clean up old entries (keep last 100)
    if (recentWrites.size > 100) {
      const firstKey = recentWrites.keys().next().value;
      recentWrites.delete(firstKey);
    }
  }

  sleep(0.2); // 5 writes/sec per VU -> 250 WPS total from 50 writers
}

export function handleSummary(data) {
  const staleReadCount = data.metrics.stale_reads?.values?.count || 0;
  const totalReadCount = data.metrics.total_reads?.values?.count || 0;
  const stalePercentage = totalReadCount > 0 ? (staleReadCount / totalReadCount) * 100 : 0;

  console.log('\\n==== CONSISTENCY RESULTS ====');
  console.log(`Total reads: ${totalReadCount}`);
  console.log(`Stale reads: ${staleReadCount}`);
  console.log(`Stale read %: ${stalePercentage.toFixed(2)}%`);

  if (data.metrics.stale_duration_ms?.values) {
    const medianStale = data.metrics.stale_duration_ms.values['p(50)'];
    const p95Stale = data.metrics.stale_duration_ms.values['p(95)'];
    console.log(`Stale duration (median): ${medianStale?.toFixed(2) || 'N/A'} ms`);
    console.log(`Stale duration (p95): ${p95Stale?.toFixed(2) || 'N/A'} ms`);
  }

  console.log('\\n**EXPECTED:**');
  if (SCENARIO.includes('no-invalidation')) {
    console.log('- High stale read % (reads during TTL window see old value)');
    console.log('- Long stale duration (up to 300-360s)');
  } else {
    console.log('- Low stale read % (brief race window only, < 1%)');
    console.log('- Short stale duration (< 5ms)');
  }

  return {
    'stdout': JSON.stringify(data, null, 2),
  };
}
