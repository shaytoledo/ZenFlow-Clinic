# ZenFlow — SQLite Database Reference

> File: `data/zenflow.db`
> Mode: WAL (Write-Ahead Log)
> Connections: Thread-local singletons (`bot/db.py`)

---

## Connection Model

### Thread-Local Connections

```python
# bot/db.py
_local = threading.local()

def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(
            str(_DB_PATH),
            check_same_thread=False,
            timeout=30.0,
            isolation_level=None   # ← autocommit
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn
```

One connection per OS thread. The asyncio thread pool can reuse threads, so each thread gets its connection lazily on first access and reuses it for all subsequent calls in that thread.

### Why `isolation_level=None` (Autocommit)

Python's default `isolation_level=""` auto-issues a `BEGIN` before every `INSERT`/`UPDATE`/`DELETE`. If a write fails and raises an exception, the connection is left with an **open transaction**. When the asyncio thread pool reuses that thread for the next request, calling `execute()` returns `SQLITE_LOCKED` **immediately** — bypassing `busy_timeout` entirely.

Setting `isolation_level=None` eliminates all implicit `BEGIN` statements. Every statement auto-commits instantly. `conn.commit()` calls throughout the codebase become harmless no-ops.

The **only exception** is `save_appointment()`, which uses an explicit `BEGIN`/`COMMIT`/`ROLLBACK` block to keep the `appointments` + `intake_sessions` inserts atomic.

### WAL Mode

`PRAGMA journal_mode=WAL` allows:
- **Multiple concurrent readers** (bot + web process can both `SELECT` simultaneously)
- **One writer at a time** (`INSERT`/`UPDATE`/`DELETE` are serialised)
- Readers do not block the writer; the writer does not block readers

### `busy_timeout`

`PRAGMA busy_timeout=30000` (30 seconds). When a write is blocked by another writer (`SQLITE_BUSY`), SQLite retries for up to 30 seconds before returning an error. This handles bot + web writing at the same instant.

---

## Schema

### Table: `therapists`

```sql
CREATE TABLE IF NOT EXISTS therapists (
    id               TEXT PRIMARY KEY,            -- "t1", "t2", ...
    name             TEXT NOT NULL,
    telegram_id      INTEGER DEFAULT 0,           -- 0 until bot activation
    email            TEXT,                        -- nullable
    password_hash    TEXT,                        -- "{salt}:{hex}" PBKDF2-SHA256-260k, NULL for Google-only
    google_id        TEXT,                        -- Google OAuth "sub" claim, nullable
    calendar_name    TEXT DEFAULT 'ZenFlow Availability',
    active           INTEGER DEFAULT 0,           -- 0=false, 1=true
    created_at       TEXT DEFAULT (datetime('now'))
);
```

**Key column notes:**

| Column | Details |
|---|---|
| `id` | Text string assigned at registration (`t1`, `t2`, … incremented from max existing id). |
| `telegram_id` | `0` for web-registered therapists who haven't completed bot activation. Non-zero after bot activation |
| `active` | `0` until therapist sends bot activation code. Only `active=1` therapists appear in `THERAPIST_MAP` and are offered to patients |
| `password_hash` | `NULL` for Google-only accounts. Format: `"{64-hex-salt}:{64-hex-hash}"` |
| `google_id` | Google's unique stable identifier for the user (from `id_token.sub`) |
| `calendar_name` | Name of the therapist's Google Calendar that holds "✅ Available" events |

**Write operations:**
- `_register_web_therapist()` → `INSERT` with `telegram_id=0, active=0`
- `_register_therapist_to_db()` (bot) → `UPDATE` sets `telegram_id`, `active=1`
- Password/google_id updates on sign-in if account was created with the other method

---

### Table: `appointments`

