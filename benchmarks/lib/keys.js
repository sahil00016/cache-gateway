// Key-distribution samplers, shared by every scenario.
//
// Distribution choice decides what a cache benchmark measures. Uniform over a
// million keys makes any cache look useless, because nothing is ever hot. Real
// traffic is skewed, so Zipfian is the honest case and uniform is the worst
// case. Both are reported, never averaged.
//
// ---------------------------------------------------------------------------
// Why this is bucketed, and why init is exported
// ---------------------------------------------------------------------------
// The first version built an exact CDF with one entry per key -- 1,000,000
// Math.pow calls. It also built it lazily, on first use, INSIDE the measured
// iteration.
//
// k6 gives every VU its own isolated JavaScript runtime, so that work ran once
// per VU, 600 times, in an interpreted runtime, and landed in the measurement.
// The result: 15.7 req/s against a server that sustains 300 req/s at p99 under
// 4ms, and iteration_duration averaging 14.5s while no HTTP request exceeded
// 1.55s. The benchmark measured the benchmark.
//
// Two fixes, both required:
//   1. Bucketed CDF -- 4096 entries instead of 1,000,000. Ranks are assigned
//      per bucket and a key is drawn uniformly within the chosen bucket, so
//      the Zipf shape is preserved at bucket granularity. Init drops from
//      ~1e6 operations to ~4e3.
//   2. initZipf() is called at MODULE SCOPE by each scenario. k6 runs module
//      top-level code in its init context, which is excluded from iteration
//      timing -- so setup cost can never contaminate a measurement again.
//
// The approximation: keys inside one bucket are equally likely, so with 1M
// keys the ~244 hottest keys share the top rank rather than one key dominating.
// That is fine for hit-rate and DB-load work. The stampede scenario needs a
// single genuinely hot key and uses a dedicated single-key generator instead.

const BUCKETS = 4096;

let cdf = null;
let cdfKey = '';
let keyspace = 0;

// Call at module scope, never inside an iteration.
export function initZipf(n, skew) {
  const signature = `${n}:${skew}`;
  if (cdf && cdfKey === signature) return;

  const table = new Float64Array(BUCKETS);
  let sum = 0;
  for (let i = 1; i <= BUCKETS; i++) {
    sum += 1 / Math.pow(i, skew);
    table[i - 1] = sum;
  }
  for (let i = 0; i < BUCKETS; i++) table[i] /= sum;

  cdf = table;
  cdfKey = signature;
  keyspace = n;
}

// Buckets are derived from proportions rather than a fixed width, so they tile
// 1..n exactly. A fixed `ceil(n / BUCKETS)` width does not: at n=1,000,000 and
// 4096 buckets it spans 1,003,520 keys, and the top buckets address ids past
// the end of the dataset. That produced 15 spurious 404s in the first fixed
// baseline run -- small enough to look like noise, which is precisely why it
// needed explaining rather than ignoring.
function bucketBounds(bucket) {
  const start = Math.floor((bucket * keyspace) / BUCKETS) + 1;
  const end = Math.floor(((bucket + 1) * keyspace) / BUCKETS);
  return [start, Math.max(end, start)];
}

function zipfKey() {
  // Inverse-CDF by binary search: O(log BUCKETS), no allocation.
  const r = Math.random();
  let lo = 0;
  let hi = BUCKETS - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (cdf[mid] < r) lo = mid + 1;
    else hi = mid;
  }
  const [start, end] = bucketBounds(lo);
  return start + Math.floor(Math.random() * (end - start + 1));
}

// Uniform: every key equally likely. Worst case for any cache.
export function uniformKey(n) {
  return 1 + Math.floor(Math.random() * n);
}

// Ids guaranteed NOT to exist. Used by the penetration scenario at M4.
export function absentKey(n) {
  return n + 1 + Math.floor(Math.random() * n);
}

export function pickKey(distribution, n) {
  if (distribution === 'uniform') return uniformKey(n);
  if (distribution === 'absent') return absentKey(n);
  return zipfKey();
}
