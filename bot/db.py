"""
db.py — SQLite singleton for ZenFlow.

- One connection per thread (threading.local)
- WAL mode for concurrent reads from bot + web processes
- Auto-creates tables and runs schema migrations on first call to init_db()
"""

import contextlib
import logging
import os
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "zenflow.db"
_local = threading.local()


def db_path() -> Path:
    """Resolve the SQLite file: `ZENFLOW_DB_PATH` env var if set, else data/zenflow.db.

    Read on every call (not cached at import) so the test harness can point each test at a
    throw-away file even though bot.config opens the database at import time.
    """
    override = os.environ.get("ZENFLOW_DB_PATH") or _settings_db_path()
    return Path(override) if override else _DEFAULT_DB_PATH


def _settings_db_path() -> str | None:
    """`.env`-sourced value (pydantic-settings never exports to os.environ — review fix)."""
    try:
        from zenflow.settings import get_settings

        return get_settings().zenflow_db_path or None
    except Exception:
        return None


def close_db() -> None:
    """Close and forget this thread's cached connection (tests; graceful shutdown)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        with contextlib.suppress(Exception):
            conn.close()
    _local.conn = None
    _local.path = None


_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS therapists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    telegram_id INTEGER DEFAULT 0,
    email TEXT,
    password_hash TEXT,
    google_id TEXT,
    calendar_name TEXT DEFAULT 'ZenFlow Availability',
    active INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    patient_name TEXT NOT NULL,
    therapist_id TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    gcal_apt_event_id TEXT,
    summary TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS intake_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    patient_id INTEGER NOT NULL,
    therapist_id TEXT NOT NULL,
    history_json TEXT,
    created_at TEXT DEFAULT (datetime('now'))
)""",
    """CREATE TABLE IF NOT EXISTS availability (
    id TEXT PRIMARY KEY,
    therapist_id TEXT NOT NULL,
    start_dt TEXT NOT NULL,
    end_dt TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS treatment_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL UNIQUE,
    patient_id INTEGER NOT NULL,
    tcm_pattern TEXT,
    treatment_principles TEXT,
    diagnosis_certainty INTEGER DEFAULT 0,
    ai_suggested_points TEXT,
    ai_recommendations TEXT,
    tongue_observation TEXT,
    pulse_observation TEXT,
    session_notes TEXT,
    used_points TEXT,
    recommendations_sent_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
)""",
]


