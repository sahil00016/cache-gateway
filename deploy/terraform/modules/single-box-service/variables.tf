# Inputs for the single-box-service module.
#
# Nothing here is cache-gateway-specific: the module provisions "one small box
# running a containerised service", which is the shape Projects 2 and 3 need
# too. Anything that names this service arrives through `service_name`.

variable "service_name" {
  description = "Service identifier. Names every resource and the ECR repository."
  type        = string

  validation {
    # ECR repository names and the /opt/<name> path both reject uppercase and
    # most punctuation, so the constraint is enforced here rather than surfacing
    # as a confusing API error halfway through an apply.
    condition     = can(regex("^[a-z][a-z0-9-]{1,38}$", var.service_name))
    error_message = "service_name must be lowercase alphanumeric with hyphens, 2-39 chars, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name. Deployed to /opt/<service_name>/<environment>, matching deploy.yml."
  type        = string
  default     = "prod"
}

variable "instance_type" {
  description = "EC2 instance type. t3.micro has 1 GB RAM, which fits one app stack and no more."
  type        = string
  default     = "t3.micro"
}

variable "root_volume_gb" {
  description = "Root EBS volume size in GB."
  type        = number
  default     = 20
}

variable "ssh_cidr" {
  description = "CIDR allowed to reach port 22. The operator's IP; never 0.0.0.0/0."
  type        = string

  validation {
    # Authentication is out of scope for this project, so the security group is
    # the only access control there is. An open SSH port would hand over a box
    # with no second line of defence.
    condition     = var.ssh_cidr != "0.0.0.0/0"
    error_message = "ssh_cidr must not be 0.0.0.0/0. The security group is the only access control on this box."
  }
}

variable "app_cidr" {
  description = "CIDR allowed to reach the app port. Widened to the world only deliberately, for a live demo."
  type        = string
}

variable "container_port" {
  description = "Host port the service listens on."
  type        = number
  default     = 8000
}

variable "public_key" {
  description = "SSH public key material for the operator's key pair."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR for the VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR for the single public subnet."
  type        = string
  default     = "10.0.1.0/24"
}

variable "availability_zone" {
  description = "AZ for the subnet. Single-AZ by design; this is not a highly available deployment."
  type        = string
  default     = "ap-south-1a"
}

variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role via OIDC. Empty disables the OIDC role entirely."
  type        = string
  default     = ""
}

variable "github_branch" {
  description = "Branch whose workflow runs may assume the deploy role."
  type        = string
  default     = "main"
}

variable "create_oidc_provider" {
  description = "Create the GitHub OIDC provider. False when the account already has one -- it is account-global and a duplicate fails."
  type        = bool
  default     = true
}

variable "budget_limit_usd" {
  description = "Monthly budget threshold in USD. Zero disables the budget alarm."
  type        = number
  default     = 5
}

variable "budget_notification_email" {
  description = "Address notified when the budget threshold is forecast to be exceeded."
  type        = string
  default     = ""
}

variable "tags" {
  description = "Extra tags applied to every resource that supports them."
  type        = map(string)
  default     = {}
}