```sql
CREATE TABLE IF NOT EXISTS appointments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id          INTEGER NOT NULL,     -- Telegram user_id
    patient_name        TEXT    NOT NULL,
    therapist_id        TEXT    NOT NULL,     -- → therapists.id
    date                TEXT    NOT NULL,     -- "YYYY-MM-DD"
    time                TEXT    NOT NULL,     -- "HH:MM"
    status              TEXT    DEFAULT 'active',  -- "active" | "cancelled"
    gcal_apt_event_id   TEXT,                -- Google Calendar event id, or "local_..." sentinel, or NULL
    summary             TEXT,                -- AI-generated clinical summary (or fallback text)
    created_at          TEXT    DEFAULT (datetime('now'))
);
```

**Key column notes:**

| Column | Details |
|---|---|
| `status` | Only `'active'` or `'cancelled'`. Never deleted — records preserved for clinical history |
| `gcal_apt_event_id` | Google Calendar event ID if Google Calendar is connected. Starts with `"local_"` if using local SQLite availability. `NULL` if booking failed |
| `summary` | AI-generated clinical summary from the intake questionnaire. Falls back to `"Patient opted to skip the intake questionnaire."` or `"Intake completed — see conversation history for details."` |

**Write operations:**
- `save_appointment()` → `INSERT` (inside explicit `BEGIN`/`COMMIT` with `intake_sessions`)
- `cancel_appointment(id: int)` → `UPDATE SET status='cancelled'`

**Soft delete rationale:** Cancelled appointments are preserved so the web dashboard can show complete patient history. Clinical records must never be destroyed.

---

### Table: `intake_sessions`

```sql
CREATE TABLE IF NOT EXISTS intake_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id  INTEGER NOT NULL,   -- → appointments.id
    patient_id      INTEGER NOT NULL,
    therapist_id    TEXT    NOT NULL,   -- → therapists.id
    history_json    TEXT,               -- JSON array of {role, content} dicts
    created_at      TEXT    DEFAULT (datetime('now'))
);
```

**Key column notes:**

| Column | Details |
|---|---|
| `history_json` | Full conversation as `[{"role": "user"|"assistant", "content": "..."}]`. Written once at appointment save time. Never updated |

**Relationship:** 1:1 with `appointments` (one intake record per appointment). The join is `appointments.id = intake_sessions.appointment_id`.

**Write operations:**
- `save_appointment()` → `INSERT` in the same transaction as the appointment row

---

### Table: `availability`

```sql
CREATE TABLE IF NOT EXISTS availability (
    id           TEXT PRIMARY KEY,   -- uuid4().hex
    therapist_id TEXT NOT NULL,      -- → therapists.id
    start_dt     TEXT NOT NULL,      -- "YYYY-MM-DDTHH:MM:SS"
    end_dt       TEXT NOT NULL       -- "YYYY-MM-DDTHH:MM:SS"
);
```

**Purpose:** Stores local availability slots when Google Calendar is not connected for a therapist.

**Key column notes:**

| Column | Details |
|---|---|
| `id` | UUID hex string (not sequential integer) — safe for concurrent inserts |
| `start_dt` / `end_dt` | ISO datetime without timezone. Interpreted as clinic local time (Asia/Jerusalem) |

**Slot management:**

When a patient books a slot, the covering availability row is deleted and re-inserted as one or two shorter rows:

```
Before: [09:00 — 13:00]   patient books 11:00
After:  [09:00 — 11:00]   (left fragment)
        [12:00 — 13:00]   (right fragment)
```

When a patient cancels, a new 1-hour row is inserted (no merging with adjacent slots).

**Write operations:**
- `web/app.py POST /api/availability` → `INSERT` (therapist creates slot via FullCalendar drag)
- `web/app.py DELETE /api/availability/{id}` → `DELETE` (therapist removes slot)
- `availability._remove_hour_from_local()` → `DELETE` + `INSERT` (booking)
- `availability._add_hour_to_local()` → `INSERT` (cancellation restore)

---

### Table: `treatment_notes`

