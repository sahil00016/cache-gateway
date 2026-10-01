# M10 — Terraform: single-box ephemeral infrastructure

**Date:** 2026-10-01
**Milestone:** M10
**Status:** Approved, not yet implemented

---

## 1. Goal

Provision the AWS infrastructure this service deploys onto, as code, so that
`terraform apply` builds it from nothing and `terraform destroy` leaves nothing
billable behind. The module must be reusable by Projects 2 (Scheduler) and 3
(Vault).

## 2. What this optimises for

**The Terraform code is the deliverable. The running box is disposable.**

This project is a portfolio piece on a personal AWS account. Infrastructure that
sits running costs money and demonstrates nothing that the code in the
repository does not already demonstrate. So the design optimises for:

1. **Provable from nothing** — apply, verify, destroy, repeat
2. **Free at rest** — zero steady-state cost, by construction
3. **Low ceremony** — fewest resources that still exercise a real deployment path

### Cost reality

A correction worth recording, because it shaped the scope discussion: almost
every resource here is free. VPC, subnet, internet gateway, route table,
security group, IAM role, instance profile, the ECR *repository*, and budget
alarms all cost nothing on any account.

| Item | Cost |
|---|---|
| t3.micro instance-hours | $0 while free-tier eligible, else ~$0.01/hr |
| Public IPv4, while running | ~$0.005/hr |
| ECR image storage (~236 MB) | ~$0.02/mo |
| S3 state file | fractions of a cent |
| Everything else | **$0** |

A three-hour demo session costs under five cents. Cutting resources from this
design saves effort, not money — so scope was cut on complexity, not on cost.

## 3. Benchmarks stay local

**Decision:** M4–M7 benchmarks run against local `docker compose`, not against
the deployed box.

This is not a cost dodge. §8.1 of the project plan requires benchmark conditions
held constant across all runs — same instance size, same dataset, same warm-up,
three runs with the median reported. The M2 baseline and M3 cache-aside results
already in `benchmarks/results/` were measured locally. Re-running M4–M7 on
different AWS hardware would produce numbers that cannot be compared against the
baseline they are supposed to be measured against.

AWS demonstrates deployment. Local compose produces measurements. Conflating
them would corrupt both.

**Consequence:** no load-generator instance is provisioned. An earlier draft
included a second EC2 in the same AZ to run k6 without stealing CPU from the
service under test (per ADR-0008, the harness is part of the system under test).
That instance is unnecessary once benchmarking stays local.

## 4. Decisions and rejected alternatives

### 4.1 One box, prod only

`deploy.yml` deploys into `/opt/cache-gateway/<environment>` and reads
`DEPLOY_HOST` per GitHub Environment, which permits either one shared box or one
box per environment.

**Decision:** provision `prod` only.

**Rejected — two boxes, one per environment:** true blast-radius isolation, but
doubles instance cost and doubles what must be remembered and destroyed.

**Rejected — one box, both stacks:** a t3.micro has 1 GB of RAM. Two Postgres
instances, two Redis, and eight uvicorn workers do not fit. Considered and
discarded on arithmetic, not preference.

**Limitation accepted:** the `uat` GitHub Environment has no infrastructure
behind it until a second box is wanted. The uat → main promotion flow continues
to govern code; it does not yet govern two live environments.

### 4.2 Ephemeral lifecycle, with guardrails

**Decision:** `task infra:up` / `task infra:down`. Nothing runs between demos.

Guardrails, because the failure mode is forgetting:

- **No Elastic IP.** An EIP reserved and left behind bills whether or not it is
  attached. The auto-assigned public IP disappears with the instance. The
  trade-off is that `DEPLOY_HOST` changes on every apply — acceptable, since the
  apply is deliberate and infrequent.
- **AWS budget alarm at $5**, email notification.
- **README states the cost of a demo session** in plain numbers.

