# ZenFlow on AWS — the Terraform and how it is used (Phase 12.2.6, ADR-50)

> **Nothing here has been applied.** The plan's rule is that nothing is provisioned without the
> owner's explicit go-ahead and a cost estimate (12.2.8). This page is the order of operations for
> the day that go-ahead is given. The cut-over itself (moving the data, switching Telegram and DNS,
> rolling back) is the migration runbook (12.2.9).

## What `infra/terraform/` builds

```
Internet ──HTTPS──► WAF ─► ALB (TLS 1.2/1.3, ACM, app.<domain>)
                            ├── /*                      → ECS "web"    (Fargate, 8080)
                            └── POST /telegram/{bot}    → ECS "bots"   (Fargate, 8081, ONE replica)
                                                          ECS "worker" (Fargate, no inbound)
                                                          ECS "migrate" (one-off task)
   tasks ──► RDS Postgres 16      (private, encrypted, force_ssl, PITR, password managed by RDS)
         ──► ElastiCache Redis 7  (private, TLS + AUTH, encrypted)
         ──► S3 media             (private, SSE-KMS, versioned, TLS-only)
         ──► ollama.<domain>:443  → internal NLB (TLS, ACM) → EC2 Ollama (CPU or GPU, SSM-only admin)
   everything ─► CloudWatch logs + alarms → SNS email;  monthly budget alarm → email
```

| File | What it holds |
|---|---|
| `versions.tf` | Terraform ≥ 1.6, AWS provider 5.x, the S3 state backend (configured per environment) |
| `variables.tf` | every knob, with cost-conscious defaults |
| `network.tf` | VPC over 2 AZs: public subnets (ALB, tasks unless `nat_gateway`), private subnets (data), S3 gateway endpoint |
| `security_groups.tf` | only the ALB is open to the internet; tasks accept the ALB only; data stores accept the tasks only |
| `data_stores.tf` | KMS key, S3 media, RDS, ElastiCache, the two Secrets Manager secrets |
| `app.tf` | ECR, log groups, IAM, the ECS cluster, the four task definitions and three services. **`app_environment` is the switch.** |
| `edge.tf` | ACM certificate + DNS validation, ALB, listeners, the `/telegram/*` rule, Route 53 alias, WAF |
| `ollama.tf` + `ollama_user_data.sh.tftpl` | the Ollama instance (pinned release, checksum verified), its internal TLS NLB and DNS name |
| `monitoring.tf` | SNS email, CloudWatch alarms (5xx, unhealthy web/bots, database CPU/storage/connections, Redis memory, Ollama status), the monthly budget |
| `outputs.tf` | URLs, ECR repositories, the migrate task, the secret to fill |
| `envs/*.tfvars` | staging / prod values (the `←` lines are the owner's to fill) |

**The switch.** The ECS task definitions set every flag the app already has, so the same images
that run on one host with SQLite run on AWS:

| Variable | Value on AWS | Built in |
|---|---|---|
| `ZF_DB_URL` | `postgresql+psycopg://zenflow@<rds>:5432/zenflow?sslmode=require` (no password) | 12.2.2 |
| `ZF_DB_PASSWORD` | injected from the RDS-managed secret (RDS rotates it) | 12.2.6 |
| `REDIS_URL` | `rediss://:<auth>@<elasticache>:6379/0`, injected from the generated secret | 9.11 |
| `ZF_STORAGE_S3` + `S3_*` | `1`, the media bucket, the KMS key | 4.3c |
| `ZF_WEBHOOK_MODE` + `TELEGRAM_WEBHOOK_URL` | `1`, `https://<domain>` | 12.2.5 |
| `ZF_LOG_FILES` / `LOG_FORMAT` | `0` / `json` → CloudWatch | 12.2.1 |
| `OLLAMA_HOST` | `https://ollama.<domain>` | Q3 |
| `ENV` | `prod` (staging: `staging`): every fail-fast check applies | 0.4 |

`tests/unit/test_infra.py` boots the app's own settings validation with exactly this environment
in prod mode. If the Terraform ever hands the app something it would refuse, the test fails.

## Before the first plan (once, by the owner)

1. **Approve the cost estimate** (`docs/AWS_COST_ESTIMATE.md`, 12.2.8).
2. An AWS account. Region `il-central-1` (Tel Aviv) is the default, so patient data stays in Israel.
   Enable the region in the account if it is not enabled yet (it is opt-in).
3. A domain with a **Route 53 hosted zone**. Its id goes into `envs/<env>.tfvars`.
4. The **state bucket and lock table**, by hand, with versioning and encryption:
   ```bash
   aws s3api create-bucket --bucket zenflow-terraform-state-<account-id> --region il-central-1 --create-bucket-configuration LocationConstraint=il-central-1
   aws s3api put-bucket-versioning --bucket zenflow-terraform-state-<account-id> --versioning-configuration Status=Enabled
   aws s3api put-bucket-encryption --bucket zenflow-terraform-state-<account-id> --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'
   aws s3api put-public-access-block --bucket zenflow-terraform-state-<account-id> --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   aws dynamodb create-table --table-name zenflow-terraform-locks --attribute-definitions AttributeName=LockID,AttributeType=S --key-schema AttributeName=LockID,KeyType=HASH --billing-mode PAY_PER_REQUEST --region il-central-1
   ```
   Then copy `envs/backend.hcl.example` to `envs/<env>.backend.hcl` (git-ignored) and fill it in.
   The state holds the generated Redis AUTH token: only the deployer may read the bucket.

## Applying (after the go-ahead)

```bash
cd infra/terraform
terraform init -backend-config=envs/prod.backend.hcl
terraform plan  -var-file=envs/prod.tfvars -var image_tag=<git-sha> -out prod.plan   # read it
terraform apply prod.plan
```

Then, in this order:

1. **Fill the app secret** (`terraform output app_secret_name`) in the console or with
   `aws secretsmanager put-secret-value`. Every key below must exist, or the tasks refuse to start.
   That is on purpose. Generate the three keys freshly and keep a copy **off this computer** in the
   owner's password manager: without `BACKUP_ENCRYPTION_KEY` an encrypted backup cannot be restored.
   ```json
   {
     "SESSION_SECRET": "<48+ random chars>",
     "TOKEN_ENCRYPTION_KEY": "<48+ random chars, different>",
     "BACKUP_ENCRYPTION_KEY": "<48+ random chars, different>",
     "TELEGRAM_TOKEN": "<patient bot token>",
     "THERAPIST_BOT_TOKEN": "<therapist bot token>",
     "TELEGRAM_WEBHOOK_SECRET": "<48+ random chars; letters, digits, _ and - only>",
     "GOOGLE_CLIENT_SECRET": "<Google OAuth client secret>"
   }
   ```
   Moving the existing deployment's data needs the **same** `TOKEN_ENCRYPTION_KEY` (the stored
   Google tokens are encrypted with it), or a rotation first (`python -m zenflow.rotate_token_key`).
   The runbook (12.2.9) covers this.