```sql
CREATE TABLE IF NOT EXISTS treatment_notes (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id          INTEGER NOT NULL UNIQUE,  -- → appointments.id (1:1)
    patient_id              INTEGER NOT NULL,
    tcm_pattern             TEXT,             -- e.g. "Liver Qi Stagnation with Blood Deficiency"
    treatment_principles    TEXT,             -- e.g. "Move Liver Qi, nourish Blood, calm Shen"
    diagnosis_certainty     INTEGER DEFAULT 0, -- 0–100 (AI confidence %)
    ai_suggested_points     TEXT,             -- JSON: [{"code":"ST36","rationale":"..."}]
    ai_recommendations      TEXT,             -- JSON: {"diet":"...","sleep":"...","exercise":"...","stress":"..."}
    tongue_observation      TEXT,             -- Therapist-entered free text
    pulse_observation       TEXT,             -- Therapist-entered free text
    session_notes           TEXT,             -- Therapist-entered free text
    used_points             TEXT,             -- JSON array: ["ST36", "LR3"]
    recommendations_sent_at TEXT,             -- ISO datetime or NULL
    completed_at            TEXT,             -- ISO datetime — set by "Complete Session" button
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now'))
);
```

**JSON column formats:**

`ai_suggested_points`:
```json
[
  {"code": "LR3", "rationale": "Primary point to move Liver Qi"},
  {"code": "SP6", "rationale": "Nourishes Blood and Yin"},
  {"code": "ST36", "rationale": "Strengthens Qi"}
]
```

`ai_recommendations`:
```json
{
  "diet":     "Favour warm, cooked foods with dark leafy greens.",
  "sleep":    "Establish a consistent sleep schedule.",
  "exercise": "Gentle movement such as Tai Chi.",
  "stress":   "Daily 10-minute breathing practice."
}
```

`used_points`: `["ST36", "LR3", "SP6"]`

**UPSERT pattern:**

`save_treatment_notes()` uses `INSERT ... ON CONFLICT(appointment_id) DO UPDATE SET ... COALESCE(excluded.field, existing_field)`. The `COALESCE` ensures that partial saves (e.g. only AI diagnosis on booking, then therapist adds tongue/pulse later) never overwrite existing data with `NULL`.

**Write operations:**
- `save_treatment_notes(appointment_id, user_id, tcm_dict)` → UPSERT
  - Called once by `schedule.py` after intake (with AI-generated TCM data)
  - Called again by `web/routers/api/treatment.py` `POST /complete` (with therapist-entered data + `completed_at`)

---

## Migrations

`init_db()` runs inline migrations for columns added after initial schema deployment:

```python
_migrations = [
    "ALTER TABLE treatment_notes ADD COLUMN diagnosis_certainty INTEGER DEFAULT 0",
    "ALTER TABLE treatment_notes ADD COLUMN completed_at TEXT",
]
for migration in _migrations:
    try:
        conn.execute(migration)
        conn.commit()
    except Exception:
        pass  # Column already exists — safe to ignore
```

To add a new column: append to `_migrations`. It will be applied on the next startup for any database that doesn't have the column yet.

---

## Backup and Inspection

```bash
# Read-only inspection
sqlite3 data/zenflow.db ".tables"
sqlite3 data/zenflow.db "SELECT * FROM appointments ORDER BY date DESC LIMIT 10;"
sqlite3 data/zenflow.db "SELECT * FROM treatment_notes ORDER BY updated_at DESC LIMIT 5;"
sqlite3 data/zenflow.db "SELECT * FROM therapists;"
sqlite3 data/zenflow.db "SELECT count(*) FROM appointments WHERE status='active';"

# Backup
cp data/zenflow.db data/zenflow.db.backup

# WAL checkpoint (flush WAL to main DB file)
sqlite3 data/zenflow.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

> **Warning:** Do not open `data/zenflow.db` in PyCharm's Database plugin while the app is running. PyCharm holds the `.db-shm` file open, which can block writes and cause `SQLITE_LOCKED` errors.

