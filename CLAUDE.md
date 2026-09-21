# Repository conventions

## Authorship — non-negotiable

Every commit in this repository is authored by Sahil Sonker, and by nobody
else. Specifically:

- **Never** add a `Co-Authored-By:` trailer of any kind.
- **Never** add "Generated with Claude Code", "Co-Authored-By: Claude", or any
  other tool attribution line to a commit message, PR body, or code comment.
- The commit author is fixed by repo-local git config:
  `sahil00016 <sahil00016@users.noreply.github.com>`. Do not change it, and do
  not fall back to the global git identity — that is a work email and must
  never appear on this public repository.

## Git workflow

- `main` is protected by the `no-commit-to-branch` pre-commit hook.
- All work happens on a branch, then a PR. CI runs on `pull_request`, so every
  change carries a visible green check.
- Conventional commit prefixes: `feat`, `fix`, `chore`, `docs`, `test`, `perf`,
  `refactor`.
- One milestone per branch, named for it (`feat/m2-baseline-endpoint`).

## Before any commit

`task check` must pass. It runs exactly what CI runs — lint, types, docstrings,
tests, OpenAPI drift, Alembic head. If those three can disagree, "green
locally" stops meaning anything, so keep their file lists in sync.

## Design rules that are not negotiable

- **Handlers never return raw dicts.** Everything goes through an envelope in
  `src/core/envelope.py`.
- **No business logic in `src/api/`.** Routers validate, delegate, serialise.
- **`src/core/` never imports from `src/service/` or `src/repository/`.** The
  dependency arrow points inward only.
- **Docstrings are enforced**, by ruff `D` for shape and darglint for accuracy.
  A docstring that lies about its signature fails the build.
- **Every non-obvious decision gets an ADR** in `docs/adr/`, written when the
  decision is made. The value is in the rejected alternatives.
- **Benchmarks record effective settings** read back from the running service,
  never the settings the operator believed they set. See ADR-0002.
