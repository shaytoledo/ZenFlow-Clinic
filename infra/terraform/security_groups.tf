# Who may talk to whom. Only the ALB accepts traffic from the internet.

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTPS (and the HTTP redirect) into the load balancer"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-alb" }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP, answered only with a redirect to HTTPS"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_tasks" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the web (8080) and bots (8081) tasks"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8081
  referenced_security_group_id = aws_security_group.app.id
}

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "ECS tasks: inbound only from the ALB; outbound to Telegram, Google, AWS APIs"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-app" }
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "web 8080 and bots 8081, from the ALB only"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8081
  referenced_security_group_id = aws_security_group.alb.id
}

resource "aws_vpc_security_group_egress_rule" "app_out" {
  security_group_id = aws_security_group.app.id
  description       = "HTTPS APIs (Telegram, Google, AWS), Postgres, Redis, Ollama over TLS"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "Postgres, from the app tasks only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-db" }
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.app.id
}

resource "aws_security_group" "redis" {
  name        = "${local.name}-redis"
  description = "Redis (TLS + AUTH), from the app tasks only"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-redis" }
}

resource "aws_vpc_security_group_ingress_rule" "redis_from_app" {
  security_group_id            = aws_security_group.redis.id
  ip_protocol                  = "tcp"
  from_port                    = 6379
  to_port                      = 6379
  referenced_security_group_id = aws_security_group.app.id
}

resource "aws_security_group" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  name        = "${local.name}-ollama"
  description = "Ollama on 11434 from inside the VPC (the internal NLB and its health checks)"
  vpc_id      = aws_vpc.main.id

  tags = { Name = "${local.name}-ollama" }
}

resource "aws_vpc_security_group_ingress_rule" "ollama_from_vpc" {
  count = var.ollama_enabled ? 1 : 0

  security_group_id = aws_security_group.ollama[0].id
  description       = "The NLB preserves client IPs, so admit the VPC"
  ip_protocol       = "tcp"
  from_port         = 11434
  to_port           = 11434
  cidr_ipv4         = var.vpc_cidr
}

resource "aws_vpc_security_group_egress_rule" "ollama_out" {
  count = var.ollama_enabled ? 1 : 0

  security_group_id = aws_security_group.ollama[0].id
  description       = "Install packages and pull the model"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
