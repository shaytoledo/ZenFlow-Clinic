# AWS Readiness — Gap Analysis (Phase 12.1)

What stands between today's single-host ZenFlow and the same code on AWS behind feature flags.
Measured against `master` on 2026-10-02 by reading the code, not the docs.

> **Rule (plan Phase 12):** nothing is provisioned on AWS without the owner's explicit go-ahead and
> a cost estimate. Until then Phase 12 produces code behind flags, IaC, tests and documents only.

---

## 1. The gap table

| Concern | Today (evidence) | AWS target | Flag | Work required | Task |
|---|---|---|---|---|---|
| **Database** | SQLite, WAL, one connection per thread (`bot/db.py`); ~48 modules run raw SQL with `?` placeholders; schema = 22 `CREATE TABLE IF NOT EXISTS` + a list of 17 `ALTER TABLE … ADD COLUMN` run in `try/except: pass`. SQLite-only constructs: `datetime('now')` defaults, `SQL_NOW` (`strftime(...)`) inline in ~10 files, `INSERT OR IGNORE` (5 sites), `AUTOINCREMENT` (13), `json_each`/`json_extract` (4 sites), two-argument `MAX(a,b)`, `GROUP_CONCAT`, `BEGIN IMMEDIATE`, `sqlite_master`/`PRAGMA table_info`, `rowid`, triggers with `RAISE(ABORT)` (audit guard), booleans as `INTEGER`. | RDS Postgres (or Aurora Serverless v2) | `ZF_DB_URL` (new) | A DB URL setting; SQLAlchemy Core behind the existing repositories; a small dialect layer for the constructs listed; Postgres trigger for the audit guard; run the **whole suite against Postgres in CI** (12.2.2) | 12.2.2, 12.2.3 |
| **Schema & migrations** | Created as a side effect of importing `bot.config` (`_init_db()`); ad-hoc `ALTER` list; `schema_migrations` records only two data migrations. Several containers starting at once would race. | Alembic, `0001` = today's schema exactly; migrations run once, as a deploy step | — | Alembic baseline + autogenerate check in CI; move schema creation out of import time | 12.2.3 |
| **Cache / relay** | Redis via `REDIS_URL`; settings refuse a non-local URL without `rediss://` **and** a password (SF-019). Lazy singletons; no pool limits, socket timeouts or health checks; `get_sync_redis()` tries `CONFIG SET maxmemory` (refused by ElastiCache, ignored); intake builds one `RedisChatMessageHistory` client per patient. | ElastiCache Redis, TLS + AUTH | `REDIS_URL` | Pool settings (`max_connections`, timeouts, `health_check_interval`), CA settings for TLS, skip `CONFIG SET` outside dev, share one client in intake | 12.2.4 |
| **Files** | Google tokens: encrypted in the DB (`google_tokens`) — the bot's last file reader was fixed 2026-10-02 (#108). Media: `Storage` ABC — `LocalStorage` / `S3Storage` (SSE-KMS, presigned links) behind `ZF_STORAGE_S3`, contract-tested with moto. Backups/exports: local files, owner-only, optionally encrypted (9.9, A10). Logs: files (row below). | S3 + KMS; RDS snapshots | `ZF_STORAGE_S3` | Nothing for media/tokens. Backups become RDS automated backups + PITR (12.2.7); exports to an encrypted S3 prefix | 12.2.7 |
| **Jobs** | `TaskQueue` ABC (`zenflow/queue.py`) with one implementation, `SqliteTaskQueue` (claim = `UPDATE … RETURNING`); `ZF_QUEUE_BACKEND` accepts `celery`/`temporal`/`aws` but they raise `NotImplementedError` (tested). Worker runs in-process with the bots or as `python -m zenflow.worker`. Cross-process leases in the DB. | ECS worker service + SQS (or EventBridge Scheduler) | `ZF_QUEUE_BACKEND` | An SQS-backed `TaskQueue` (delay via `DelaySeconds`/scheduler), or the Postgres queue with `FOR UPDATE SKIP LOCKED`; conformance tests run against both | 12.2.2/12.2.4 |
| **Periodic work** | The follow-up reconcile loop (`followup_scheduler._scheduler_loop`) runs inside **every** bot process. | One scheduled job (EventBridge → worker) | — | Move the loop to a scheduled job; keep the lease so duplicates are harmless | 12.2.4 |
| **LLM** | `ZF_AI_PROVIDER` / `USE_AI` = `ollama` \| `anthropic`; every call goes through the meter `ai_calls.ask()` (tested). Models are module-level LangChain objects built at import (Anthropic model id hard-coded); no Bedrock; `langchain-anthropic` not pinned; an Ollama health check runs at import. | Bedrock, the Anthropic API, or Ollama on EC2-GPU (owner decision Q3) | `ZF_AI_PROVIDER` | An `AIProvider` seam built lazily from settings; model id from settings; Bedrock adapter if chosen; parity tests across providers | 12.2.x (after Q3) |
| **Bots** | Long polling only (`bot/main.py` → `start_polling`). `ZF_WEBHOOK_MODE` **is declared but nothing reads it**. `TelegramChannel.verify_webhook` (secret-token check) exists and is contract-tested, but there is **no Telegram webhook route and no `setWebhook`**. The WhatsApp webhook route exists. Both bots must run in one process (`wire_bots`). | Webhooks behind the ALB; horizontally scalable | `ZF_WEBHOOK_MODE` | Webhook routes for both bots with secret verification; `setWebhook` at deploy; conversation state fully external (PTB persistence is SQLite today); split bots from the worker | 12.2.5 |
| **In-process state** | `bot.config.THERAPISTS` / `THERAPIST_MAP` / `THERAPIST_BY_ID` mutated at runtime from four places (31 references in 11 files) — web and bot already run as separate processes, so these copies **drift even today**. Registration ids `t{n}` allocated under process-local locks (two of them). Intake history cache and rolling summaries are module dicts. | No mutable module state; short-TTL cached repository reads | — | Replace the therapist globals with a cached repository read; allocate ids in the DB (sequence/unique constraint); keep caches keyed and bounded | 12.2.4 |
| **Sessions** | Stateless signed cookie + the `revoked_sessions` table (9.2). | Same | — | None beyond the DB move | — |
| **Secrets** | `SecretsProvider` seam: `EnvSecrets` (default) and `AwsSecretsManagerSecrets` (one JSON bundle, cached for the process) — chosen only when the **process environment** has `ZF_CLOUD` and `AWS_SECRETS_ID` (a `.env` value does not switch it). No SSM provider, no refresh. | Secrets Manager (rotation) / SSM | `ZF_CLOUD` + `AWS_SECRETS_ID` (plan: `ZF_SECRETS_BACKEND`) | Document that the switch is an environment variable of the task definition (correct for ECS); optional periodic refresh for rotated secrets | 12.2.6 |
| **Logs** | JSON outside dev (`zenflow/logging.py`) to **stderr**, plus file handlers (`logs/webLogs.text`, `logs/botLogs.text`, uvicorn's file handler) unless `ENV=test`. Email addresses and tokens are redacted (T2, A13). | CloudWatch Logs from stdout | `LOG_TO_FILES` (new) | A flag to turn file handlers off; JSON access log for uvicorn | 12.2.1 |
| **Health / ops** | `/healthz` (liveness) and `/readyz` (503 when the DB is gone); metrics at `/api/admin/metrics` behind a dashboard login (a scraper cannot reach it); tracing wired but the OpenTelemetry packages are not installed; **bots and the worker have no health endpoint**. Client IP from `X-Forwarded-For` was spoofable (SF-022, fixed: `ZF_TRUSTED_PROXIES`). | ALB health checks; CloudWatch alarms; a metrics scrape token | `ZF_TRUSTED_PROXIES` | Health endpoints for the bot/worker containers; a metrics token; set `ZF_TRUSTED_PROXIES` to the ALB subnet | 12.2.1, 12.2.6 |
| **Email / OAuth** | Gmail API per therapist (OAuth). Only `GOOGLE_REDIRECT_URI` is used; `GOOGLE_REG_REDIRECT_URI` and `GOOGLE_GMAIL_REDIRECT_URI` are copied into `bot/config.py` and never read; there is no `/auth/gmail/callback` route. https enforced outside dev. | Unchanged (per-therapist identity is the feature) | — | Remove the two dead settings or wire them; list the redirect URIs per environment in the runbook | 12.2.9 |
| **Static** | `StaticFiles` at `/static`, `Cache-Control: no-store`, unversioned file names; FullCalendar CSS from jsDelivr. | S3 + CloudFront, fingerprinted assets | `ZF_CDN` (new) | Asset fingerprinting + long cache; optional CDN origin | later |
| **Containers / IaC** | None: no Dockerfile, compose, Terraform/CDK or Alembic. `Procfile`, `railway.toml` (nixpacks), `start.sh` → `startup/launch.py` (starts Redis/Ollama locally). CI has no Postgres service, no image build, no image scan. | Multi-stage non-root images; compose parity stack; Terraform/CDK skeleton | — | Dockerfiles (web, worker, bots) + compose (app, postgres, redis, minio, ollama); IaC skeleton with staging/prod workspaces; trivy in CI | 12.2.1, 12.2.6 |

---

## 2. Found while measuring (fixed or filed)

| Finding | Status |
|---|---|
| The booking bot read Google tokens from `data/google_tokens/*.json`, which the dashboard moves into the DB and deletes → the bot silently lost every therapist's calendar | **Fixed** 2026-10-02 (#108) |
| `X-Forwarded-For` trusted from any client → the per-IP sign-in lock and sign-up cap could be dodged, audit IPs forged (SF-022) | **Fixed** 2026-10-02 (#110) |
| `ZF_WEBHOOK_MODE` looks like a working switch but nothing reads it | Filed → 12.2.5 (until then the flag is documented as "not implemented") |
| Schema creation at import time races with several containers | Filed → 12.2.3 |
| `bot.config.THERAPISTS` copies drift between the web and bot processes today | Filed → 12.2.4 |

## 3. Order of work (each step keeps `master` deployable on today's single host)

1. **12.2.1** Dockerfiles + compose parity stack; logs to stdout behind a flag.
2. **12.2.3** Alembic baseline (`0001` = today's schema), schema out of import time.
3. **12.2.2** SQLAlchemy Core behind the repositories + `ZF_DB_URL`; the whole suite on Postgres in CI.
4. **12.2.4** Remove mutable globals; reconcile loop → scheduled job; Redis pool settings.
5. **12.2.5** Telegram webhooks (both bots) behind `ZF_WEBHOOK_MODE`.
6. **12.2.6** IaC skeleton (Terraform or CDK — an ADR first), no `apply`.
7. **12.2.7–12.2.9** Backups/DR drill on the compose stack, cost estimate + smallest-viable option, migration runbook with rollback.

Owner decisions that gate parts of this: **Q3** (AWS budget, region, and where the LLM runs).
