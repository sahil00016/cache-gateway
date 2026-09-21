# Architecture Decision Records

One file per non-obvious decision. A decision is worth an ADR when a competent
engineer could reasonably have chosen otherwise.

These are written as the decision is made, not reconstructed afterwards. The
value is in the *rejected* options and why they lost -- that is the part that
is impossible to recover six months later.

| # | Title | Status |
|---|---|---|
| [0001](0001-engineering-setup.md) | Engineering setup: stack, tooling and structure | Accepted |
| [0002](0002-env-var-typos-are-silent.md) | Environment variable typos are silently ignored | Accepted |
| 0003 | Bloom filter over null-caching for penetration protection | Pending (M4) |
| 0004 | TTL jitter for avalanche protection | Pending (M5) |
| 0005 | In-process coalescing, and when the Redis lock earns its cost | Pending (M6) |
| 0006 | Cache-aside with delete-on-write | Pending (M7) |

## Template

```markdown
# NNNN. Title

**Status:** Proposed | Accepted | Superseded by [NNNN](...)
**Date:** YYYY-MM-DD

## Context
What forced a decision. Facts and constraints, no opinions.

## Decision
What was chosen, stated plainly.

## Alternatives considered
Each option, and the specific reason it lost.

## Consequences
What this makes easy, what it makes hard, and what has to be revisited later.
```
