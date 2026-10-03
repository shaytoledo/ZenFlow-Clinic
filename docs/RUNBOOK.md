# RUNBOOK — operating ZenFlow (Phase 13.3)

What to do when something is wrong, or something routine is due. Each entry covers both deployments:
**host**, the single machine today (`startup/launch.py`, SQLite), and **AWS**, after the move
(`docs/INFRA.md`, ECS). Commands run from the project root, with the project's virtualenv. On
AWS they run as a one-off ECS task: the `migrate` task definition with its command overridden
(`docs/MIGRATION_RUNBOOK.md` step 4 shows the `aws ecs run-task` form).

## 0. Where to look first

| What | Host | AWS |
|---|---|---|
| Is the dashboard alive? | `http://localhost:8080/healthz` | `https://<domain>/healthz` |
| Can it reach its database? | `/readyz` (503 = database gone) | same, plus the ALB target health |
| Are the bots alive? | `http://127.0.0.1:8081/healthz` (503 names the bot that is down) | ALB target group `…-bots`; alarm `…-bots-unhealthy` |
| Jobs, follow-ups, AI and message numbers | Dashboard → `/api/admin/metrics` (signed in) | same |
| Logs | `logs/webLogs.text`, `logs/botLogs.text` | CloudWatch `/ecs/zenflow-<env>/{web,bots,worker}` |
| Background jobs | `python -m zenflow.jobs stats` / `dead` | the same, as a one-off task |
| Alarms | — | SNS email (5xx, unhealthy web/bots, database CPU/storage/connections, Redis memory, Ollama) |

Logs carry a `request_id`. Find one in an error line and search for it to see the whole request.
Tokens, secrets and e-mail addresses are redacted in logs (ADR-18), so a log is safe to share
with a developer.

## 1. "The bot is down" — patients get no answer

1. **Bots `/healthz`.**
   - No answer: the process is not running. Restart (§2).
   - 503: it names the bot that stopped. Restart, then check its token (step 3).
2. **The log** (`botLogs.text` / `…/bots`):
   - `Unauthorized` / `401`: the token was revoked. Go to step 3.
   - `Conflict: terminated by other getUpdates`: **two copies are polling**. Stop one; only one bots
     process may run (ADR-49).
   - `RetryAfter` / flood: Telegram is rate-limiting us. It recovers by itself; check for a loop.
3. **The token.** In @BotFather, check the bot exists. A token is in `.env` / the app secret. Never
   paste it into a chat or a ticket. Its check is
   `curl https://api.telegram.org/bot<TOKEN>/getMe` (the reply must say `"ok": true`).
4. **Webhook or polling?** `curl https://api.telegram.org/bot<TOKEN>/getWebhookInfo`.
   - On the **host** the `url` must be empty (polling). If it is set, the AWS bots took over; see the
     migration runbook.
   - On **AWS** it must be `https://<domain>/telegram/<bot>`, with `last_error_message` empty.
     A certificate or 403 error means `TELEGRAM_WEBHOOK_SECRET` changed without a restart. Restart.
5. **Redis or the database down?** `/readyz` and the dashboard's status page say so. The relay needs
   Redis; the intake still answers without it, with fixed questions.

## 2. Restart

| | Host | AWS |
|---|---|---|
| Everything | stop `startup/launch.py` (Ctrl+C) and start it again | `aws ecs update-service --cluster <c> --service <web\|bots\|worker> --force-new-deployment` |
| Only the bots | `python startup/run_bots.py` | `--service bots` (stop-then-start: a few seconds offline; Telegram queues the updates) |

A restart loses nothing: conversations are persisted (`bot_persistence`), and jobs live in the
database. On a restart, the bots in webhook mode register their webhook again.

## 3. A dead-lettered background job

A job is dead when it failed every attempt. Its owner was alerted: a dashboard notification, e.g.
"recommendations not sent".
```bash
python -m zenflow.jobs dead                       # what died, why (errors redacted)
# fix the cause first: Google reconnected, Telegram reachable, the bug deployed
python -m zenflow.jobs replay <id> [<id> …]       # or: replay --all --name recommendations.dispatch
python -m zenflow.jobs stats
```
Handlers check the database before acting, so a replay never sends twice. A job that should not
run at all: `python -m zenflow.jobs cancel <id>`.

