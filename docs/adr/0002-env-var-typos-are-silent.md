# 0002. Environment variable typos are silently ignored

**Status:** Accepted
**Date:** 2026-09-21

## Context

Settings use `extra="forbid"`, which reads as though unknown configuration is
rejected. It is not. `extra="forbid"` rejects unexpected keys passed to
`__init__`; pydantic-settings only ever looks up environment variables whose
names match declared fields, and ignores everything else -- necessarily, or
`PATH` and `HOME` would be validation errors.

A test was written asserting that `CACHE_TTL_SECOND` (missing the trailing S)
would be rejected. It failed. The typo is accepted silently and the default
stays in force.

This matters more here than in a typical service. Every failure-mode protection
is an environment toggle:

```
BLOOM_ENABLED  COALESCE_ENABLED  REDIS_LOCK_ENABLED  CACHE_TTL_JITTER_PCT
```

A typo means demonstrating a fix that was never switched on, and publishing a
benchmark that measures the wrong thing. The failure is silent and the result
looks plausible, which is the worst combination.

## Decision

Accept the behaviour for now, and **document it in a test** rather than leave
it as an unstated assumption. The test
`test_a_typo_in_an_env_var_name_is_silently_ignored` asserts the real
behaviour and explains the hazard, so nobody later assumes protection that does
not exist.

Additionally, every benchmark run must record the **effective settings** read
back from the running service (via `/v1/admin/stats`), not the settings the
operator believed they set. Verifying the configuration at the point of
measurement removes the hazard where it actually causes harm.

## Alternatives considered

**Adopt an `env_prefix`** such as `CG_`, then reject any `CG_*` variable that
does not match a field. This genuinely closes the hole and is what a production
service should do. Not adopted yet: it renames every variable, including
`WEB_CONCURRENCY`, which gunicorn expects unprefixed. Worth revisiting before
the first real deployment at M10.

**Validate against `.env.example`** by parsing it for the canonical key list.
Rejected as fragile -- configuration arrives from the real environment in
production, where no `.env` file exists.

**Ignore it.** Rejected. An unwritten assumption about configuration is exactly
the kind of thing that invalidates a benchmark without anyone noticing.

## Consequences

**Now.** The hazard is documented and executable. Benchmarks report effective
configuration, so a typo is caught at the point where it would do damage.

**Later.** Revisit the `CG_` prefix at M10. If adopted, it lands in all three
services at once, since they share this skeleton.
