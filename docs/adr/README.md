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
| [0003](0003-bloom-filter-for-penetration-protection.md) | Bloom filter over null-caching for penetration protection | Accepted |
| [0004](0004-ttl-jitter-for-avalanche-protection.md) | TTL jitter for avalanche protection | Accepted, amended after benchmarking |
| [0005](0005-request-coalescing-for-stampede-protection.md) | In-process coalescing, and when the Redis lock earns its cost | Accepted, amended after benchmarking |
| [0006](0006-cache-invalidation-strategy.md) | Cache-aside with delete-on-write | Accepted, amended after benchmarking |
| [0007](0007-metrics-under-multiple-workers.md) | Metrics must aggregate across gunicorn workers | Accepted |
| [0008](0008-benchmark-harness-correctness.md) | The benchmark harness is part of the system under test | Accepted |
| [0009](0009-cache-adds-round-trip-latency.md) | Cache-aside made tail latency worse, not better | Accepted |
| [0010](0010-single-box-ephemeral-infrastructure.md) | Single-box ephemeral infrastructure | Accepted, not yet applied |

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

## Amended records

0004, 0005 and 0006 were amended on 2026-10-05 after the M4-M7 benchmarks were
finally run. Each had asserted a number the measurements contradicted. The
originals are left visible inside each record rather than rewritten, in the
spirit of 0009 -- an ADR that documents a result going the wrong way is worth
more than one that only ever agreed with itself.
