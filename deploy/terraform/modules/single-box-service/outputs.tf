output "public_ip" {
  description = "Public IPv4 of the box. Changes on every apply -- there is no Elastic IP by design."
  value       = aws_instance.this.public_ip
}

output "instance_id" {
  description = "EC2 instance id."
  value       = aws_instance.this.id
}

output "ecr_repository_url" {
  description = "ECR repository URL to push images to."
  value       = aws_ecr_repository.this.repository_url
}

output "ssh_command" {
  description = "Ready-to-paste SSH command."
  value       = "ssh ec2-user@${aws_instance.this.public_ip}"
}

output "health_url" {
  description = "Liveness probe URL, for verifying an apply end to end."
  value       = "http://${aws_instance.this.public_ip}:${var.container_port}/healthz"
}

output "deploy_host" {
  description = "Value for the DEPLOY_HOST GitHub Environment secret. Update it after every apply."
  value       = aws_instance.this.public_ip
}

output "github_deploy_role_arn" {
  description = "Value for the AWS_DEPLOY_ROLE_ARN secret. Empty when OIDC is disabled."
  value       = local.enable_oidc ? aws_iam_role.github_deploy[0].arn : ""
}

output "postgres_password" {
  description = "Generated Postgres password. Already written to the box's env file; exposed for debugging."
  value       = random_password.postgres.result
  sensitive   = true
}
