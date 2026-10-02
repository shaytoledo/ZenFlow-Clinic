# Data Protection — Posture, Retention, Access and Erasure (Phase 9.9)

ZenFlow holds **identifiable patient health data**: names and contact details, Telegram/WhatsApp
identities, the patient's own description of their symptoms (intake), a TCM diagnosis, the points
needled and the therapist's notes. This page is the one place that says what we keep, for how long,
how a patient gets a copy of it, and how it is erased.

> **Not legal advice.** The legal summary below is an engineering reading of public sources so the
> code can be built to the right shape. The clinic owner confirms it with counsel — owner decision
> **Q5** in `docs/MASTER_PLAN_EN.md`. Every number that depends on that answer is a setting, not code.

---

## 1. Legal posture (to confirm with counsel — Q5)

**Israel — Protection of Privacy Law, 5741-1981, as amended by Amendment 13 (2025).** Health data
is *"data of special sensitivity"*, the class with the strictest handling rules. The law's
principles include **purpose limitation, data minimisation and data retention**; a data subject may
ask to **review** their data and to **correct or delete** inaccurate, incomplete, unclear or
**outdated** data (s. 14), and the controller must answer whether it accepts the request. The
database regime (registration/notification, security duties, a DPO above thresholds, vendor
management, cross-border transfer rules) applies to a digital collection kept for business.

**Israel — Privacy Protection (Data Security) Regulations, 2017.** Security duties scaled by
database size and sensitivity — access control, logging and log retention (Reg. 10), incident
handling, vendor controls. A clinic database of health data should plan for at least the
"medium" level.

**Israel — Patient's Rights Law, 1996.** Defines *medical information* (which the privacy law
reuses) and requires a caregiver to keep medical records; whether its record-keeping duties and the
retention regulations under it apply to an acupuncture practice is a question for counsel. If they apply, a **statutory minimum retention** overrides an erasure request
for the clinical record.

**EU GDPR** — applies only if the clinic offers its service to people in the EU or monitors them
there. If it does: health data is a special category (Art. 9; basis 9(2)(h) health care), the right
to erasure (Art. 17) yields to a legal obligation to retain (17(3)(b)) and to health-care purposes
(17(3)(c)/(h)), and access (Art. 15) must be answered within a month.

**What the code assumes until Q5 is answered:** a patient can get **everything** we hold
(`patient_export`); an erasure request removes everything that **identifies or reaches** them and
keeps the **de-identified** clinical record (`patient_erasure --mode anonymize`); nothing clinical
is ever deleted automatically (`ZF_RETENTION_CLINICAL_YEARS=0`). That is the safe side of every
reading above — it never destroys a record the law may require, and never keeps an identity it no
longer needs.

### Owner decisions still open (Q5)

| # | Decision | Where it lands |
|---|---|---|
| a | Does the Patient's Rights Law record-keeping duty apply to the clinic? | `ZF_RETENTION_CLINICAL_YEARS` |
| b | The clinical retention period (years after the last appointment) | `ZF_RETENTION_CLINICAL_YEARS` |
| c | The operational retention period (default 2 years) | `ZF_RETENTION_OPERATIONAL_DAYS` |
| d | Database registration / DPO thresholds — does the clinic cross them? | operations, not code |
| e | Patient consent text for Telegram (not end-to-end encrypted — `docs/TRANSPORT.md`) | bot copy |
| f | Is any patient in the EU (GDPR applies)? | this page |

---

## 2. Retention policy per data class

The policy is code — `POLICY` in `zenflow/retention.py` — and `python -m zenflow.retention --policy`
prints it. The sweep (`python -m zenflow.retention --apply`, run daily) applies it.

| Data class | Tables | Kept |
|---|---|---|
| Identity | `patients` | with the clinical record; anonymized on an erasure request |
| Channel identities | `patient_channels` | with the clinical record; deleted on an erasure request |
| Clinical record | `appointments`, `intake_sessions`, `treatment_notes`, `followups` | `ZF_RETENTION_CLINICAL_YEARS` after the last appointment — **0 = forever** (Q5) |
| Audit trail | `audit_log` | as long as the clinical record it describes; purged with it |
| Message metadata | `message_log` | `ZF_RETENTION_OPERATIONAL_DAYS` (default 730) |
| AI-call meters | `ai_calls` | `ZF_RETENTION_OPERATIONAL_DAYS` |
| Dashboard notifications | `notifications` | `ZF_RETENTION_OPERATIONAL_DAYS` once read or resolved; open alerts stay |
| Background jobs | `jobs` | `ZF_RETENTION_OPERATIONAL_DAYS` once done or dead; pending jobs stay |
| Bot conversation state | `bot_persistence` | until the flow ends or times out (`ZF_CONV_TIMEOUT_MINUTES`) |
| Redis (intake history, relay, caches) | Redis | its key TTL — 30 min to 7 days (`docs/DATA_LAYER.md`) |
| API idempotency records | `api_idempotency` | 24 hours (pruned by `web/services/idempotency.py`) |
| Revoked sessions | `revoked_sessions` | until the session would have expired (pruned by `web/session_policy.py`) |

