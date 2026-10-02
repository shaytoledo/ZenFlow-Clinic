locals {
  name    = "${var.project}-${var.environment}"
  is_prod = var.environment == "prod"

  tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
    DataClass   = "health" # identifiable patient data lives here (docs/DATA_PROTECTION.md)
  }

  azs = slice(data.aws_availability_zones.available.names, 0, var.az_count)

  # Tasks sit in private subnets behind a NAT gateway, or in public subnets with a public IP whose
  # security group admits only the load balancer (cheaper; ADR-50).
  task_subnet_ids = var.nat_gateway ? aws_subnet.private[*].id : aws_subnet.public[*].id
  task_public_ip  = !var.nat_gateway

  app_url       = "https://${var.domain_name}"
  ollama_domain = "ollama.${var.domain_name}"
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}