def get_db() -> sqlite3.Connection:
    """Return a thread-local SQLite connection (WAL mode, Row factory, autocommit)."""
    path = db_path()
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) != path:
        # The configured file changed (test harness) — a pooled thread must not keep
        # writing to the old one.
        close_db()
        conn = None
    if conn is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None = autocommit: Python never issues an implicit BEGIN,
        # so there are no stale open transactions when a thread is reused from the pool.
        conn = sqlite3.connect(
            str(path), check_same_thread=False, timeout=30.0, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
        _local.path = path
    return conn


def init_db() -> None:
    """Create all tables and run schema migrations on every startup."""
    conn = get_db()
    # Use individual execute() calls (not executescript) so busy_timeout is respected
    for stmt in _SCHEMA_STMTS:
        conn.execute(stmt)
    conn.commit()

    # Migrations: add columns that may be missing from existing databases
    _migrations = [
        "ALTER TABLE treatment_notes ADD COLUMN diagnosis_certainty INTEGER DEFAULT 0",
        "ALTER TABLE treatment_notes ADD COLUMN completed_at TEXT",
        "ALTER TABLE treatment_notes ADD COLUMN followup_rating INTEGER",
        "ALTER TABLE treatment_notes ADD COLUMN followup_sent_at TEXT",
        "ALTER TABLE treatment_notes ADD COLUMN therapist_diagnosis TEXT",
        "ALTER TABLE treatment_notes ADD COLUMN therapist_notes TEXT",
        "ALTER TABLE appointments ADD COLUMN source TEXT DEFAULT 'telegram'",
        "ALTER TABLE appointments ADD COLUMN patient_phone TEXT",
        # Manual therapist-entered patient feedback (fallback when no Telegram)
        "ALTER TABLE treatment_notes ADD COLUMN manual_feedback_rating INTEGER",
        "ALTER TABLE treatment_notes ADD COLUMN manual_feedback_notes TEXT",
        # Full multi-turn follow-up conversation stored as JSON
        "ALTER TABLE treatment_notes ADD COLUMN followup_conversation TEXT",
        # Per-therapist UI language preference
        "ALTER TABLE therapists ADD COLUMN language TEXT DEFAULT 'en'",
        # Pending lifestyle recommendations to auto-send 24h after session completion
        "ALTER TABLE treatment_notes ADD COLUMN pending_recommendations TEXT",
        "ALTER TABLE treatment_notes ADD COLUMN pending_rec_send_at TEXT",
        # Stage-2 pipeline state: NULL | GENERATING | COMPLETED | FAILED
        "ALTER TABLE treatment_notes ADD COLUMN points_status TEXT",
        # Email contact for manual patients (used by SMTP fallback)
        "ALTER TABLE appointments ADD COLUMN patient_email TEXT",
        # Per-therapist UI preferences, a whitelisted JSON object (Phase 4.2d, therapist_repo)
        "ALTER TABLE therapists ADD COLUMN ui_prefs TEXT",
    ]
    # Per-therapist Google OAuth2 tokens (Calendar + Gmail) — Fernet-encrypted at rest
    conn.execute("""CREATE TABLE IF NOT EXISTS google_tokens (
        therapist_id TEXT PRIMARY KEY,
        encrypted_token TEXT NOT NULL,
        scopes TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.commit()
    # Notifications table — therapist-scoped alerts shown in the bell dropdown
    conn.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        therapist_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        severity TEXT DEFAULT 'info',
        title TEXT NOT NULL,
        body TEXT,
        appointment_id INTEGER,
        patient_id INTEGER,
        patient_name TEXT,
        persistent INTEGER DEFAULT 0,
        read_at TEXT,
        resolved_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_therapist_unread ON notifications(therapist_id, read_at)"
    )
    conn.commit()
    # Durable task queue (Phase 1.2, ADR-20) — zenflow/queue.py. Timestamps are canonical UTC.
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        run_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 5,
        last_error TEXT,
        idempotency_key TEXT UNIQUE,
        locked_by TEXT,
        locked_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, run_at)")
    conn.commit()
    # Named expiring locks (Phase 3.1) — zenflow/leases.py. One generation per appointment.
    conn.execute("""CREATE TABLE IF NOT EXISTS leases (
        name TEXT PRIMARY KEY,
        holder TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )""")
    conn.commit()
    # Bot persistence (Phase 2.3) — bot/persistence.py. Conversation states and whitelisted
    # scheduling keys only; never clinical free text.
    conn.execute("""CREATE TABLE IF NOT EXISTS bot_persistence (
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        key TEXT NOT NULL,
        value_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (kind, name, key)
    )""")
    conn.commit()
    for migration in _migrations:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass  # Column already exists — safe to ignore
    _create_active_slot_index(conn)
    _create_acupoints(conn)
    _create_message_log(conn)
    _create_followups(conn)
    _create_api_tables(conn)


def _create_api_tables(conn: sqlite3.Connection) -> None:
    """Booking API clients and idempotency keys (7.3), the audit trail (8.1), the AI meter (8.2)."""
    from web import session_policy
    from web.repositories import api_client_repo
    from web.services import ai_calls, audit, idempotency

    api_client_repo.create_schema(conn)
    idempotency.create_schema(conn)
    session_policy.create_schema(conn)
    audit.create_schema(conn)
    ai_calls.create_schema(conn)


def _create_patients(conn: sqlite3.Connection) -> None:
    """Patients and their channels (Phase 7.2), plus the one-time move to internal ids.

    Runs after every table that stores a patient id exists and before the follow-up backfill,
    which reads `patient_channels`. A failed migration raises: the process must not start with
    old and new ids mixed (the database is left unchanged, and a backup was taken first).
    """
    from web.repositories import patient_repo

    patient_repo.create_schema(conn)
    patient_repo.migrate_legacy_ids(conn, db_path())


def _create_message_log(conn: sqlite3.Connection) -> None:
    """Outbound patient messages (plan 8.3, started in Phase 6.6)."""
    from web.repositories import message_log_repo

    message_log_repo.create_schema(conn)


def _create_followups(conn: sqlite3.Connection) -> None:
    """The 24h check-ins (Phase 6.3), backfilled from `treatment_notes` — inserts only, so a
    restart is idempotent and older columns stay untouched."""
    from web.repositories import followup_repo

    followup_repo.create_schema(conn)
    _create_patients(conn)
    try:
        added = followup_repo.backfill_from_treatment_notes(conn)
    except sqlite3.Error:
        logger.exception("followups backfill failed; sessions keep their treatment_notes data")
        return
    if added:
        logger.info("followups: %d row(s) backfilled from treatment_notes", added)


def _create_acupoints(conn: sqlite3.Connection) -> None:
    """Acupoint reference data (Phase 4.3a): created here, filled from the repo's seed when empty.

    Updates to the seed are applied with `python -m zenflow.seed acupoints`; images are added with
    `python -m zenflow.ingest_images <folder>` (Phase 4.3b).
    """
    from zenflow.ingest_images import CREATE_ACUPOINT_IMAGES, CREATE_ACUPOINT_IMAGES_INDEX
    from zenflow.seed import CREATE_ACUPOINTS, seed_acupoints

    conn.execute(CREATE_ACUPOINTS)
    if conn.execute("SELECT COUNT(*) FROM acupoints").fetchone()[0] == 0:
        seed_acupoints(conn)
    conn.execute(CREATE_ACUPOINT_IMAGES)
    conn.execute(CREATE_ACUPOINT_IMAGES_INDEX)


def _create_active_slot_index(conn: sqlite3.Connection) -> None:
    """One active appointment per therapist/date/time (BOT_AUDIT B4).

    A partial unique index, so cancelled rows (soft-deleted, kept for clinical history) do not
    hold a slot. If a database already contains a double booking the index cannot be created —
    that is logged with the offending slots rather than crashing the bot at startup, because the
    application-level check in `save_appointment()` still prevents new ones.
    """
    try:
        conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_appointments_active_slot
               ON appointments(therapist_id, date, time) WHERE status='active'""")
        conn.commit()
    except sqlite3.IntegrityError:
        dupes = conn.execute(
            """SELECT therapist_id, date, time, COUNT(*) AS n, GROUP_CONCAT(id) AS ids
               FROM appointments WHERE status='active'
               GROUP BY therapist_id, date, time HAVING n > 1"""
        ).fetchall()
        for row in dupes:
            logger.error(
                "Double booking in the database: therapist=%s %s %s has %d active appointments "
                "(ids %s). Cancel the extras, then restart to create ux_appointments_active_slot.",
                row["therapist_id"],
                row["date"],
                row["time"],
                row["n"],
                row["ids"],
            )