**Minimisation already in place:** message text is never stored (`message_log` is metadata only,
8.3); AI prompts are stored as SHA-256 only (`ZF_AI_DEBUG_PROMPTS` keeps text in dev alone, 8.2);
bot persistence keeps scheduling keys only, never clinical text (`PERSISTED_USER_KEYS`); logs
redact tokens and never carry message bodies (ADR-18).

---

## 3. Right of access — export

```bash
python -m zenflow.patient_export <patient_id> [--out patient.json]
```

One JSON document, one section per data class above, strictly scoped by `patients.id` (a test proves
no other patient's rows can appear). Read-only. Hand the file to the patient over a channel you would
trust with their medical record — it **is** their medical record. To send it anywhere, add
`--encrypt` (§5): only `patient.json.enc` is written, and the key travels separately.

---

## 4. Right to erasure — `zenflow.patient_erasure`

```bash
python -m zenflow.patient_erasure <patient_id> --reason "DSAR 2026-10-01"           # plan only
python -m zenflow.patient_erasure <patient_id> --reason "DSAR 2026-10-01" --apply   # do it
```

Nothing changes without `--apply`; the plan prints the row counts per class and the manual steps.
`--reason` is required and is recorded in the audit trail.

### `--mode anonymize` (default) — an erasure request inside the retention period

| Class | What happens |
|---|---|
| `patients` | name → "Erased patient"; phone, email, notes, legacy id cleared; the row and its id stay |
| `patient_channels` | **deleted** — nothing can message the patient; a new message from the same account starts a new, unrelated patient |
| `appointments` | name, phone, email cleared; date, time, therapist, summary stay (clinical) |
| `intake_sessions`, `treatment_notes`, `followups` | **kept** — the de-identified clinical record |
| `message_log`, `notifications` | deleted (operational; carry identity) |
| `ai_calls` | meters kept; any dev-only prompt/response copy cleared |
| `jobs` | pending jobs for their appointments (follow-ups, confirmations) deleted |
| `bot_persistence`, Redis | conversation state, intake history and relay keys under their Telegram id deleted |
| `audit_log` | identity fields inside before/after values overwritten with `[erased]`; clinical values and the trail itself stay |

### `--mode purge` — the record's retention period is over

Every row about the patient is deleted, clinical record and audit trail included. It is refused
while the clinical retention period runs — and therefore **always**, while `ZF_RETENTION_CLINICAL_YEARS`
is 0 — unless `--override-retention` records that a person (with counsel) decided otherwise. The
retention sweep uses this mode for patients whose period has lapsed.

### The audit trail and erasure

`audit_log` is append-only by two SQLite triggers (8.1). Erasure is the single sanctioned exception:
inside **one transaction** it lifts the guard, rewrites (anonymize) or deletes (purge) the
patient's audit rows, and recreates the guard; any failure rolls back everything, guard included
(tested). Then it records `patient.erased` / `patient.purged` — actor `system/patient_erasure`, the
mode, the reason and the counts, and **nothing that identifies the patient**. The triggers protect
the trail from the application; anyone who can run this command already holds the database file.

### What a person still has to do (the command lists it)

- **Google Calendar**: the therapist's events carry the patient's name in the title — delete or
  rename the listed event ids.
- **Backups**: existing backups still hold the patient until they age out of the rotation; restoring
  one means re-running the erasure.
- **The patient's own devices / Telegram history**: outside the clinic's control.

---

## 5. Encryption and access

- **At rest**: the SQLite file lives on the clinic host — full-disk encryption on that host is the
  control today; RDS encryption at rest and encrypted snapshots are Phase 12 (`docs/TRANSPORT.md`).
- **Backups and exports that leave the host are encrypted first** (`zenflow/file_crypto.py`):
  `python -m zenflow.db_backup --encrypt` takes the backup **in memory** and writes only
  `<db>.bak-<stamp>.enc`; `python -m zenflow.patient_export <id> --out f.json --encrypt` writes only
  `f.json.enc`. Fernet (authenticated — a wrong key or a damaged file is refused, never
  half-decrypted), keyed by **`BACKUP_ENCRYPTION_KEY`**, its own secret (≥ 32 chars, different from
  the session and token keys). Restore with `python -m zenflow.file_crypto decrypt <file>.enc --out
  <file>`. **Keep a copy of the key off the host** — without it an encrypted backup is lost.
  Stored Google tokens are Fernet-encrypted (`TOKEN_ENCRYPTION_KEY`, rotation in `docs/SECRETS.md`).
- **File permissions** (A10, SF-020): the database, its WAL/SHM files, every backup, restore and
  export are owner-only (`0600`) from creation; a database found looser is tightened on open.
- **In transit**: HTTPS / `rediss://` everywhere outside dev (ADR-14, `docs/TRANSPORT.md`).
- **Minimum-necessary access**: every dashboard and API route is scoped to the signed-in therapist's
  own patients (9.1, `docs/AUTHZ.md`, `tests/security/test_tenant_isolation.py`); the export and
  erasure commands need shell access to the host, not a web login.
