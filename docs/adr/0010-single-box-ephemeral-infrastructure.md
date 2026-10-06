# ADR-0010: Single-box ephemeral infrastructure

**Status:** Accepted
**Date:** 2026-10-06
**Milestone:** M10

## Context

The service needs somewhere to run. This is a portfolio project on a personal
AWS account, which changes what "somewhere" should mean: infrastructure that
sits running costs money every hour and demonstrates nothing the repository
does not already demonstrate.

The full design, including rejected alternatives, is in
[docs/superpowers/specs/2026-10-01-m10-terraform-design.md](../superpowers/specs/2026-10-01-m10-terraform-design.md).
This ADR records the decisions that shape the code.

## Decision

**The Terraform is the deliverable. The box is disposable.**

One t3.micro in one public subnet in one AZ, brought up for a demo and
destroyed afterwards, provisioned by a module that Projects 2 and 3 can reuse
unchanged.

## What this rules out, and why

Four resources account for most surprise AWS bills on small accounts. None is
provisioned:

| Rejected | Cost | Why it is not needed |
|---|---|---|
| NAT gateway | ~$32/mo | The box has a public IP; nothing needs egress-only networking |
| Application Load Balancer | ~$16/mo | One box, no load to balance |
| Elastic IP | billed when reserved and unattached | The auto-assigned IP dies with the instance |
| RDS + ElastiCache | not free beyond a window | They would change the performance topology the benchmarks assume |

The Elastic IP decision is the one that costs something in convenience: the
public IP changes on every apply, so `DEPLOY_HOST` must be updated on the
GitHub Environment each time. `task infra:up` prints the new value. Annoying is
preferable to a reserved address quietly billing after a `destroy`.

Everything that remains — VPC, subnet, internet gateway, route table, security
group, IAM role and instance profile, the ECR *repository*, budget alarm — is
free at rest. A three-hour demo session costs under five cents. **Scope was cut
on complexity, not on cost**, because cost did not distinguish the options.

## Benchmarks stay local

M4–M7 benchmarks run against local `docker compose`, never against the
deployed box.

This is not a cost dodge. The benchmark method fixes conditions across every
run — same instance size, same dataset, same warm-up, three runs with the
median reported — and the M2 baseline and M3 cache-aside results were measured
locally. Re-running later milestones on different AWS hardware would produce
numbers that cannot be compared against the baseline they exist to be measured
against.

**AWS demonstrates deployment. Local compose produces measurements.** Conflating
them corrupts both. The consequence is that no load-generator instance is
provisioned, which an earlier draft of the design included.

## State in S3, locking without DynamoDB

Versioned S3 bucket, Terraform native locking (`use_lockfile`, 1.10+).

With ephemeral infrastructure, losing state is worse than usual: `destroy` can
no longer find what it created, so an instance keeps billing with nothing
tracking it. Versioning makes a corrupted state restorable rather than
orphaning resources. Local state was rejected for exactly that failure mode.

The bucket is created by one documented `aws s3 mb` command rather than a
`bootstrap/` Terraform config, which would be a second configuration to
understand in order to create the backend the first one needs.

## Security posture

Authentication is out of scope for this project, so **the security group is the
only access control**. That shapes several choices:

- Both ingress rules default to the operator's IP. The module *rejects*
  `0.0.0.0/0` for SSH with a validation error rather than trusting the operator
  to notice.
- The app port is widened to the world only deliberately, for a live demo, and
  narrowed again afterwards. A permanently public unauthenticated box would
  expose `POST /v1/admin/bloom/rebuild` — a full-table scan — to anyone who
  finds it.
- IMDSv2 is required. Under IMDSv1 any SSRF in the application reads the
  instance role's credentials with one unauthenticated GET.
- The instance profile grants ECR pull and nothing else.
- The GitHub OIDC deploy role's trust policy is scoped to one repository *and*
  one branch. An unscoped `sub` condition is the most common and most severe
  mistake in OIDC trust policies: it makes the role assumable from any
  repository on GitHub.

Security group rules are separate resources rather than inline blocks. Inline
rules are wholly managed by the group, so a rule added out-of-band for a demo
is silently reverted on the next apply with no diff shown first.

## Consequences

**Positive.**
- Zero steady-state cost, by construction rather than by discipline.
- Provable from nothing: apply, verify, destroy, repeat.
- The module is service-agnostic; Projects 2 and 3 pass a different
  `service_name`.
- A `$5` budget alarm notifies on *forecast* breach, which is still actionable,
  not just on actual spend, which is money already gone.

**Negative.**
- `DEPLOY_HOST` changes on every apply.
- The `uat` GitHub Environment has no infrastructure behind it. The uat → main
  promotion flow governs code, not two live environments.
- No TLS, no domain — plain HTTP on port 8000.
- A t3.micro has 1 GB of RAM. One app stack fits; two do not. This was settled
  on arithmetic, not preference.

**Deferred.**
- Extracting the module to its own repository, until a second consumer actually
  exists. Cross-repo module plumbing built before then is speculative.
- SSM Parameter Store for the Postgres password. Better practice generally, and
  free, but gold-plating for a database that publishes no host port and is
  unreachable from outside the box. It is the upgrade path if the topology
  changes.

## Status of the apply

**The code is written and `terraform validate` passes. It has not been
applied.** The AWS credentials available at the time of writing belong to a
corporate account, not the personal account this design assumes, and
provisioning a portfolio VPC, EC2 instance and an account-global GitHub OIDC
provider into an employer's production account is not a thing to do by default.

Acceptance criteria 1, 2, 4 and 6 in the design document — apply from nothing,
liveness, destroy leaving nothing billable, and a clean re-apply — are
therefore outstanding. Criterion 5, `fmt -check` and `validate` in CI, is met.

An apply path that has never been run has at least one thing wrong with it, and
that remains true here.
