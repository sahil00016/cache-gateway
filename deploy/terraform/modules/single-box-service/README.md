# single-box-service

One small EC2 box running a containerised service, plus the minimum around it:
a VPC, an ECR repository, an instance profile that can pull from it, and an
optional GitHub OIDC role that can push to it.

Nothing in this module is cache-gateway-specific. Everything that names the
service arrives through `service_name`.

## Usage

```hcl
module "service" {
  source = "../../modules/single-box-service"

  service_name = "cache-gateway"
  environment  = "prod"

  ssh_cidr   = "203.0.113.10/32"   # your IP; 0.0.0.0/0 is rejected
  app_cidr   = "203.0.113.10/32"
  public_key = file("~/.ssh/id_ed25519.pub")

  github_repository         = "owner/repo"
  budget_notification_email = "you@example.com"
}
```

## What it creates

```
VPC + public subnet + internet gateway + route table
security group        :22 and the app port, both from a given CIDR
EC2 t3.micro          Amazon Linux 2023, IMDSv2 required, gp3 encrypted root
ECR repository        scan on push, keep last 10 tagged, expire untagged at 1d
IAM instance profile  ECR pull only
GitHub OIDC role      optional; trust scoped to one repo and one branch
AWS budget            optional; forecast and actual notifications
```

## What it deliberately does not create

No Elastic IP, NAT gateway, load balancer, or managed datastore. Those four are
the usual reason a small AWS account produces a surprising bill, and a single
disposable box needs none of them. See
[ADR-0010](../../../../docs/adr/0010-single-box-ephemeral-infrastructure.md).

The consequence to plan for: **the public IP changes on every apply.** Read it
from the `deploy_host` output and update your deployment target accordingly.

## Inputs worth knowing about

| Variable | Note |
|---|---|
| `ssh_cidr` | Validation **rejects `0.0.0.0/0`**. With authentication out of scope, the security group is the only access control there is. |
| `app_cidr` | Widen to the world only for a live demo, then narrow it again. |
| `create_oidc_provider` | The GitHub OIDC provider is **account-global**. Set `false` if the account already has one, or the apply fails with `EntityAlreadyExists`. |
| `github_repository` | Empty disables the OIDC role entirely — useful when deploying from a laptop. |
| `instance_type` | `t3.micro` is 1 GB of RAM. One app stack fits; two do not. |

## Outputs

`public_ip`, `instance_id`, `ecr_repository_url`, `ssh_command`, `health_url`,
`deploy_host`, and `postgres_password` (sensitive — already written to the box).

## user-data

Installs docker and the compose plugin (pinned, not `latest`), creates
`/opt/<service_name>/<environment>`, writes a `0600` env file containing a
Terraform-generated Postgres password, and logs in to ECR using the instance
role so no credentials are written to disk.

Everything is logged to `/var/log/user-data.log`. A user-data script that fails
silently leaves a box that looks healthy to Terraform and serves nothing, which
is the most common way this kind of deploy goes wrong.

Changing user-data sets `user_data_replace_on_change`, so the instance is
replaced rather than left running stale — cloud-init only runs user-data at
first boot, so an in-place update would do nothing at all.

## Reuse in another repository

The module lives here while this is its only consumer. When Project 2 or 3
needs it, move it to a standalone repository and consume it by git source with
a pinned ref:

```hcl
source = "git::https://github.com/<owner>/terraform-single-box-service.git?ref=v1.0.0"
```

Building that plumbing before a second consumer exists is speculative, so it is
deliberately deferred.
