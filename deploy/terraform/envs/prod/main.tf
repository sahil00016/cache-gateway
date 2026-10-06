# Production environment for cache-gateway: one box, prod only.
#
# Region and SSH user are not free choices -- .github/workflows/deploy.yml
# already hardcodes ap-south-1 and ec2-user.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project = "cache-gateway"
      Repo    = "sahil00016/cache-gateway"
    }
  }
}

module "service" {
  source = "../../modules/single-box-service"

  service_name = "cache-gateway"
  environment  = "prod"

  instance_type  = var.instance_type
  container_port = var.container_port

  # Both default to the operator's IP. The app port is widened to the world
  # only deliberately, for a live demo, and narrowed again afterwards.
  ssh_cidr = var.ssh_cidr
  app_cidr = var.app_cidr

  public_key        = var.public_key
  availability_zone = "${var.aws_region}a"

  github_repository = var.github_repository
  github_branch     = "main"
  # The GitHub OIDC provider is account-global: creating a second one fails.
  # Set to false if this account already has one from another project.
  create_oidc_provider = var.create_oidc_provider

  budget_limit_usd          = var.budget_limit_usd
  budget_notification_email = var.budget_notification_email
}
