variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "zenflow"
}

variable "environment" {
  description = "staging or prod — prod turns on deletion protection and final snapshots."
  type        = string

  validation {
    condition     = contains(["staging", "prod"], var.environment)
    error_message = "environment must be staging or prod."
  }
}

variable "region" {
  description = "AWS region. il-central-1 (Tel Aviv) keeps patients' data in Israel (ADR-50)."
  type        = string
  default     = "il-central-1"
}

variable "domain_name" {
  description = "Public hostname of the clinic app, e.g. app.clinic.example (a Route 53 zone must exist)."
  type        = string
}

variable "route53_zone_id" {
  description = "Hosted zone that holds domain_name (ACM validation + the alias records)."
  type        = string
}

variable "alert_email" {
  description = "Receives CloudWatch alarms and the budget alerts (confirm the SNS subscription once)."
  type        = string
}

variable "monthly_budget_usd" {
  description = "Budget alarm: email at 80% of actual and 100% of forecast monthly spend."
  type        = number
  default     = 150
}

# ── network ──
variable "vpc_cidr" {
  type    = string
  default = "10.40.0.0/16"
}

variable "az_count" {
  description = "Availability zones for subnets (RDS and the ALB need 2)."
  type        = number
  default     = 2
}

variable "nat_gateway" {
  description = "true = tasks in private subnets behind a NAT gateway (~$35/month more); false = tasks in public subnets with public IPs, inbound only from the ALB."
  type        = bool
  default     = false
}

# ── application ──
variable "image_tag" {
  description = "Tag of the web/bots/worker images in ECR (the git SHA that CI pushed)."
  type        = string
}

variable "web_cpu" {
  type    = number
  default = 512
}

variable "web_memory" {
  type    = number
  default = 1024
}

variable "web_desired_count" {
  type    = number
  default = 1
}

variable "bots_cpu" {
  type    = number
  default = 512
}

variable "bots_memory" {
  type    = number
  default = 1024
}

variable "worker_cpu" {
  type    = number
  default = 256
}

variable "worker_memory" {
  type    = number
  default = 512
}

variable "worker_desired_count" {
  type    = number
  default = 1
}

variable "clinic_tz" {
  type    = string
  default = "Asia/Jerusalem"
}

variable "google_client_id" {
  description = "Google OAuth client id (not a secret; the client secret is in the app secret)."
  type        = string
  default     = ""
}

variable "app_secret_keys" {
  description = "Keys of the person-filled app secret injected into every task (docs/INFRA.md has the template). A key missing from the secret stops the task from starting — on purpose."
  type        = list(string)
  default = [
    "SESSION_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
    "TELEGRAM_TOKEN",
    "THERAPIST_BOT_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "GOOGLE_CLIENT_SECRET",
  ]
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "container_insights" {
  description = "ECS Container Insights (extra CloudWatch metrics, billed)."
  type        = bool
  default     = false
}

# ── data ──
variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  type    = number
  default = 20
}

variable "db_multi_az" {
  description = "A standby in a second AZ (doubles the database cost)."
  type        = bool
  default     = false
}

variable "db_backup_retention_days" {
  description = "Automated backups + point-in-time recovery window (12.2.7)."
  type        = number
  default     = 14
}

variable "redis_node_type" {
  type    = string
  default = "cache.t4g.micro"
}

# ── AI (owner decision Q3: Ollama stays) ──
variable "ollama_enabled" {
  type    = bool
  default = true
}

variable "ollama_instance_type" {
  description = "il-central-1 on-demand (2026-10): c7i.xlarge CPU $0.20/h (~$147/month); c7i.2xlarge $0.40/h; g5.xlarge GPU $1.18/h (~$861/month, set ollama_gpu = true). g4dn is not offered in il-central-1."
  type        = string
  default     = "c7i.xlarge"
}

variable "ollama_gpu" {
  description = "true = the NVIDIA deep-learning AMI (drivers included) for a g5 instance; false = plain Amazon Linux."
  type        = bool
  default     = false
}

variable "ollama_model" {
  type    = string
  default = "gemma3:latest"
}

variable "ollama_version" {
  description = "Pinned Ollama release installed on the instance."
  type        = string
  default     = "0.35.1" # latest release on 2026-10-02 (github.com/ollama/ollama)
}

# ── edge ──
variable "waf_enabled" {
  type    = bool
  default = true
}

variable "telegram_cidrs" {
  description = "Telegram's published webhook source ranges; WAF admits /telegram/* only from these."
  type        = list(string)
  default     = ["149.154.160.0/20", "91.108.4.0/22"]
}
