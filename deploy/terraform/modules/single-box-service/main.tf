# One small box running a containerised service, and the minimum around it.
#
# Design and rejected alternatives: docs/superpowers/specs/2026-10-01-m10-terraform-design.md
#
# The guiding constraint is that the Terraform is the deliverable and the box is
# disposable. Everything here is either free at rest or dies with the instance:
# no Elastic IP, no NAT gateway, no load balancer, no managed datastores. Those
# four are the usual way a "free" account produces a surprising bill.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  name = "${var.service_name}-${var.environment}"

  tags = merge(
    {
      Service     = var.service_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "single-box-service"
    },
    var.tags,
  )

  enable_oidc   = var.github_repository != ""
  enable_budget = var.budget_limit_usd > 0 && var.budget_notification_email != ""
}

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

# ---------------------------------------------------------------- network --

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr

  # Both required for the instance to resolve and be resolved by name, and for
  # the ECR pull in user-data to work at all.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = merge(local.tags, { Name = local.name })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(local.tags, { Name = local.name })
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = var.subnet_cidr
  availability_zone = var.availability_zone

  # The instance needs a public IP to pull from ECR, because there is no NAT
  # gateway. That is the deliberate trade: a NAT gateway costs ~$32/month and
  # buys egress-only networking that nothing here needs.
  map_public_ip_on_launch = true

  tags = merge(local.tags, { Name = "${local.name}-public" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = merge(local.tags, { Name = "${local.name}-public" })
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# --------------------------------------------------------- security group --

resource "aws_security_group" "this" {
  name        = local.name
  description = "Ingress for ${local.name}: operator SSH and the app port."
  vpc_id      = aws_vpc.this.id

  tags = merge(local.tags, { Name = local.name })

  # The security group is referenced by the instance, so replacing it in place
  # would force the instance to be destroyed first and fail.
  lifecycle {
    create_before_destroy = true
  }
}

# Rules are separate resources rather than inline blocks: inline rules are
# wholly managed by the group, so any out-of-band rule added for a demo is
# silently reverted on the next apply with no diff shown beforehand.
resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.this.id
  description       = "Operator SSH"
  cidr_ipv4         = var.ssh_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
  tags              = local.tags
}

resource "aws_vpc_security_group_ingress_rule" "app" {
  security_group_id = aws_security_group.this.id
  description       = "Application port"
  cidr_ipv4         = var.app_cidr
  from_port         = var.container_port
  to_port           = var.container_port
  ip_protocol       = "tcp"
  tags              = local.tags
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.this.id
  description       = "All egress: ECR pulls, package installs, OS updates"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  tags              = local.tags
}

# ------------------------------------------------------------------- ecr --

resource "aws_ecr_repository" "this" {
  name                 = var.service_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # The repository is free; only stored images cost anything, and this project's
  # image is ~236 MB. force_delete lets `terraform destroy` actually complete --
  # without it a repository holding images blocks the destroy, which is exactly
  # the silent-billing outcome this design exists to avoid.
  force_delete = true

  tags = merge(local.tags, { Name = var.service_name })
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep only the 10 most recent tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 10
        }
        action = { type = "expire" }
      },
    ]
  })
}

# ------------------------------------------------------- instance profile --

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "${local.name}-instance"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "ecr_pull" {
  # GetAuthorizationToken has no resource scope -- it is account-wide by
  # definition, so a resource constraint here would simply never match.
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPull"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [aws_ecr_repository.this.arn]
  }
}

resource "aws_iam_role_policy" "ecr_pull" {
  name   = "ecr-pull"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.ecr_pull.json
}

resource "aws_iam_instance_profile" "this" {
  name = "${local.name}-instance"
  role = aws_iam_role.instance.name
  tags = local.tags
}

# -------------------------------------------------------------- instance --

# Resolved from SSM rather than hardcoded: an AMI id is region-specific and goes
# stale on every Amazon Linux release, and a hardcoded one is the most common
# reason a working Terraform config stops applying months later.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_key_pair" "this" {
  key_name   = local.name
  public_key = var.public_key
  tags       = local.tags
}

resource "random_password" "postgres" {
  length  = 32
  special = false # the password is interpolated into a URL; percent-encoding it buys nothing
}

resource "aws_instance" "this" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.this.id]
  key_name               = aws_key_pair.this.key_name
  iam_instance_profile   = aws_iam_instance_profile.this.name

  user_data = templatefile("${path.module}/user-data.sh.tftpl", {
    service_name      = var.service_name
    environment       = var.environment
    aws_region        = data.aws_region.current.region
    ecr_registry      = split("/", aws_ecr_repository.this.repository_url)[0]
    ecr_repository    = aws_ecr_repository.this.repository_url
    container_port    = var.container_port
    postgres_password = random_password.postgres.result
  })

  # Changing user-data on a running box does nothing, since cloud-init only runs
  # it at first boot. Replacing the instance is the honest behaviour: it makes
  # the box provably reproducible from nothing, which is M10's whole point.
  user_data_replace_on_change = true

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  # IMDSv2 required. IMDSv1 lets any SSRF in the application read the instance
  # role's credentials with a single unauthenticated GET; requiring a session
  # token closes that path.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2 # containers are one hop further than the host
  }

  tags = merge(local.tags, { Name = local.name })
}

# ------------------------------------------------------- github oidc role --

data "aws_iam_openid_connect_provider" "github" {
  count = local.enable_oidc && !var.create_oidc_provider ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count           = local.enable_oidc && var.create_oidc_provider ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
  tags            = local.tags
}

locals {
  oidc_provider_arn = local.enable_oidc ? (
    var.create_oidc_provider
    ? aws_iam_openid_connect_provider.github[0].arn
    : data.aws_iam_openid_connect_provider.github[0].arn
  ) : ""
}

data "aws_iam_policy_document" "github_assume" {
  count = local.enable_oidc ? 1 : 0

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Scoped to one branch of one repository. Without this the role is
    # assumable from any GitHub repository on the internet -- the single most
    # common and most severe mistake in OIDC trust policies.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  count              = local.enable_oidc ? 1 : 0
  name               = "${local.name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_assume[0].json
  tags               = local.tags
}

data "aws_iam_policy_document" "ecr_push" {
  count = local.enable_oidc ? 1 : 0

  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid = "EcrPush"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
    ]
    resources = [aws_ecr_repository.this.arn]
  }
}

resource "aws_iam_role_policy" "github_ecr_push" {
  count  = local.enable_oidc ? 1 : 0
  name   = "ecr-push"
  role   = aws_iam_role.github_deploy[0].id
  policy = data.aws_iam_policy_document.ecr_push[0].json
}

# ---------------------------------------------------------------- budget --

# The guardrail against the real failure mode, which is forgetting to destroy.
# Notifies on FORECASTED spend, not actual: by the time actual spend crosses the
# threshold the money is already gone, whereas a forecast breach is still
# actionable.
resource "aws_budgets_budget" "this" {
  count = local.enable_budget ? 1 : 0

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_notification_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
