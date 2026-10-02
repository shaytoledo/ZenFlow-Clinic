# The application on ECS Fargate: web (behind the ALB), bots (ONE replica — ADR-49), worker, and
# a one-off migrate task. The task environment below IS the switch to AWS: every flag the app
# already has, pointed at the managed services (ADR-50).

locals {
  images = { for target in ["web", "bots", "worker"] :
    target => "${aws_ecr_repository.app[target].repository_url}:${var.image_tag}"
  }

  app_environment = {
    ENV          = local.is_prod ? "prod" : "staging"
    LOG_FORMAT   = "json"
    LOG_LEVEL    = "INFO"
    CLINIC_TZ    = var.clinic_tz
    ZF_LOG_FILES = "0" # stdout → CloudWatch (12.2.1)

    # Postgres (12.2.2): no password in the URL — ZF_DB_PASSWORD comes from the RDS secret.
    ZF_DB_URL = "postgresql+psycopg://zenflow@${aws_db_instance.main.address}:5432/zenflow?sslmode=require"

    # media in S3 under the app's KMS key (4.3c)
    ZF_STORAGE_S3 = "1"
    S3_BUCKET     = aws_s3_bucket.media.bucket
    S3_REGION     = var.region
    S3_PREFIX     = "media/"
    S3_KMS_KEY_ID = aws_kms_key.main.arn

    # Telegram by webhook (12.2.5): Telegram → ALB /telegram/* → the bots task
    ZF_WEBHOOK_MODE      = "1"
    TELEGRAM_WEBHOOK_URL = local.app_url

    # The tasks accept connections from the ALB only (security group), so the peer is always the
    # ALB: uvicorn may believe X-Forwarded-* from any peer ("*" — this uvicorn takes no CIDR) and
    # takes the LAST X-Forwarded-For entry, the one the ALB appended (SF-022).
    FORWARDED_ALLOW_IPS = "*"

    # AI: Ollama stays (Q3), over TLS through the internal NLB (ADR-14 refuses plain http)
    USE_AI       = "ollama"
    OLLAMA_HOST  = var.ollama_enabled ? "https://${local.ollama_domain}" : ""
    OLLAMA_MODEL = var.ollama_model

    GOOGLE_CLIENT_ID          = var.google_client_id
    GOOGLE_REDIRECT_URI       = "${local.app_url}/auth/callback"
    GOOGLE_REG_REDIRECT_URI   = "${local.app_url}/register/google/callback"
    GOOGLE_GMAIL_REDIRECT_URI = "${local.app_url}/auth/gmail/callback"
  }

  app_secrets = concat(
    [for key in var.app_secret_keys : {
      name      = key
      valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::"
    }],
    [
      {
        name      = "REDIS_URL"
        valueFrom = "${aws_secretsmanager_secret.generated.arn}:REDIS_URL::"
      },
      {
        name      = "ZF_DB_PASSWORD"
        valueFrom = "${aws_db_instance.main.master_user_secret[0].secret_arn}:password::"
      },
    ]
  )

  container_environment = [for k, v in local.app_environment : { name = k, value = v }]
}

# ── images ──
resource "aws_ecr_repository" "app" {
  for_each = toset(["web", "bots", "worker"])

  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.main.arn
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each = aws_ecr_repository.app

  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep the last 20 images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 20
      }
      action = { type = "expire" }
    }]
  })
}

# ── logs ──
resource "aws_cloudwatch_log_group" "app" {
  for_each = toset(["web", "bots", "worker", "migrate"])

  name              = "/ecs/${local.name}/${each.key}"
  retention_in_days = var.log_retention_days
}

# ── IAM ──
data "aws_iam_policy_document" "ecs_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# Pulls images, writes logs, reads exactly the three secrets the tasks get.
resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.app.arn,
      aws_secretsmanager_secret.generated.arn,
      aws_db_instance.main.master_user_secret[0].secret_arn,
    ]
  }

  statement {
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

# What the running app may do: the media bucket, under its key. Nothing else.
resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_assume.json
}

data "aws_iam_policy_document" "task" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.media.arn}/*"]
  }

  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.media.arn]
  }

  statement {
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [aws_kms_key.main.arn]
  }
}

resource "aws_iam_role_policy" "task" {
  name   = "media"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

# ── cluster, tasks, services ──
resource "aws_ecs_cluster" "main" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = var.container_insights ? "enabled" : "disabled"
  }
}

locals {
  task_defs = {
    web     = { image = local.images.web, cpu = var.web_cpu, memory = var.web_memory, port = 8080, command = null }
    bots    = { image = local.images.bots, cpu = var.bots_cpu, memory = var.bots_memory, port = 8081, command = null }
    worker  = { image = local.images.worker, cpu = var.worker_cpu, memory = var.worker_memory, port = null, command = null }
    migrate = { image = local.images.web, cpu = 256, memory = 512, port = null, command = ["python", "-m", "zenflow.migrate", "upgrade"] }
  }
}

resource "aws_ecs_task_definition" "app" {
  for_each = local.task_defs

  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = each.value.cpu
  memory                   = each.value.memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([merge(
    {
      name                   = each.key
      image                  = each.value.image
      essential              = true
      readonlyRootFilesystem = false
      environment            = local.container_environment
      secrets                = local.app_secrets
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.app[each.key].name
          awslogs-region        = var.region
          awslogs-stream-prefix = each.key
        }
      }
      portMappings = each.value.port == null ? [] : [{ containerPort = each.value.port, protocol = "tcp" }]
    },
    each.value.command == null ? {} : { command = each.value.command },
  )])
}

resource "aws_ecs_service" "web" {
  name            = "web"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app["web"].arn
  desired_count   = var.web_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = local.task_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = 8080
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 60
  depends_on                        = [aws_lb_listener.https]
}

resource "aws_ecs_service" "bots" {
  name            = "bots"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app["bots"].arn
  # ONE replica, stop-then-start: python-telegram-bot keeps conversation state in process (ADR-49).
  desired_count                      = 1
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  launch_type                        = "FARGATE"

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = local.task_public_ip
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.bots.arn
    container_name   = "bots"
    container_port   = 8081
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  health_check_grace_period_seconds = 90
  depends_on                        = [aws_lb_listener.https]
}

resource "aws_ecs_service" "worker" {
  name            = "worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app["worker"].arn
  desired_count   = var.worker_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = local.task_subnet_ids
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = local.task_public_ip
  }

  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}
