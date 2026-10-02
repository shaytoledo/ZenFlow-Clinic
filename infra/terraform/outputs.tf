output "app_url" {
  value = local.app_url
}

output "telegram_webhook_url" {
  description = "Base the bots register with setWebhook (they append /telegram/<bot>)."
  value       = local.app_url
}

output "ecr_repositories" {
  description = "Push web/bots/worker images here, tagged with var.image_tag."
  value       = { for k, repo in aws_ecr_repository.app : k => repo.repository_url }
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "migrate_task_definition" {
  description = "Run once per deploy, before the new services start (docs/INFRA.md)."
  value       = aws_ecs_task_definition.app["migrate"].arn
}

output "task_subnets" {
  value = local.task_subnet_ids
}

output "app_security_group" {
  value = aws_security_group.app.id
}

output "app_secret_name" {
  description = "Fill this secret with the JSON template in docs/INFRA.md before the first deploy."
  value       = aws_secretsmanager_secret.app.name
}

output "database_endpoint" {
  value = aws_db_instance.main.address
}

output "media_bucket" {
  value = aws_s3_bucket.media.bucket
}
