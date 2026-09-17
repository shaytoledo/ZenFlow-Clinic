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

The **only exception** is `save_appointment()`, which uses an explicit `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` block to keep the `appointments` + `intake_sessions` inserts atomic. `IMMEDIATE` takes the write lock up front, so the "is this hour still free?" check and the insert cannot be interleaved with another booking (BOT_AUDIT B4).

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
    -- added by migrations in init_db():
    -- language      TEXT DEFAULT 'en'            -- UI language, 'en' | 'he'
    -- ui_prefs      TEXT                         -- JSON, whitelisted UI preferences (Phase 4.2d)
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
| `ui_prefs` | JSON object, e.g. `{"point_density": "compact"}`. Only keys and values in `therapist_repo.UI_PREF_CHOICES` are ever written; anything else reads as the default. API: `GET`/`PATCH /api/my/preferences` |

**Write operations:**
- `_register_web_therapist()` → `INSERT` with `telegram_id=0, active=0`
- `_register_therapist_to_db()` (bot) → `UPDATE` sets `telegram_id`, `active=1`
- Password/google_id updates on sign-in if account was created with the other method

---

### Table: `acupoints` (Phase 4.3a)

Reference data for the treatment page. Created by `init_db()`, which fills an empty table from
`zenflow/seed_data/acupoints.json`; later changes to that file are applied with
`python -m zenflow.seed acupoints [--dry-run]` (idempotent upsert by code; rows not in the file
are kept). Read through `web/repositories/acupoint_repo.py` and served by `GET /api/acupoints`.

```sql
CREATE TABLE IF NOT EXISTS acupoints (
    code              TEXT PRIMARY KEY,           -- WHO alphanumeric code: LI4, KI3, … (YINTANG for the extra point)
    aliases           TEXT NOT NULL DEFAULT '[]', -- JSON list of other spellings, normalised: ["KD3"]
    name_pinyin       TEXT NOT NULL DEFAULT '',
    name_cn           TEXT NOT NULL DEFAULT '',   -- Han characters (simplified)
    name_en           TEXT NOT NULL DEFAULT '',   -- usual English translation
    channel           TEXT NOT NULL DEFAULT '',   -- English channel name; picks the card colour
    location          TEXT NOT NULL DEFAULT '',
    actions           TEXT NOT NULL DEFAULT '',
    needle_depth      TEXT NOT NULL DEFAULT '',
    needle_angle      TEXT NOT NULL DEFAULT '',
    contraindications TEXT NOT NULL DEFAULT '[]', -- JSON list; only "pregnancy" today
    translations      TEXT NOT NULL DEFAULT '{}', -- JSON {"he": {"name", "channel", "location", "actions"}}
    source            TEXT NOT NULL DEFAULT '',
    licence           TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL
);
```

Deviation from the plan's column list: `aliases` (the AI and old notes use non-WHO codes such as
`KD3`) and `translations` (the page is bilingual) were added.

### Table: `acupoint_images` (Phase 4.3b)

Images of a point, added only by `python -m zenflow.ingest_images <folder>` (which refuses any
file without a credit and a licence). The bytes live in the media store (`zenflow/storage.py`);
the row keeps the keys and the attribution.

```sql
CREATE TABLE IF NOT EXISTS acupoint_images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    point_code    TEXT NOT NULL REFERENCES acupoints(code) ON DELETE CASCADE,
    storage_key   TEXT NOT NULL UNIQUE,     -- acupoints/<code>/<sha256[:16]>.webp (≤1600 px)
    thumb_key     TEXT NOT NULL,            -- …-thumb.webp (≤320 px)
    kind          TEXT NOT NULL DEFAULT 'diagram' CHECK (kind IN ('diagram', 'photo', '3d')),
    width         INTEGER NOT NULL,         -- of the web copy
    height        INTEGER NOT NULL,
    sha256        TEXT NOT NULL,            -- of the original file; makes re-ingesting idempotent
    original_name TEXT NOT NULL DEFAULT '',
    credit        TEXT NOT NULL,
    licence       TEXT NOT NULL,
    licence_url   TEXT NOT NULL DEFAULT '',
    source_url    TEXT NOT NULL DEFAULT '',
    is_primary    INTEGER NOT NULL DEFAULT 0, -- the first image ingested for a point
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acupoint_images_code ON acupoint_images(point_code);
```

`GET /api/acupoints` lists a point's images (links, kind, size, attribution — never the storage
keys) only when `ZF_POINT_IMAGES=1`.

### Table: `leases` (Phase 3.1)

