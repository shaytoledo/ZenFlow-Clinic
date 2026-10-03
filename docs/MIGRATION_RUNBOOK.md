# Migration Runbook — from the single host to AWS, and back (Phase 12.2.9)

> Used only after the owner's go-ahead on the cost estimate (`docs/AWS_COST_ESTIMATE.md`) and
> after `terraform apply` (`docs/INFRA.md`). **Rehearse the whole runbook on staging first.**
> Nothing in it is reversible by accident: the old host's database is never modified, so going
> back is always possible.

## What moves, and how

| Data | From (the host today) | To (AWS) | How |
|---|---|---|---|
| The database (every table) | `data/zenflow.db` (SQLite) | RDS Postgres | `zenflow.move_data`: verified copy, ids kept, sequences reset |
| Acupoint images | `data/media/` | S3 media bucket, `media/` prefix | `aws s3 sync` (KMS-encrypted) |
| Google tokens | inside the database, encrypted | same | the **same** `TOKEN_ENCRYPTION_KEY` in the AWS app secret |
| Telegram | the bots poll | webhooks to `https://<domain>/telegram/*` | automatic when the AWS bots start (12.2.5) |
| Redis (relay routes, intake histories) | local Redis | ElastiCache | **not moved**: short-lived by design; a patient mid-intake answers that question again |

`zenflow.move_data` (tested both ways in CI: `tests/integration/test_move_data.py`):
- opens the source read-only and refuses a target that already holds data;
- copies every table in foreign-key order in one transaction;
- verifies every table's row count and SHA-256 in source and target, and exits 1 on any difference.

**Performed 2026-10-03** on the synthetic clinic from the restore drill:

| Run | Rows | Took | Result |
|---|---|---|---|
| Cut-over: SQLite → Postgres 16 | 952 | 0.85 s | identical |
| Rollback: Postgres → a new SQLite file | 952 | 0.82 s | identical |
| The same cut-over again into the now-full database | — | — | **refused**, exit 2 |
| The source file's SHA-256 before and after | — | — | **unchanged** |

## The day before (T − 1)

- [ ] Staging rehearsal of this whole page done, timings written down.
- [ ] `terraform apply` done for prod; `terraform output` saved.
- [ ] The app secret is filled. It uses the **same** `TOKEN_ENCRYPTION_KEY` and `BACKUP_ENCRYPTION_KEY`
      as the host today, and a **new** `TELEGRAM_WEBHOOK_SECRET`.
- [ ] Images pushed to ECR with the release's `image_tag`. The `migrate` task has run once (schema built).
- [ ] Google Cloud console: add `https://<domain>/auth/callback`, `/register/google/callback` and
      `/auth/gmail/callback` to the OAuth client's redirect URIs. Keep the old ones until the end.
- [ ] Services scaled to **0** (`web`, `bots`, `worker`), so nothing writes to RDS before the data is there.
- [ ] Therapists told: a 30-minute pause at the chosen quiet hour (e.g. 22:00).
- [ ] The SNS alarm subscription is confirmed (the email arrived).

## The cut-over (≈ 30 minutes)

1. **Stop writes on the old host.** Stop the bots and the dashboard (`startup/launch.py` → Ctrl+C,
   or the service). Nothing may write to `data/zenflow.db` from here on.
2. **Take the final backup, encrypted**, and keep it on the host as the rollback anchor:
   ```bash
   python -m zenflow.db_backup --encrypt          # prints data/zenflow.db.bak-<stamp>.enc
   python -m zenflow.restore_drill                # must print "ok": true
   ```
3. **Upload** the backup and the media (KMS key = `terraform output`; the bucket = `media_bucket`):
   ```bash
   aws s3 cp data/zenflow.db.bak-<stamp>.enc s3://<media_bucket>/migration/zenflow.db.enc --sse aws:kms --sse-kms-key-id <kms key arn>
   aws s3 sync data/media/ s3://<media_bucket>/media/ --sse aws:kms --sse-kms-key-id <kms key arn>
   ```