**Rejected — always-on t3.micro:** a permanently live URL is genuinely nice on a
resume, but it bills once free-tier eligibility lapses, and §10 of the plan puts
authentication out of scope. A permanently public unauthenticated box exposes
`POST /v1/admin/bloom/rebuild` — a full-table scan — to anyone who finds it.

**Rejected — code only, never applied:** zero cost and zero risk, but M10's
acceptance criterion is explicitly that apply builds the box from nothing. An
apply path that has never been run has at least one thing wrong with it.

### 4.3 State in S3

**Decision:** versioned S3 bucket, Terraform native locking (`use_lockfile`,
1.10+). No DynamoDB table.

With ephemeral infrastructure, losing state is worse than usual: `destroy` can
no longer find what it created, so resources keep billing silently. Versioning
means a corrupted state is restorable rather than orphaning resources.

**Rejected — local state:** zero setup, but a single file whose loss causes
exactly the silent-billing outcome the whole design is built to avoid.

**Rejected — a `bootstrap/` Terraform config to create the bucket:** solves the
chicken-and-egg problem at the cost of a second config to understand and
maintain. One documented `aws s3 mb` command does the same job once.

### 4.4 No managed services

Postgres and Redis run in compose on the box.

**Rejected — RDS and ElastiCache:** both change the performance topology the
benchmarks assume, and neither is free beyond a limited window. The cache
gateway's measurements depend on Postgres and Redis being exactly where they
were when the baseline was taken.

**Rejected — NAT gateway (~$32/mo) and ALB (~$16/mo):** nothing needs
egress-only networking, and a single box needs no load balancer. These are the
two resources most likely to turn a "free" account into a surprising bill.

### 4.5 Secrets

**Decision:** Terraform generates the Postgres password with `random_password`
and writes it into the box's compose env file via user-data.

Postgres is not published to the host in the production compose file; it is
reachable only inside the compose network. The security group admits only the
operator's IP.

**Rejected — SSM Parameter Store SecureString:** the better practice in general,
and free. Rejected here as gold-plating for a database that is not reachable
from outside the box. Noted as the upgrade path if the topology ever changes.

## 5. Resources

```
VPC 10.0.0.0/16  (ap-south-1a, single AZ)
├── public subnet 10.0.1.0/24
├── internet gateway + route table + association
├── security group
│     ├── :22   from var.ssh_cidr    (operator IP; never 0.0.0.0/0)
│     ├── :8000 from var.app_cidr    (operator IP; widened only for a live demo)
│     └── egress all
├── key pair                          (public key supplied by variable)
├── EC2 t3.micro
│     ├── Amazon Linux 2023, user ec2-user   (matches deploy.yml)
│     ├── AMI resolved from an SSM parameter, never a hardcoded ID
│     ├── gp3 20 GB root
│     ├── IMDSv2 required (http_tokens = "required")
│     ├── instance profile → ECR pull only
│     └── auto-assigned public IP, no EIP
├── ECR repository "cache-gateway"
│     ├── scan on push
│     └── lifecycle: keep last 10 tagged, expire untagged after 1 day
├── GitHub OIDC provider + deploy role  → AWS_DEPLOY_ROLE_ARN
│     └── trust scoped to this repository and to the main branch
└── AWS budget $5 → email notification
```

Region `ap-south-1` and SSH user `ec2-user` are not free choices — `deploy.yml`
already hardcodes both.

### user-data

Installs docker, the compose plugin, and the AWS CLI; enables and starts docker;
creates `/opt/cache-gateway/prod`; writes the compose file and the generated env
file; logs in to ECR using the instance role. Idempotent, and it logs to
`/var/log/user-data.log` so a failed boot is diagnosable.

## 6. Layout and reusability

