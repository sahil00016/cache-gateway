# Cache Gateway

[![CI](https://github.com/sahil00016/cache-gateway/actions/workflows/ci.yml/badge.svg)](https://github.com/sahil00016/cache-gateway/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-blue.svg)](https://mypy-lang.org/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

A read-through cache service in front of PostgreSQL, built to demonstrate the
four ways a cache fails in production and the fix for each.

The cache is not the point. The evidence is: every failure mode is reproduced
under load, measured, fixed, and measured again.

> **Status: M6 complete (code).** Bloom filter (M4), TTL jitter (M5), and request
> coalescing (M6) implemented. Benchmarks pending deployment.
> Progress is tracked in `../project planning/01-cache-gateway.md`.

---

## The four failure modes

| # | Problem | Fix | Status |
|---|---|---|---|
| 1 | **Penetration** -- lookups for keys that do not exist miss the cache every time and hit the database. Trivially weaponisable. | Bloom filter, 1.2 MB per worker. **Measured: 99.0% fewer database queries** (23,195 → 242). [ADR-0003](docs/adr/0003-bloom-filter-for-penetration-protection.md) · [results](benchmarks/PENETRATION.md) | **M4 ✓** |
| 2 | **Avalanche** -- many keys share a TTL, expire together, and the database takes the full load at once. | TTL jitter, 10% uniform. **Measured: 19.5% lower peak QPS** (113 → 91), abrupt wall becomes a six-second ramp. [ADR-0004](docs/adr/0004-ttl-jitter-for-avalanche-protection.md) · [results](benchmarks/AVALANCHE.md) | **M5 ✓** |
| 3 | **Stampede** -- one hot key expires and every concurrent request misses simultaneously. | Hand-built per-worker request coalescing. **Measured: 89.9% fewer duplicate queries**, ~18.7 per expiry → ~2.5. [ADR-0005](docs/adr/0005-request-coalescing-for-stampede-protection.md) · [results](benchmarks/STAMPEDE.md) | **M6 ✓** |
| 4 | **Consistency** -- a write updates the database but the cache keeps serving the old row. | Cache-aside with delete-on-write. **Measured: median staleness 29.7s → 0.47s**, read-your-own-writes failures 99.5% → 1.3%. [ADR-0006](docs/adr/0006-cache-invalidation-strategy.md) · [results](benchmarks/CONSISTENCY.md) | **M7 ✓** |

Every protection has a flag that disables it, so each failure can be reproduced
rather than only described: `BLOOM_ENABLED`, `CACHE_TTL_JITTER_PCT`,
`COALESCE_ENABLED`, `CACHE_INVALIDATE_ON_WRITE`. They are read at process start,
so changing one needs a restart — the service does not yet support flipping a
protection on a running instance.

**Three of the four reductions above are smaller than this README previously
claimed.** The numbers were predictions until the benchmarks were run on
2026-10-05; where measurement disagreed, the ADR was amended and the prediction
left visible. The stampede figure in particular was 99.2% on paper and is 86.6%
in practice, because a real stampede against a 2.5 ms query is about 19
duplicates deep rather than 500.

---

## Quickstart

```bash
git clone https://github.com/sahil00016/cache-gateway.git
cd cache-gateway
cp .env.example .env
task up          # Postgres, Redis and the API
curl localhost:8010/healthz
```

Running on the host instead, against the compose datastores:

```bash
task install
task dev
```

Host ports are **8010** (API), **5433** (Postgres) and **6380** (Redis) rather
than the defaults, so the stack can run alongside other local services without
a port clash.

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

Terraform provisions one t3.micro running docker-compose, in a VPC it also
creates, with an ECR repository and a GitHub OIDC role to push to it. The module
is service-agnostic and reusable.

```bash
cp deploy/terraform/envs/prod/terraform.tfvars.example \
   deploy/terraform/envs/prod/terraform.tfvars   # add your IP, key and email

task infra:plan     # what would change
task infra:up       # create; prints the new DEPLOY_HOST
task infra:down     # destroy; run this when the demo is over
task infra:check    # fmt -check and validate, same as CI
```

**The box is deliberately ephemeral — nothing runs between demos.** No Elastic
IP, NAT gateway, load balancer, or managed datastore is provisioned; those four
are the usual reason a small AWS account produces a surprising bill. Everything
that remains is free at rest, so **a three-hour demo session costs under five
cents** and an idle account costs nothing.

The trade-off is that the public IP changes on every apply, so `DEPLOY_HOST`
must be updated on the GitHub Environment each time. `task infra:up` prints it.

Reasoning and rejected alternatives:
[ADR-0010](docs/adr/0010-single-box-ephemeral-infrastructure.md) ·
[module README](deploy/terraform/modules/single-box-service/README.md).

**Status:** the Terraform is written and `terraform validate` passes in CI, but
**it has not yet been applied** — no AWS account was available that it would be
appropriate to apply it to. Benchmarks run against local compose by design and
do not depend on this (see ADR-0010).

---

## Author

Sahil Sonker — [github.com/sahil00016](https://github.com/sahil00016)

Part of a three-service set exploring caching, scheduling and secrets
management. The other two: `scheduler-service`, `vault-service`.

## License

MIT
