"""
db.py — SQLite singleton for ZenFlow.

- One connection per thread (threading.local)
- WAL mode for concurrent reads from bot + web processes
- Auto-creates tables and runs schema migrations on first call to init_db()
"""
import logging
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent.parent / "data" / "zenflow.db"
_local = threading.local()

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
    conn = getattr(_local, "conn", None)
    if conn is None:
        _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None = autocommit: Python never issues an implicit BEGIN,
        # so there are no stale open transactions when a thread is reused from the pool.
        conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False,
                               timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
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
    ]
    for migration in _migrations:
        try:
            conn.execute(migration)
            conn.commit()
        except Exception:
            pass  # Column already exists — safe to ignore