```
deploy/terraform/
├── modules/
│   └── single-box-service/        the reusable piece
│       ├── main.tf
│       ├── variables.tf
│       ├── outputs.tf
│       ├── user-data.sh.tftpl
│       └── README.md
└── envs/
    └── prod/
        ├── backend.tf             S3 backend
        ├── main.tf                module instantiation
        ├── variables.tf
        └── terraform.tfvars
```

The module takes `service_name`, `instance_type`, `ssh_cidr`, `app_cidr`,
`container_port`, and `public_key`. Nothing in it is cache-gateway-specific.

**Reuse across repositories:** Projects 2 and 3 live in their own repositories.
The module stays in this repo for now, and its README documents the extraction
path — move it to a standalone repository and consume it by git source with a
pinned ref — to be taken when the second consumer actually exists. Building
cross-repo module plumbing before there is a second consumer is speculative.

Outputs: `public_ip`, `ecr_repository_url`, `ssh_command`, `health_url`.

## 7. Repository changes

| File | Change |
|---|---|
| `docker-compose.prod.yml` | new — pulls from ECR, does not build; Postgres and Redis unpublished |
| `Taskfile.yml` | add `infra:plan`, `infra:up`, `infra:down` |
| `.github/workflows/deploy.yml` | enable the `push: [main]` trigger |
| `.github/workflows/ci.yml` | add `terraform fmt -check` and `terraform validate` |
| `.gitignore` | add `*.tfstate*`, `.terraform/`, `.terraform.lock.hcl` kept |
| `docs/adr/0010-*.md` | new — single-box ephemeral infrastructure |
| `README.md` | infrastructure section, cost per demo session |

## 8. Security posture

Authentication is out of scope per §10 of the plan, so the security group is the
only access control. Both ingress rules default to the operator's IP. The app
port is widened to the world only deliberately, for a live demo, and narrowed
again afterwards.

IMDSv2 is required, which prevents the SSRF-to-credential-theft path that
IMDSv1 allows. The instance profile grants ECR pull and nothing else. The OIDC
deploy role's trust policy is scoped to this repository and the `main` branch,
so a fork or another branch cannot assume it.

## 9. Acceptance criteria

1. `terraform apply` from nothing provisions every resource above
2. `curl http://<public_ip>:8000/healthz` returns 200
3. A push to `main` builds, pushes a SHA-tagged image to ECR, runs migrations,
   restarts the container, and the workflow's own liveness check passes
4. `terraform destroy` leaves zero billable resources — verified in the console,
   not assumed
5. `terraform fmt -check` and `terraform validate` pass in CI
6. A second `apply` after a `destroy` succeeds without manual intervention

## 10. Out of scope

- The `uat` environment's infrastructure — one box, prod only
- Multi-AZ, autoscaling, load balancing — a single box by design
- RDS, ElastiCache, NAT gateway, ALB, Elastic IP
- TLS and a domain name — plain HTTP on port 8000
- A load-generator instance — benchmarks run locally (§3)
- Extracting the module to its own repository — deferred until a second
  consumer exists (§6)

## 11. Risks

**Scope against a 7h budget.** OIDC trust-policy conditions and user-data
debugging are where this kind of work overruns. If it slips, the honest cut is
the OIDC provider and deploy role — deploy from the laptop with local
credentials until M11 — not the state backend and not the budget alarm.

**`DEPLOY_HOST` changes on every apply.** The consequence of having no EIP. The
`infra:up` task prints the new value, and the README documents updating the
GitHub Environment secret. Annoying, not costly; the reverse would be costly.

**Free-tier terms.** AWS changed the free tier in July 2025; newer accounts
receive a time-limited credit allowance rather than the previous 12-month
always-free t3.micro. Which applies depends on when the account was created and
should be checked in the billing console before the first apply. The budget
alarm exists precisely because this is worth not guessing about.

## 12. Unrelated drift noticed

§10 of `01-cache-gateway.md` states the Bloom filter uses `rbloom`. The
implementation uses `pybloomfiltermmap3`. Not an M10 concern, but the plan
document should be corrected.