4. **Move the data into RDS**, as a one-off task in the private network (RDS is not reachable from
   outside it):
   ```bash
   aws ecs run-task --cluster <ecs_cluster> --launch-type FARGATE --task-definition <migrate_task_definition> \
     --network-configuration "awsvpcConfiguration={subnets=[<task_subnets>],securityGroups=[<app_security_group>],assignPublicIp=ENABLED}" \
     --overrides '{"containerOverrides":[{"name":"migrate","command":["python","-m","zenflow.move_data","--from","s3://<media_bucket>/migration/zenflow.db.enc","--to","app"]}]}'
   ```
   Read its log in CloudWatch (`/ecs/zenflow-prod/migrate`). Go on **only** with `"ok": true` and
   `"differences": []`. Exit 2 means it refused (the reason is printed). Exit 1 means it found a
   difference: stop, and roll back.
5. **Start the services**: `web` 1, `bots` 1, `worker` 1. At start-up the bots register their
   webhooks, so Telegram now delivers to AWS.
6. **Verify** (10 minutes):
   - [ ] `https://<domain>/healthz` → 200; `/readyz` → 200
   - [ ] sign in to the dashboard; today's schedule and a known patient's history are there
   - [ ] an acupoint image opens in a treatment page (S3)
   - [ ] the patient bot: `/start` answers; the therapist bot: `/start` answers
   - [ ] book a test appointment and cancel it (then delete nothing — it is history)
   - [ ] "Send recommendations" for a test session reaches a test inbox (Google tokens decrypted)
   - [ ] CloudWatch: no `ERROR` in `/ecs/zenflow-prod/*` for 10 minutes; no alarm
7. **Done.** Delete `s3://<media_bucket>/migration/zenflow.db.enc`. Keep the old host's
   `data/` (and the encrypted backup) untouched for 30 days. Remove the old Google redirect URIs
   after a week.

## Rollback

**Decide by the verification checklist.** Any failed line that cannot be fixed in 15 minutes means
rolling back. Two cases:

**A. Before any real use on AWS** (rolled back during the cut-over window). The old database is untouched:
1. Scale the AWS services to 0.
2. Start the old host as before. Its bots poll, and starting to poll **removes the webhook**, so
   Telegram delivers to the old host again.
3. Verify the same checklist against the old host. Done. Nothing was lost.

**B. After the clinic has worked on AWS for a while** (data written to RDS must come back):
1. Scale the AWS `web`/`bots`/`worker` to 0, so nothing more is written.
2. Export RDS to an encrypted SQLite file with the same tool, the other way round:
   ```bash
   aws ecs run-task … --overrides '{"containerOverrides":[{"name":"migrate","command":["python","-m","zenflow.move_data","--from","app","--to","s3://<media_bucket>/migration/rollback.db.enc"]}]}'
   ```
   Go on only with `"ok": true`.
3. On the old host: move the current `data/zenflow.db` aside (do not delete it), then
   ```bash
   aws s3 cp s3://<media_bucket>/migration/rollback.db.enc data/rollback.db.enc
   python -m zenflow.file_crypto decrypt data/rollback.db.enc --out data/zenflow.db
   aws s3 sync s3://<media_bucket>/media/ data/media/
   ```
4. Start the old host (polling removes the webhook) and run the verification checklist.

`tests/integration/test_move_data.py` runs rollback B's path (Postgres → SQLite, through S3,
encrypted) and the cut-over (SQLite → a fresh Postgres) on every PR.

## Timings to expect

| Step | Synthetic clinic (drill) | Expect for a real clinic |
|---|---|---|
| Final backup + drill | < 1 s | seconds |
| Upload | — | seconds (MB) |
| `move_data` into RDS | 0.85 s locally | under a minute |
| Services start + webhooks | — | 2–4 minutes (image pull, health checks) |
| Verification | — | 10 minutes |
| **Total pause** | | **≈ 20–30 minutes** |
