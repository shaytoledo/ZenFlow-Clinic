# Backups and Disaster Recovery (Phase 12.2.7)

How the clinic's data is copied, how much can be lost (**RPO**), how long recovery takes (**RTO**),
and the drill that proves a backup restores.

## RPO / RTO per deployment

| Deployment | What is copied, how often | RPO (data that can be lost) | RTO (back in service) |
|---|---|---|---|
| **Today: one host, SQLite** | Automatic encrypted backup every `ZF_BACKUP_HOURS` (24), keeping `ZF_BACKUP_KEEP` (14) beside the database. Copy one off the host daily: same disk = same fate. | **≤ 24 h** (≤ the period set) | **≈ 15 min**: stop the app, `python -m zenflow.file_crypto decrypt <backup>.enc --out data/zenflow.db`, start |
| **AWS, Option A** (`docs/INFRA.md`) | RDS: a daily snapshot + transaction logs every 5 min, kept 14 days (`db_backup_retention_days`); S3 media versioned; snapshot taken on deletion in prod | **≤ 5 min** (point-in-time recovery) | **≈ 1 h**: restore to a new instance (20–40 min for a small database), point `ZF_DB_URL` at it, redeploy |
| **AWS, Option B** (one EC2 server) | Daily EBS snapshot + the same automatic encrypted backup | ≤ 24 h | ≈ 30–60 min: a new instance from the snapshot |

**Redis holds no record of truth.** Relay routing (7 days), intake histories (30 min) and caches are
rebuilt or expire; losing Redis means a patient mid-intake starts that question again. It is not
backed up on purpose.

**The keys are part of the backup.** An encrypted backup is useless without
`BACKUP_ENCRYPTION_KEY`, and the stored Google tokens are useless without `TOKEN_ENCRYPTION_KEY`.
Keep both in the owner's password manager, **off the machine that holds the backups**.

## The tools

```bash
python -m zenflow.db_backup [--encrypt]      # SQLite: online backup API; Postgres: pg_dump --format=custom
python -m zenflow.restore_drill [--keep]     # back up → restore into a scratch copy → compare every table
python -m zenflow.restore_drill --compare-url postgresql://…   # compare a restored RDS instance with the live one
python -m zenflow.periodic db.backup         # take the automatic backup now if it is due
```

- Every backup file is owner-only from its first byte (A10). `--encrypt` encrypts in memory, so the
  plaintext never touches disk.
- On Postgres the password reaches `pg_dump` / `pg_restore` through `PGPASSWORD`, never through the
  command line (visible in `ps`). `pg_dump` must be at least the server's major version.
- The automatic backup is a `zenflow.periodic` task, run once across all workers (ADR-48). On
  Postgres it does nothing: RDS backs itself up.

## The drill — performed 2026-10-03

`zenflow.restore_drill` fingerprints the live database: for every table, the row count and a SHA-256
over its rows in primary-key order, plus the Alembic revision. It then takes a backup **with the
production code path**, restores it into a scratch copy (SQLite after `PRAGMA integrity_check`;
Postgres with `pg_restore` into a new database, dropped afterwards), fingerprints the copy and
compares. Any difference fails it (exit 1). `tests/integration/test_restore_drill.py` proves it
notices one lost row and one changed value.

Both runs used the same synthetic clinic (no real patient data): 3 therapists, 40 patients, 200
appointments with intakes and treatment notes, 200 audit rows, 40 queued jobs, the 26 acupoints.

| Run | Database | Backup | Backup took | Restore took | Tables | Rows | Result |
|---|---|---|---|---|---|---|---|
| SQLite (Windows) | file | 426 KB, encrypted | 0.05 s | 0.02 s | 22 | 952 | **identical** |
| Postgres 16 (WSL) | `zenflow_drill` | 67 KB `pg_dump`, encrypted | 0.12 s | 1.38 s | 22 | 949 | **identical**; the scratch database was dropped |

Both runs: Alembic revision `0001`, and the backup file was deleted afterwards. CI repeats the
drill on every PR (`test_restore_drill.py` runs in `quality` and on Postgres in `postgres-suite`).

## The AWS drill — to run once after the go-ahead, then every quarter

1. RDS console → the instance → *Restore to point in time* → a time 10 minutes ago, new identifier
   `zenflow-prod-drill`, same subnet group, same security group.
2. From a one-off task in the app's security group (the `migrate` task definition with the command
   overridden), run
   `python -m zenflow.restore_drill --compare-url "postgresql+psycopg://zenflow@<drill endpoint>:5432/zenflow?sslmode=require"`
   with `ZF_DB_PASSWORD` from the RDS secret. The differences are exactly the writes made in the
   last 10 minutes, if any.
3. Write down how long step 1 took (that is the RTO), then delete `zenflow-prod-drill`.
4. S3: restore one acupoint image's previous version, to check that versioning works.
