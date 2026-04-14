"""
db.py — PostgreSQL singleton for ZenFlow (Railway deployment).

Provides a thin psycopg2 wrapper that keeps the sqlite3-like interface used
throughout the codebase so no SQL files need their queries rewritten:

  get_db()              → _PgConnection (thread-local, from a pool)
  conn.execute(sql, params) → _PgCursor
  cursor.fetchall()     → list[dict]
  cursor.fetchone()     → dict | None
  conn.commit()         → no-op in autocommit mode; commits explicit transactions
  conn.execute("BEGIN") → starts an explicit transaction
  conn.execute("COMMIT/ROLLBACK") → ends it

Automatic SQL transforms applied in execute():
  ?                    → %s   (psycopg2 placeholder style)
  datetime('now')      → TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
"""
import logging
import os
import threading

import psycopg2
import psycopg2.extras
import psycopg2.pool

logger = logging.getLogger(__name__)

_DATABASE_URL = os.getenv("DATABASE_URL", "")
_pool: psycopg2.pool.ThreadedConnectionPool | None = None
_pool_lock = threading.Lock()


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                url = _DATABASE_URL
                if not url:
                    raise RuntimeError(
                        "DATABASE_URL is not set. "
                        "Add a PostgreSQL addon in Railway and ensure DATABASE_URL is in your environment."
                    )
                _pool = psycopg2.pool.ThreadedConnectionPool(minconn=1, maxconn=10, dsn=url)
                logger.info("PostgreSQL connection pool created")
    return _pool


# ── Cursor wrapper ─────────────────────────────────────────────────────────────

class _PgCursor:
    """Wraps a psycopg2 RealDictCursor to behave like a sqlite3.Cursor."""

    def __init__(self, pg_cursor):
        self._cur = pg_cursor
        self._last_id = None

    def fetchall(self) -> list[dict]:
        try:
            rows = self._cur.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception:
            return []

    def fetchone(self) -> dict | None:
        try:
            row = self._cur.fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def __iter__(self):
        for row in self._cur:
            yield dict(row)

    @property
    def lastrowid(self) -> int | None:
        """Only populated when execute() was called with RETURNING id in the SQL."""
        return self._last_id


# ── Connection wrapper ─────────────────────────────────────────────────────────

_SQL_TRANSFORMS = [
    ("datetime('now')", "TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')"),
]


class _PgConnection:
    """
    Wraps a psycopg2 connection to behave like sqlite3.Connection (isolation_level=None).

    - Default mode: autocommit=True  (each statement commits immediately)
    - BEGIN string  → switches to autocommit=False (starts explicit transaction)
    - COMMIT string → commits + restores autocommit=True
    - ROLLBACK string → rolls back + restores autocommit=True
    """

    def __init__(self, raw_conn, pool):
        self._conn = raw_conn
        self._pool = pool
        self._conn.autocommit = True

    def _transform(self, sql: str) -> str:
        sql = sql.replace("?", "%s")
        for old, new in _SQL_TRANSFORMS:
            sql = sql.replace(old, new)
        return sql

    def execute(self, sql: str, params=()):
        upper = sql.strip().upper()

        if upper == "BEGIN":
            self._conn.autocommit = False
            return _PgCursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

        if upper == "COMMIT":
            self._conn.commit()
            self._conn.autocommit = True
            return _PgCursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

        if upper == "ROLLBACK":
            self._conn.rollback()
            self._conn.autocommit = True
            return _PgCursor(self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor))

        sql = self._transform(sql)
        pg_cur = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        pg_cur.execute(sql, params if params else None)
        cursor = _PgCursor(pg_cur)
        return cursor

    def commit(self):
        """Commit an explicit transaction; no-op in autocommit mode."""
        if not self._conn.autocommit:
            self._conn.commit()
            self._conn.autocommit = True

    def rollback(self):
        if not self._conn.autocommit:
            self._conn.rollback()
            self._conn.autocommit = True

    def close(self):
        """Return the underlying connection to the pool."""
        try:
            self._pool.putconn(self._conn)
        except Exception:
            pass


# ── Thread-local connection ────────────────────────────────────────────────────

_local = threading.local()


def get_db() -> _PgConnection:
    """Return a thread-local _PgConnection (borrowed from the pool)."""
    conn_wrapper = getattr(_local, "conn", None)

    # Validate the connection is still alive
    if conn_wrapper is not None:
        try:
            conn_wrapper._conn.cursor().execute("SELECT 1")
        except Exception:
            try:
                _get_pool().putconn(conn_wrapper._conn)
            except Exception:
                pass
            _local.conn = None
            conn_wrapper = None

    if conn_wrapper is None:
        pool = _get_pool()
        raw = pool.getconn()
        raw.autocommit = True
        conn_wrapper = _PgConnection(raw, pool)
        _local.conn = conn_wrapper

    return conn_wrapper


# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA_STMTS = [
    """CREATE TABLE IF NOT EXISTS therapists (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    telegram_id INTEGER DEFAULT 0,
    email TEXT,
    password_hash TEXT,
    google_id TEXT,
    google_token_json TEXT,
    calendar_name TEXT DEFAULT 'ZenFlow Availability',
    active INTEGER DEFAULT 0,
    created_at TEXT DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
)""",
    """CREATE TABLE IF NOT EXISTS appointments (
    id SERIAL PRIMARY KEY,
    patient_id INTEGER NOT NULL,
    patient_name TEXT NOT NULL,
    therapist_id TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    status TEXT DEFAULT 'active',
    gcal_apt_event_id TEXT,
    summary TEXT,
    created_at TEXT DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
)""",
    """CREATE TABLE IF NOT EXISTS intake_sessions (
    id SERIAL PRIMARY KEY,
    appointment_id INTEGER NOT NULL,
    patient_id INTEGER NOT NULL,
    therapist_id TEXT NOT NULL,
    history_json TEXT,
    created_at TEXT DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
)""",
    """CREATE TABLE IF NOT EXISTS availability (
    id TEXT PRIMARY KEY,
    therapist_id TEXT NOT NULL,
    start_dt TEXT NOT NULL,
    end_dt TEXT NOT NULL
)""",
    """CREATE TABLE IF NOT EXISTS treatment_notes (
    id SERIAL PRIMARY KEY,
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
    completed_at TEXT,
    created_at TEXT DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
    updated_at TEXT DEFAULT TO_CHAR(NOW(), 'YYYY-MM-DD HH24:MI:SS')
)""",
]


def init_db() -> None:
    """Create all tables and run idempotent schema migrations on every startup."""
    conn = get_db()
    for stmt in _SCHEMA_STMTS:
        conn.execute(stmt)

    # ADD COLUMN IF NOT EXISTS — safe to run every startup
    _migrations = [
        "ALTER TABLE treatment_notes ADD COLUMN IF NOT EXISTS diagnosis_certainty INTEGER DEFAULT 0",
        "ALTER TABLE treatment_notes ADD COLUMN IF NOT EXISTS completed_at TEXT",
        "ALTER TABLE therapists ADD COLUMN IF NOT EXISTS google_token_json TEXT",
    ]
    for migration in _migrations:
        try:
            conn.execute(migration)
        except Exception:
            pass

    logger.info("PostgreSQL schema ready")