## 4. Rotate a secret

| Secret | How | What it costs |
|---|---|---|
| `SESSION_SECRET` | new value in `.env` / the app secret, restart | everyone signs in again |
| `TOKEN_ENCRYPTION_KEY` | set the new value, then `python -m zenflow.rotate_token_key --dry-run` and `python -m zenflow.rotate_token_key --old-material '<old key>'` | nothing, if every row re-encrypts (exit 0) |
| `BACKUP_ENCRYPTION_KEY` | new value, restart | **keep the old key**: backups made with it still need it |
| `TELEGRAM_TOKEN` / `THERAPIST_BOT_TOKEN` | @BotFather `/revoke`, new value, restart | seconds offline |
| `TELEGRAM_WEBHOOK_SECRET` (AWS) | new value, restart `bots` (the webhook is registered again with the new token) | seconds offline |
| A booking-API key | `python -m zenflow.api_keys revoke <name>` then `create <name>` (printed once) | the client needs the new key |
| `GOOGLE_CLIENT_SECRET` | rotate in Google Cloud, new value, restart | nothing: stored tokens stay valid |
| RDS password (AWS) | RDS rotates it in Secrets Manager; force a new deployment so tasks read it | seconds |
| Redis AUTH (AWS) | `terraform apply -replace=random_password.redis_auth`, then a new deployment | relay and intake state is lost (short-lived) |

Secrets never go into a log, a chat or a commit (ADR-41, `docs/SECRETS.md`).

## 5. Restore a backup

- **Host:** stop the app, then
  `python -m zenflow.file_crypto decrypt data/zenflow.db.auto-<stamp>.enc --out data/zenflow.db`,
  then start it. Check with `python -m zenflow.restore_drill`.
- **AWS:** point-in-time restore of RDS (`docs/BACKUP_DR.md`, "The AWS drill"), then point
  `ZF_DB_URL` at the restored instance and redeploy.
- Every quarter: run `python -m zenflow.restore_drill` (host) or the AWS drill, and note the result
  in `docs/BACKUP_DR.md`.

## 6. A patient asks for their data, or to be forgotten (9.9)

```bash
python -m zenflow.patient_export <patient_id> --out export.json --encrypt
python -m zenflow.patient_erasure <patient_id> --reason "request of <date>"            # plan
python -m zenflow.patient_erasure <patient_id> --reason "request of <date>" --apply    # do
```
Anonymize is the default: the clinical record stays, without identity, for the retention period
(`docs/DATA_PROTECTION.md`). Answer within a month.

## 7. Routine

| When | What |
|---|---|
| Every deploy | `python -m zenflow.migrate upgrade` before the new code starts (AWS: the `migrate` task) |
| Daily (host) | the automatic backup runs itself (`ZF_BACKUP_HOURS`); copy the newest `.enc` off the machine |
| Monthly | `python -m zenflow.retention` (a preview), then `--apply --keep <ids of minors>` (`docs/DATA_PROTECTION.md`) |
| Quarterly | the restore drill (§5); review `docs/QUESTIONS_FOR_SHAI.md` and the open rows in `docs/PROGRESS.md` |
| When a dependency alert arrives | CI's `supply-chain` job (pip-audit, gitleaks) → `python tasks.py lock` → a PR |

## 8. The alarms (AWS)

| Alarm | Means | First step |
|---|---|---|
| `…-alb-5xx` | the app answered 5xx more than 10 times in 5 min | CloudWatch `/…/web`, search `ERROR` |
| `…-web-unhealthy` / `…-bots-unhealthy` | no healthy task | §1 / §2; ECS events show why a task stopped |
| `…-db-cpu` / `…-db-connections` | the database is busy, or connections near the budget | `/api/admin/metrics`; a runaway job? (`zenflow.jobs stats`) |
| `…-db-storage` | under 2 GB free | RDS grows by itself up to 5×; check that retention runs (§7) |
| `…-redis-memory` | Redis above 85%: evictions can drop relay routes | check for a key leak; a larger node |
| `…-ollama-status` | the Ollama instance failed its checks | intake falls back to fixed questions; reboot it from the EC2 console |
| Budget (email) | 80% of the month's budget spent, or 100% forecast | `docs/AWS_COST_ESTIMATE.md` lists the levers |
