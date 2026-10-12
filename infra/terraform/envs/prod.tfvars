# Production. Every value marked ← is the owner's to fill before `terraform plan`.
environment = "prod"
region      = "il-central-1"

domain_name     = "app.clinic.example"   # ← the owner's domain
route53_zone_id = "Z0000000000000000000" # ← its Route 53 hosted zone
alert_email     = "owner@clinic.example" # ← who gets alarms and budget emails

monthly_budget_usd = 400 # option A's alarm (owner decision Q12, 2026-10-12; docs/AWS_COST_ESTIMATE.md)
image_tag          = "set-by-ci"

db_backup_retention_days = 14
db_multi_az              = false # the recommendation, not yet confirmed by the owner; true = + ~$17/month

# Owner decision Q12 (2026-10-12): option A — Ollama on CPU, c7i.xlarge ~$147/month. Measure its
# speed on staging before considering c7i.2xlarge (+$147) or the GPU g5.xlarge (~$861, ollama_gpu = true).
# g4dn is not offered in il-central-1.
ollama_instance_type = "c7i.xlarge"
ollama_gpu           = false
