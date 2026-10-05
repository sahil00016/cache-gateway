# M7 Cache Consistency — delete-on-write, before and after

**Recorded:** 2026-10-05
**Scenario:** `benchmarks/scenarios/consistency.js`
**Raw results:** `benchmarks/results/consistency-*.json`

## What this measures

How stale the data a reader receives actually is, while writers concurrently
update the same keys. Without invalidation a write updates Postgres and the
cache keeps serving the superseded value until its TTL expires. With
delete-on-write ([ADR-0006](../docs/adr/0006-cache-invalidation-strategy.md))
the entry is removed, so the next read repopulates from Postgres.

Two independent measurements, neither of which needs shared state between k6
VUs:

1. **Value age** — each writer encodes its own wall-clock timestamp in the
   value it writes, so any reader can date the value it received.
2. **Read-after-write** — each writer immediately re-reads the key it just
   wrote, within its own VU, and checks whether it can see its own write. This
   measures the invalidation race window directly.

## Environment

| | |
|---|---|
| Host | 12 vCPU dev machine, **shared with other running containers** |
| Service | docker compose, gunicorn + **4 uvicorn workers** |
| Database | PostgreSQL 16, containerised |
| Cache TTL | 60s, 10% jitter |
| Load | 50 reader VUs + 50 writer VUs over 100 hot keys, 60s |
| Runs | 3 per arm |
| Counters | service `/metrics`, sampled at 1 Hz |

The only variable is `CACHE_INVALIDATE_ON_WRITE`.

## Results

### Staleness — the headline

Median of 3 runs.

| | No invalidation | Delete-on-write |
|---|---|---|
| **Writer cannot see own write** | **99.48%** | **1.27%** |
| Reads older than 1s | 99.46% | 21.06% |
| **Value age — median** | **29,700 ms** | **474 ms** |
| Value age — p95 | 58,331 ms | 1,986 ms |
| Value age — p99 | 64,014 ms | 12,356 ms |
| Value age — max | 67,959 ms | 28,301 ms |
| Median time-to-fresh | — | 76 ms |

**Delete-on-write cuts median staleness by 63x** (29.7s → 0.47s) and takes
read-your-own-writes failures from essentially always to 1.27%.

The read-after-write figure is the cleanest number here: it is per-writer, needs
no threshold, and is stable across all three runs in both arms (99.59 / 99.00 /
99.48 against 1.376 / 1.264 / 1.274).

### Without invalidation, staleness is bounded by the TTL — and slightly exceeds it

Observed max value age: 60,022 ms (run 1), 69,946 ms (run 2), 67,959 ms (run 3).

Run 1's 60,022 ms against a 60s base TTL is the theoretical bound reproduced to
within 22 ms. The runs that exceed it are not noise, and the mechanism is worth
stating because it is not obvious:

> Age is measured from when a value was **written to Postgres**, not from when
> it was **cached**. A reader can fetch value `V` from the database, and before
> it writes `V` into the cache another writer supersedes it. `V` then enters the
> cache already outdated and lives there for a full TTL. Its age at eviction is
> `TTL + jitter + however long V had already been stale` — comfortably past 66s.

So the honest bound is not "staleness ≤ TTL". It is "staleness ≤ TTL + jitter +
the age the value already had when it was cached".

### Delete-on-write does not eliminate the tail

This is the finding that is *not* in ADR-0006.

Median age drops to 474 ms, but **p99 is 12.4s and max is 28.3s**. With 250
writes/second spread across 100 keys, every key is rewritten several times a
second — no key should go 12 seconds without a write. These are not write gaps.
They are genuinely stale cache entries that survived invalidation.

The mechanism is the classic cache-aside interleaving, and delete-on-write does
not close it:

```
reader                          writer
------                          ------
GET key -> miss
SELECT -> V_old
                                UPDATE -> V_new
                                DELETE key        <- invalidation runs here
SET key = V_old                                   <- and is then undone
```

The reader's write-back lands *after* the delete, reinstating a stale value for
a full TTL. ADR-0006 argues correctly that delete beats update because it avoids
a write-write race; it does not claim to close this read-write race, and this
measurement shows the race is real at roughly 1-in-100 reads reaching p99
staleness of 12 seconds.

Closing it needs either versioned writes (compare-and-set on a monotonic
version, rejecting a write-back older than the current value) or a short lock
around the read's cache fill. Neither is implemented. **This is a known
limitation, now measured rather than assumed absent.**

### The cost: database load roughly triples

| | No invalidation | Delete-on-write |
|---|---|---|
| Cache hit rate | 99.51% | 66.95% |
| DB queries per read | ~0.005 | **~0.94** |
| DB QPS (runs reaching target throughput) | 122–170 | 329–347 |

Every write deletes a key that is immediately re-read, so a write-heavy
workload on hot keys converts almost every read into a cache miss. At this
workload's 1:2 write:read ratio the cache stops being a cache — a 99.5% hit
rate collapses to 67%.

**That trade is the entire substance of the decision and ADR-0006 does not
mention it.** Delete-on-write buys correctness with database load. Whether that
is the right trade depends on the write rate, which is exactly the parameter a
reader of the ADR needs to know about.

### Run 3 of the invalidation arm is excluded from DB-load figures

Run 3 achieved 211.8 req/s against 695.1 and 659.9 in runs 1 and 2 — roughly a
third of the work (2,825 writes vs ~10,000). k6 exited 0 with zero errors, so
nothing failed; the client simply could not deliver the load on a shared host.

Per [ADR-0008](../docs/adr/0008-benchmark-harness-correctness.md), when achieved
throughput collapses the number describes the harness, not the service. DB-load
figures above therefore come from runs 1 and 2 only, and that exclusion is
stated rather than hidden in a median.

Notably the **staleness** figures from run 3 are unaffected (1.274%
read-after-write, in line with 1.376% and 1.264%). The measurement that matters
for M7 is robust to the throughput variation; only its database-load side effect
is contaminated.

## Conclusion

Delete-on-write reduces median staleness **63x** and read-your-own-writes
failures from 99.5% to 1.3%, at the cost of roughly **3x the database load** and
a hit rate falling from 99.5% to 67% under this write-heavy workload.

It does **not** eliminate stale reads. A read-write interleaving can reinstate a
stale value for a full TTL, measured here at p99 = 12.4s. Delete-on-write is a
large improvement, not a correctness guarantee.

## Harness note

The original scenario could not detect staleness at all. It kept a module-scope
`recentWrites` Map, written by writer VUs and read by reader VUs. k6 gives every
VU its own isolated JavaScript runtime, so a reader's Map was permanently empty
and the staleness branch was unreachable — it reported `Stale reads: 0` for
every configuration, including ones with no invalidation, where the correct
answer is 99%. The detector was blind, not the service clean. Same per-VU
isolation trap as ADR-0008.

The rewrite removes the need for shared state by encoding the write timestamp in
the written value (`price_cents` is a BigInteger, so a millisecond epoch fits).

Separately, M7 had **no "before" arm at all**: the scenario documented
`WRITE_STRATEGY=ttl_only`, which is not a member of the `WriteStrategy` enum and
fails validation at boot, and invalidation was unconditional in the service.
Every other failure mode shipped a toggle to disable its protection;
`CACHE_INVALIDATE_ON_WRITE` was added so this one could be measured too.

## Reproducing

```bash
task up
python benchmarks/run.py consistency-no-invalidation consistency-with-invalidation --runs 3
python benchmarks/summarize.py consistency
```
