# ZenFlow on AWS (Phase 12.2.6, ADR-50). Nothing here is applied without the owner's go-ahead and
# the cost estimate (12.2.8) — see docs/INFRA.md for the order of operations.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # State: an encrypted, versioned S3 bucket + a DynamoDB lock table, created once by hand.
  #   terraform init -backend-config=envs/<env>.backend.hcl
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.tags
  }
}
