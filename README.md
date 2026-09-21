# Cache Gateway

[![CI](https://github.com/sahil00016/cache-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/sahil00016/cache-gateway/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A read-through cache service in front of PostgreSQL, built to demonstrate the
four ways a cache fails in production and the fix for each.

The cache is not the point. The evidence is: every failure mode is reproduced
under load, measured, fixed, and measured again.

> **Status: M1 complete.** The service skeleton runs -- configuration, health,
> metrics, tracing, error envelopes, container, CI. There is no cache yet.
> Progress is tracked in `../project planning/01-cache-gateway.md`.

---

## The four failure modes

| # | Problem | Fix | Status |
|---|---|---|---|
| 1 | **Penetration** -- lookups for keys that do not exist miss the cache every time and hit the database. Trivially weaponisable. | Bloom filter, compared against null-caching | M4 |
| 2 | **Avalanche** -- many keys share a TTL, expire together, and the database takes the full load at once. | TTL jitter | M5 |
| 3 | **Stampede** -- one hot key expires and every concurrent request misses simultaneously. | Hand-built request coalescing, plus an optional Redis lock across workers | M6 |
| 4 | **Consistency** -- a write updates the database but the cache keeps serving the old row. | Cache-aside with delete-on-write | M7 |

Every protection is independently toggleable at runtime, so each failure can be
reproduced on a live instance rather than only described.

---

## Quickstart

```bash
git clone https://github.com/sahil00016/cache-gateway.git
cd cache-gateway
cp .env.example .env
task up          # Postgres, Redis and the API
curl localhost:8000/healthz
```

Running on the host instead, against the compose datastores:

```bash
task install
task dev
```

---

## Commands

```bash
task              # list everything
task check        # exactly what CI runs -- green here means green there
task fmt          # format and autofix
task test         # unit tests
task up / down    # local stack
task migrate      # apply migrations
task openapi      # regenerate api/openapi.json
```

---

## Layout

```
src/
  api/          routers only, no business logic
  service/      business logic
  repository/   async data access
  model/        SQLAlchemy tables
  schema/       Pydantic models
  common/       settings, enums, OpenAPI metadata
  core/         exceptions, envelopes, middleware, observability, lifecycle
  db/           engine and Redis lifecycle
scripts/        CI helpers (OpenAPI export, Alembic head check)
tests/          mirrors src/
benchmarks/     k6 scenarios, results and graphs
deploy/         Terraform and compose
docs/adr/       one file per non-obvious decision
```

---

## Engineering notes

A few decisions worth knowing before reading the code. The full reasoning lives
in [`docs/adr/`](docs/adr/).

**Liveness and readiness are not the same probe.** `/healthz` touches nothing
and answers "is this process alive?". `/readyz` probes every dependency and
answers "can it serve traffic?". Conflating them causes restart storms: Postgres
hiccups, every instance fails liveness, every instance restarts, and the
reconnect herd makes the original outage worse.

**Configuration fails at boot, not on first request.** Settings are frozen and
validated at startup. A misconfigured cache is worse than a missing one, because
it fails silently under load instead of loudly at start. One gap in that
guarantee is documented in [ADR-0002](docs/adr/0002-env-var-typos-are-silent.md)
-- a mistyped variable name is silently ignored, which is why benchmarks record
*effective* settings read back from the service.

**The protections are per-worker, and that is the interesting part.** The Bloom
filter and the request coalescer live in process memory. With 4 uvicorn workers
there are 4 of each, so a hot-key expiry costs up to 4 duplicate queries rather
than 1. This is measured directly in failure mode 3 rather than assumed away.

**Error codes are derived from class names.** `ProductNotFound` becomes
`product_not_found` automatically, so the code and the class it describes cannot
drift apart -- which they always do when both are maintained by hand.

**`db_echo` is a first-class setting, not a debug flag.** This project is about
controlling database load. The queries stay visible.

---

## Deployment

Terraform provisions a single EC2 instance running docker-compose. ECS Fargate
was considered and rejected on cost; the tradeoff, and what would change at real
scale, are written up in the deploy notes. The deploy workflow exists at
`.github/workflows/deploy.yml` and is manual-dispatch only until M10.

---

## Author

Sahil Sonker — [github.com/sahil00016](https://github.com/sahil00016)

Part of a three-service set exploring caching, scheduling and secrets
management. The other two: `scheduler-service`, `vault-service`.

## License

MIT
