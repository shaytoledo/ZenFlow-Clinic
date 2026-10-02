# Someone hears about it: alarms and the budget go to var.alert_email (confirm the subscription once).

# CloudWatch cannot publish to a topic encrypted with the AWS-managed SNS key, so the topic has its
# own key whose policy lets CloudWatch (and the account's admins) use it.
data "aws_iam_policy_document" "alerts_key" {
  statement {
    sid       = "AccountAdmins"
    actions   = ["kms:*"]
    resources = ["*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
  }

  statement {
    sid       = "CloudWatchAlarmsPublish"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey*"]
    resources = ["*"]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
  }
}

resource "aws_kms_key" "alerts" {
  description             = "${local.name}: the alarm topic (CloudWatch must be able to publish)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.alerts_key.json
}

resource "aws_sns_topic" "alerts" {
  name              = "${local.name}-alerts"
  kms_master_key_id = aws_kms_key.alerts.arn
}

data "aws_iam_policy_document" "alerts_topic" {
  statement {
    sid       = "CloudWatchAlarms"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.alerts.arn]

    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic.json
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  alarms = {
    alb-5xx = {
      namespace  = "AWS/ApplicationELB"
      metric     = "HTTPCode_Target_5XX_Count"
      stat       = "Sum"
      threshold  = 10
      comparison = "GreaterThanThreshold"
      period     = 300
      dimensions = tomap({ LoadBalancer = aws_lb.main.arn_suffix })
      what       = "the app answered 5xx more than 10 times in 5 minutes"
    }

    web-unhealthy = {
      namespace  = "AWS/ApplicationELB"
      metric     = "HealthyHostCount"
      stat       = "Minimum"
      threshold  = 1
      comparison = "LessThanThreshold"
      period     = 60
      dimensions = tomap({ LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.web.arn_suffix })
      what       = "no healthy web task"
    }

    bots-unhealthy = {
      namespace  = "AWS/ApplicationELB"
      metric     = "HealthyHostCount"
      stat       = "Minimum"
      threshold  = 1
      comparison = "LessThanThreshold"
      period     = 60
      dimensions = tomap({ LoadBalancer = aws_lb.main.arn_suffix, TargetGroup = aws_lb_target_group.bots.arn_suffix })
      what       = "the bots are down: patients get no answers"
    }

    db-cpu = {
      namespace  = "AWS/RDS"
      metric     = "CPUUtilization"
      stat       = "Average"
      threshold  = 80
      comparison = "GreaterThanThreshold"
      period     = 300
      dimensions = tomap({ DBInstanceIdentifier = aws_db_instance.main.identifier })
      what       = "database CPU above 80%"
    }

    db-storage = {
      namespace  = "AWS/RDS"
      metric     = "FreeStorageSpace"
      stat       = "Minimum"
      threshold  = 2000000000
      comparison = "LessThanThreshold"
      period     = 300
      dimensions = tomap({ DBInstanceIdentifier = aws_db_instance.main.identifier })
      what       = "under 2 GB of database storage left"
    }

    db-connections = {
      namespace  = "AWS/RDS"
      metric     = "DatabaseConnections"
      stat       = "Maximum"
      threshold  = 60
      comparison = "GreaterThanThreshold"
      period     = 300
      dimensions = tomap({ DBInstanceIdentifier = aws_db_instance.main.identifier })
      what       = "connections near the budget (docs/DATABASE.md)"
    }

    redis-memory = {
      namespace  = "AWS/ElastiCache"
      metric     = "DatabaseMemoryUsagePercentage"
      stat       = "Maximum"
      threshold  = 85
      comparison = "GreaterThanThreshold"
      period     = 300
      dimensions = tomap({ ReplicationGroupId = aws_elasticache_replication_group.main.id })
      what       = "Redis above 85% memory: evictions drop relay routing keys"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "app" {
  for_each = local.alarms

  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.what
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.stat
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison
  period              = each.value.period
  evaluation_periods  = 2
  treat_missing_data  = "notBreaching"
  dimensions          = each.value.dimensions
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "ollama_status" {
  count = var.ollama_enabled ? 1 : 0

  alarm_name          = "${local.name}-ollama-status"
  alarm_description   = "the Ollama instance failed its status checks: intake falls back to fixed questions"
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed"
  statistic           = "Maximum"
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  period              = 60
  evaluation_periods  = 3
  dimensions          = { InstanceId = aws_instance.ollama[0].id }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# The cost guard rail (plan 12.2.6): an email before the month's spend surprises anyone.
resource "aws_budgets_budget" "monthly" {
  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