```sql
CREATE TABLE IF NOT EXISTS leases (
    name        TEXT PRIMARY KEY,  -- e.g. 'generation:42'
    holder      TEXT NOT NULL,     -- e.g. 'job-17'
    expires_at  TEXT NOT NULL      -- canonical UTC
);
```

Named, expiring locks (`zenflow/leases.py`). Acquire is one `INSERT … ON CONFLICT DO UPDATE …
WHERE holder matches OR expired`, so two processes cannot both win; expiry frees a lease whose
holder crashed. Used for `generation:{appointment_id}` so two generations never overlap.

### Table: `bot_persistence` (Phase 2.3)

```sql
CREATE TABLE IF NOT EXISTS bot_persistence (
    kind        TEXT NOT NULL,   -- 'conv' (conversation state) | 'user' (user_data)
    name        TEXT NOT NULL,   -- conversation name ('patient'); '' for user rows
    key         TEXT NOT NULL,   -- JSON [chat_id, user_id] for conv; telegram user id for user
    value_json  TEXT NOT NULL,   -- state int, or whitelisted user_data
    updated_at  TEXT NOT NULL,   -- canonical UTC
    PRIMARY KEY (kind, name, key)
);
```

Written by `bot/persistence.py::SqlitePersistence` so a restart does not drop a patient's
half-finished booking. Only `PERSISTED_USER_KEYS` are stored (therapist choice, selected
day/time/week, intake counter, flow marker, and the cancel list reduced to id/date/time/therapist/
calendar id) — never intake answers, summaries or names. Rows older than
`ZF_CONV_TIMEOUT_MINUTES` are not restored (the therapist choice is). An ended conversation deletes
its row.

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

**Indexes:**

```sql
CREATE UNIQUE INDEX IF NOT EXISTS ux_appointments_active_slot
    ON appointments(therapist_id, date, time) WHERE status='active';
```

One active appointment per therapist per hour (BOT_AUDIT B4). It is *partial* on purpose:
cancelled rows are kept for clinical history and must not keep holding their slot. If an existing
database already contains a double booking the index cannot be created — `init_db()` logs each
offending slot with its appointment ids and carries on; cancel the extras and restart to get the
index. Until then only the bot is protected — `save_appointment()` re-checks the hour itself — while
the dashboard's manual booking, which relies on the index alone, could still create a clash.

**Write operations:**
- `save_appointment()` → `INSERT` (inside explicit `BEGIN IMMEDIATE`/`COMMIT` with
  `intake_sessions`; raises `SlotTaken` when the hour is already booked)
- `set_gcal_event_id(id, event_id)` → `UPDATE` after the calendar work, which happens *after* the
  row exists so a booking is never lost to a calendar failure
- `cancel_appointment(id: int)` → `UPDATE SET status='cancelled'` (frees the slot for the index)

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
- `web/routers/api/availability.py POST /api/availability` → `INSERT` (therapist creates slot via FullCalendar drag)
- `web/routers/api/availability.py DELETE /api/availability/{id}` → `DELETE` (therapist removes slot)
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

---

## Database file location (Phase 0.3)

`bot/db.py::db_path()` resolves the SQLite file from the `ZENFLOW_DB_PATH` environment variable,
falling back to `data/zenflow.db`. It is read on every `get_db()` call, and a thread whose cached
connection points at a different file reconnects — this is what lets the test harness give every
test a fresh database even though `bot/config.py` opens the DB at import time. `close_db()` closes
the current thread's connection. Tests assert they never touch the real file.

---

## `jobs` — durable task queue (Phase 1.2, ADR-20)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | job id |
| `name` | TEXT | handler name, e.g. `followup.send_step1` |
| `payload_json` | TEXT | JSON object handed to the handler |
| `run_at` | TEXT | canonical UTC; claimable when `run_at <= now` |
| `status` | TEXT | `pending` → `running` → `done` \| `dead` \| `cancelled` |
| `attempts` / `max_attempts` | INTEGER | attempt counter (incremented on claim) / retry budget (default 5) |
| `last_error` | TEXT | last failure message (truncated) |
| `idempotency_key` | TEXT UNIQUE | enqueueing the same key again returns the existing job |
| `locked_by` / `locked_at` | TEXT | claiming worker + time; a `running` job whose lock is older than 10 min is reclaimable (worker died) |
| `created_at` / `updated_at` / `completed_at` | TEXT | canonical UTC |

Claim is one atomic `UPDATE … WHERE id IN (SELECT … LIMIT n) RETURNING *`, so two workers never
take the same job. Backoff: 60 s × 2^(attempt−1). Access only through `zenflow/queue.py`.
