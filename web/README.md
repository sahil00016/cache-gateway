# Dashboard

Live view of the cache gateway, and the switches that turn each protection off.

```bash
task up          # the service, on :8010
cd web && npm install && npm run dev    # the dashboard, on :5173
```

Vite proxies `/v1`, `/metrics` and `/readyz` to `localhost:8010`, so development
runs on one origin and CORS only has to be right for the deployed case. For a
deployed dashboard, set `CORS_ALLOW_ORIGINS` on the service to the dashboard's
origin.

## What it shows

- **Database QPS** — the headline. Every protection exists to move it.
- **Cache hit rate**, request rate, and coalesced requests per second.
- **Bloom panel** — saturation, measured against target false positive rate,
  and a rebuild button.
- **Four switches**, one per failure mode.
- **Probe** — fire reads at an existing id, an absent id, or 25 random ids and
  watch the numbers move.

## Rates, not counters

Prometheus counters only increase, so a raw counter answers "how many since this
worker booted" rather than "what is happening now". Every live number is a delta
between two consecutive polls divided by the **real** elapsed time between them,
taken from the service's own scrape timestamp — never the nominal poll interval,
which a slow response or a backgrounded tab makes wrong.

Hit rate shows `—` rather than `0%` when nothing was looked up in a window. An
idle service has no hit rate, and drawing zero would read as "everything
missed", which is the opposite of the truth.

## The switches are per worker

The flags live in process memory, so with four gunicorn workers there are four
copies and a PATCH reaches whichever worker answers it. The panel shows the
answering worker's PID and the worker count; click a switch until the state
settles. This is the same per-process limitation the Bloom filter and the
coalescer already have (ADR-0003, ADR-0005).

Sharing them would mean a Redis round trip in the read path of every request so
that a demo button feels tidier. That trade is not worth making.

## Verifying a switch really works

Reading the flag back only proves the flag changed. To prove *behaviour*
changed, watch database queries for ids that do not exist:

```
bloom ON   200 absent ids ->   0 reached Postgres
bloom OFF  200 absent ids -> 200 reached Postgres
```
