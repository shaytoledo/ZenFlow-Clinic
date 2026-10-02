# Where patient data rests: one customer-managed KMS key encrypts the database, Redis, the media
# bucket and the secrets. Nothing here is reachable from the internet.

resource "aws_kms_key" "main" {
  description             = "${local.name}: RDS, ElastiCache, S3 media, Secrets Manager"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_kms_alias" "main" {
  name          = "alias/${local.name}"
  target_key_id = aws_kms_key.main.key_id
}

# ── media (acupoint images; ZF_STORAGE_S3, Phase 4.3c) ──
resource "aws_s3_bucket" "media" {
  bucket = "${local.name}-media-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_ownership_controls" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_public_access_block" "media" {
  bucket                  = aws_s3_bucket.media.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "media" {
  bucket = aws_s3_bucket.media.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.main.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "media" {
  bucket = aws_s3_bucket.media.id

  rule {
    id     = "expire-replaced-versions"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

data "aws_iam_policy_document" "media_tls_only" {
  statement {
    sid     = "DenyPlainHttp"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.media.arn,
      "${aws_s3_bucket.media.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "media" {
  bucket = aws_s3_bucket.media.id
  policy = data.aws_iam_policy_document.media_tls_only.json

  depends_on = [aws_s3_bucket_public_access_block.media]
}

# ── Postgres (ZF_DB_URL, 12.2.2) ──
resource "aws_db_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "main" {
  name   = local.name
  family = "postgres16"

  parameter {
    name  = "rds.force_ssl" # no plaintext connection is accepted (ADR-14)
    value = "1"
  }

  parameter {
    name  = "log_min_duration_statement" # slow queries (ms) reach CloudWatch
    value = "1000"
  }
}

resource "aws_db_instance" "main" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_allocated_storage_gb * 5
  storage_type          = "gp3"
  storage_encrypted     = true
  kms_key_id            = aws_kms_key.main.arn

  db_name  = "zenflow"
  username = "zenflow"
  # RDS creates, stores (Secrets Manager) and rotates the password; the tasks get it as
  # ZF_DB_PASSWORD and it never passes through Terraform or its state.
  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.main.key_id

  db_subnet_group_name   = aws_db_subnet_group.main.name
  parameter_group_name   = aws_db_parameter_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az

  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "00:30-01:30" # UTC = 03:30 Israel, the clinic is closed
  maintenance_window        = "sun:02:00-sun:03:00"
  copy_tags_to_snapshot     = true
  delete_automated_backups  = false
  deletion_protection       = local.is_prod
  skip_final_snapshot       = !local.is_prod
  final_snapshot_identifier = "${local.name}-final"

  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
}

# ── Redis (REDIS_URL — rediss:// with AUTH, SF-019) ──
resource "random_password" "redis_auth" {
  length  = 48
  special = false
}

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_elasticache_parameter_group" "main" {
  name   = local.name
  family = "redis7"

  parameter {
    name  = "maxmemory-policy" # what the app used to CONFIG SET itself (12.2.4)
    value = "allkeys-lru"
  }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = local.name
  description          = "ZenFlow cache, relay routing, intake history, live-update wake-ups"
  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  num_cache_clusters   = 1
  port                 = 6379

  subnet_group_name    = aws_elasticache_subnet_group.main.name
  parameter_group_name = aws_elasticache_parameter_group.main.name
  security_group_ids   = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  kms_key_id                 = aws_kms_key.main.arn
  transit_encryption_enabled = true
  auth_token                 = random_password.redis_auth.result

  automatic_failover_enabled = false
  snapshot_retention_limit   = 1
}

# ── secrets ──
# Filled by a person (docs/INFRA.md has the JSON template); Terraform never sees the values.
resource "aws_secretsmanager_secret" "app" {
  name                    = "${local.name}/app"
  description             = "ZenFlow app secrets: ${join(", ", var.app_secret_keys)}"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
}

# Values Terraform itself generated (the Redis AUTH token, inside REDIS_URL).
resource "aws_secretsmanager_secret" "generated" {
  name                    = "${local.name}/generated"
  description             = "REDIS_URL with the AUTH token Terraform generated"
  kms_key_id              = aws_kms_key.main.arn
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "generated" {
  secret_id = aws_secretsmanager_secret.generated.id
  secret_string = jsonencode({
    REDIS_URL = "rediss://:${random_password.redis_auth.result}@${aws_elasticache_replication_group.main.primary_endpoint_address}:6379/0"
  })
}
