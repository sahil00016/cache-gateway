# 0001. Engineering setup: stack, tooling and structure

**Status:** Accepted
**Date:** 2026-09-21

## Context

This is the first of three portfolio services. Whatever is decided here is
copied into the other two, so the cost of a bad choice is tripled and the value
of a good one is too.

Two constraints shaped everything:

1. The author's professional experience is Python and TypeScript. Go was
   considered and initially chosen, then reversed -- the learning budget is
   better spent on the caching problems than on a new language.
2. The project is *about* database load. Any tool that hides the queries being
   issued is disqualified on those grounds alone.

## Decision

| Area | Choice |
|---|---|
| Runtime | Python 3.12, FastAPI, gunicorn + N uvicorn workers |
| Data access | SQLAlchemy 2.0 async + asyncpg, `echo` configurable |
| Migrations | Alembic, with a CI check for a single head |
| Config | pydantic-settings, frozen, validated at boot |
| Cache | Redis 7 via `redis.asyncio` |
| Structure | `api / service / repository / model / schema / common / core` |
| Lint & format | ruff, ~26 rule groups including pydocstyle |
| Types | mypy `--strict` |
| Docstrings | ruff `D` for presence and shape, darglint for truthfulness |
| Dependencies | pip-tools, layered `.in` files, `--generate-hashes` |
| Tests | pytest, pytest-asyncio, httpx, testcontainers |
| Tasks | Taskfile |
| Hooks | pre-commit |

## Alternatives considered

**Go instead of Python.** Would have closed a genuine gap and produced stronger
benchmark numbers. Rejected because the point of these projects is depth on the
problems, and a new language taxes every hour. The cost is acknowledged: these
projects add no new-language signal, so the measured results have to carry the
whole weight.

**GORM-style ORM convenience, or lazy loading.** Rejected outright. A project
about controlling database load cannot use a tool that issues queries the author
did not write. `echo` is a first-class setting for the same reason.

**Package by capability** (`cache/`, `store/`, `product/`), which is more
idiomatic in many codebases. Rejected in favour of layering that mirrors the
author's existing production service, so structure is not where the learning
budget is spent.

**Moderate linting.** Rejected. The strict set is the cheapest available source
of idiom feedback, and it is far easier to start strict than to retrofit.

## Consequences

**Easier.** Every tool is already familiar, so the first commit of real logic
comes early. The setup transfers to projects 2 and 3 unchanged.

**Harder.** Python's process model forces decisions Go would have deferred. The
Bloom filter and the request coalescer live in *process* memory, so with N
workers there are N of each. That is not a defect to hide -- it is the most
interesting fact in the project, and failure mode 3 measures it directly.

**Deferred.** Whether the Redis distributed lock is worth its round trip, which
cannot be answered without numbers. That is ADR-0005, at M6.