2. **Push the images** to the three ECR repositories (`terraform output ecr_repositories`), tagged with
   the same `image_tag`.
3. **Run the migrations** once, before the services take traffic:
   ```bash
   aws ecs run-task --cluster <ecs_cluster> --launch-type FARGATE --task-definition <migrate_task_definition> \
     --network-configuration "awsvpcConfiguration={subnets=[<task_subnets>],securityGroups=[<app_security_group>],assignPublicIp=ENABLED}"
   ```
4. The services start. The bots register their webhooks with Telegram at start-up (12.2.5), which
   **moves Telegram away from any bot still polling elsewhere**: stop the old host's bots first
   (runbook 12.2.9).
5. **Confirm the SNS subscription** email, so alarms and budget alerts arrive.

## Choices that change the bill (defaults first; 12.2.8 prices them)

| Variable | Default | Alternative |
|---|---|---|
| `nat_gateway` | `false`: tasks in public subnets with public IPs, inbound from the ALB only | `true`: private subnets behind a NAT gateway (+ ~$35/month + data) |
| `db_multi_az` | `false` | `true`: a standby in a second AZ (database cost × 2) |
| `ollama_instance_type` / `ollama_gpu` | `c7i.xlarge` / `false` (CPU, ~$147/month) | `g5.xlarge` / `true` (GPU, ~$861/month; `g4dn` is not offered in il-central-1); or `ollama_enabled = false` for a hosted model |
| `container_insights` | `false` | `true` (more metrics, billed per metric) |
| `waf_enabled` | `true` | `false` saves ~$10/month and removes a layer of protection |
| `log_retention_days` | 30 | longer costs storage |

## Validated, not applied

- CI's `infra` job runs `terraform fmt -check`, `terraform init -backend=false` + `terraform validate`,
  and a trivy misconfiguration scan (advisory). It runs on every PR, with no AWS credentials.
- `tests/unit/test_infra.py` checks what those cannot:
  - the task environment boots the app in prod mode;
  - secrets are injected, never plain environment;
  - one bots replica;
  - encryption and privacy at rest;
  - only the ALB is open to the internet;
  - Ollama is pinned and its checksum verified.
