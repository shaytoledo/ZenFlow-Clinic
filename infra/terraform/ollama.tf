# Ollama stays (owner decision Q3, 2026-10-02): one EC2 instance, reached ONLY over TLS through an
# internal NLB at ollama.<domain> with an ACM certificate. Intake answers are health data, and the
# app refuses plain http to a non-local host (ADR-14). No SSH: administration is SSM Session Manager.

data "aws_ssm_parameter" "ollama_ami" {
  count = var.ollama_enabled ? 1 : 0

  name = var.ollama_gpu ? "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-amazon-linux-2023/latest/ami-id" : "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  name               = "${local.name}-ollama"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

resource "aws_iam_role_policy_attachment" "ollama_ssm" {
  count = var.ollama_enabled ? 1 : 0

  role       = aws_iam_role.ollama[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  name = "${local.name}-ollama"
  role = aws_iam_role.ollama[0].name
}

resource "aws_instance" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  ami                         = data.aws_ssm_parameter.ollama_ami[0].value
  instance_type               = var.ollama_instance_type
  subnet_id                   = var.nat_gateway ? aws_subnet.private[0].id : aws_subnet.public[0].id
  associate_public_ip_address = !var.nat_gateway # egress only: its security group admits the VPC alone
  vpc_security_group_ids      = [aws_security_group.ollama[0].id]
  iam_instance_profile        = aws_iam_instance_profile.ollama[0].name

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = 100
    encrypted   = true
    kms_key_id  = aws_kms_key.main.arn
  }

  user_data = templatefile("${path.module}/ollama_user_data.sh.tftpl", {
    ollama_version = var.ollama_version
    model          = var.ollama_model
  })

  tags = { Name = "${local.name}-ollama" }

  lifecycle {
    ignore_changes = [ami] # a new AMI is an explicit replacement, not a surprise in a plan
  }
}

resource "aws_lb" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  name               = "${local.name}-ollama"
  load_balancer_type = "network"
  internal           = true
  subnets            = aws_subnet.private[*].id
}

resource "aws_lb_target_group" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  name        = "${local.name}-ollama"
  port        = 11434
  protocol    = "TCP"
  target_type = "instance"
  vpc_id      = aws_vpc.main.id

  health_check {
    protocol = "HTTP"
    path     = "/" # "Ollama is running"
    matcher  = "200"
  }
}

resource "aws_lb_target_group_attachment" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  target_group_arn = aws_lb_target_group.ollama[0].arn
  target_id        = aws_instance.ollama[0].id
  port             = 11434
}

resource "aws_lb_listener" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  load_balancer_arn = aws_lb.ollama[0].arn
  port              = 443
  protocol          = "TLS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.main.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.ollama[0].arn
  }
}

resource "aws_route53_record" "ollama" {
  count = var.ollama_enabled ? 1 : 0

  zone_id = var.route53_zone_id
  name    = local.ollama_domain
  type    = "A"

  alias {
    name                   = aws_lb.ollama[0].dns_name # resolves to private IPs: unreachable outside the VPC
    zone_id                = aws_lb.ollama[0].zone_id
    evaluate_target_health = true
  }
}
