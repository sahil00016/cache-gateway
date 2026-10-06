variable "aws_region" {
  description = "AWS region. deploy.yml hardcodes ap-south-1."
  type        = string
  default     = "ap-south-1"
}

variable "instance_type" {
  description = "EC2 instance type."
  type        = string
  default     = "t3.micro"
}

variable "container_port" {
  description = "Host port the service listens on."
  type        = number
  default     = 8000
}

variable "ssh_cidr" {
  description = "CIDR allowed to SSH. The operator's IP, as a /32."
  type        = string
}

variable "app_cidr" {
  description = "CIDR allowed to reach the app port."
  type        = string
}

variable "public_key" {
  description = "SSH public key material for the operator's key pair."
  type        = string
}

variable "github_repository" {
  description = "owner/repo permitted to assume the deploy role. Empty disables OIDC."
  type        = string
  default     = "sahil00016/cache-gateway"
}

variable "create_oidc_provider" {
  description = "Create the account-global GitHub OIDC provider. False if one already exists."
  type        = bool
  default     = true
}

variable "budget_limit_usd" {
  description = "Monthly budget threshold in USD."
  type        = number
  default     = 5
}

variable "budget_notification_email" {
  description = "Address notified on budget breach."
  type        = string
  default     = ""
}
