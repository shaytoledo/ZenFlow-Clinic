# Production. Every value marked ← is the owner's to fill before `terraform plan`.
environment = "prod"
region      = "il-central-1"

domain_name     = "app.clinic.example"   # ← the owner's domain
route53_zone_id = "Z0000000000000000000" # ← its Route 53 hosted zone
alert_email     = "owner@clinic.example" # ← who gets alarms and budget emails

monthly_budget_usd = 600 # ← from the cost estimate (docs/AWS_COST_ESTIMATE.md)
image_tag          = "set-by-ci"

db_backup_retention_days = 14
db_multi_az              = false # true doubles the database cost (12.2.8)

ollama_instance_type = "g4dn.xlarge" # NVIDIA T4
ollama_gpu           = true
