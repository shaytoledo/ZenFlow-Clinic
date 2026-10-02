# Staging: the same shape as prod, smallest sizes, no deletion protection, CPU-only Ollama.
environment = "staging"
region      = "il-central-1"

domain_name     = "staging.clinic.example" # ← the owner's domain
route53_zone_id = "Z0000000000000000000"   # ← its Route 53 hosted zone
alert_email     = "owner@clinic.example"   # ← who gets alarms and budget emails

monthly_budget_usd = 120
image_tag          = "set-by-ci"

ollama_instance_type = "c7i.xlarge" # CPU only: slow answers, fine for testing the wiring
ollama_gpu           = false
