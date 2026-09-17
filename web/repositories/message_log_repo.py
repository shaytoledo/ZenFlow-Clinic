"""
web/repositories/message_log_repo.py
─────────────────────────────────────
SQL access for `message_log` (plan 8.3, started in Phase 6.6): one row per outbound patient
message attempt — what went out, on which channel, and whether it arrived at the provider.

Append-only. The recipient address itself is not stored (the appointment has it); an error text
is secret-redacted and shortened before it is written, because therapists read it.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from zenflow import clock
from zenflow.logging import redact

DIRECTIONS = ("out", "in")
CHANNELS = ("telegram", "whatsapp", "email")
KINDS = ("recommendations", "followup", "confirmation")
MIGRATION = "0002_message_log_kinds"
STATUSES = ("sent", "failed")
MAX_ERROR_LEN = 300

CREATE_MESSAGE_LOG = """CREATE TABLE IF NOT EXISTS message_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'out' CHECK (direction IN ('out','in')),
    channel TEXT NOT NULL CHECK (channel IN ('telegram','whatsapp','email')),
    patient_id INTEGER,
    therapist_id TEXT NOT NULL DEFAULT '',
    appointment_id INTEGER,
    kind TEXT NOT NULL CHECK (kind IN ('recommendations','followup','confirmation')),
    status TEXT NOT NULL CHECK (status IN ('sent','failed')),
    provider_message_id TEXT,
    error TEXT
)"""
CREATE_MESSAGE_LOG_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_message_log_appointment ON message_log(appointment_id, ts)"
)


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_MESSAGE_LOG)
    conn.execute(CREATE_MESSAGE_LOG_INDEX)
    widen_kinds(conn)


def widen_kinds(conn: sqlite3.Connection) -> None:
    """Phase 7.3: booking confirmations, and WhatsApp as a channel.

    SQLite cannot change a CHECK constraint, so a table created before this runs is rebuilt once
    with its rows copied over. Recorded in `schema_migrations` like every data migration.
    """
    from web.repositories import patient_repo

    conn.execute(patient_repo.CREATE_SCHEMA_MIGRATIONS)
    done = conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (MIGRATION,)).fetchone()
    if done:
        return
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='message_log'"
    ).fetchone()
    if sql and "confirmation" not in str(sql["sql"]):
        with patient_repo.atomic(conn, "message_log_kinds"):
            conn.execute(CREATE_MESSAGE_LOG.replace("message_log", "message_log_new", 1))
            conn.execute("INSERT INTO message_log_new SELECT * FROM message_log")
            conn.execute("DROP TABLE message_log")
            conn.execute("ALTER TABLE message_log_new RENAME TO message_log")
            conn.execute(CREATE_MESSAGE_LOG_INDEX)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (name, applied_at) VALUES (?, ?)",
        (MIGRATION, clock.iso_now()),
    )


def record(
    *,
    channel: str,
    kind: str,
    status: str,
    therapist_id: str,
    patient_id: int | None,
    appointment_id: int | None,
    provider_message_id: Any = None,
    error: str | None = None,
) -> int:
    """Append one outbound attempt. Returns the row id."""
    text = redact(str(error))[:MAX_ERROR_LEN] if error else None
    cur = _conn().execute(
        """INSERT INTO message_log (ts, direction, channel, patient_id, therapist_id,
                                    appointment_id, kind, status, provider_message_id, error)
           VALUES (?, 'out', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            clock.iso_now(),
            channel,
            patient_id,
            therapist_id or "",
            appointment_id,
            kind,
            status,
            None if provider_message_id is None else str(provider_message_id),
            text,
        ),
    )
    return int(cur.lastrowid or 0)


def for_appointment(therapist_id: str, appointment_id: int) -> list[dict[str, Any]]:
    """The session's outbound messages, oldest first, for its own therapist only."""
    rows = (
        _conn()
        .execute(
            """SELECT * FROM message_log
               WHERE therapist_id=? AND appointment_id=?
               ORDER BY ts, id""",
            (therapist_id, appointment_id),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def provider_id(result: Any) -> str | None:
    """The provider's message id from a send result: a channel's `SentMessage`, Telegram's
    JSON, or a plain id (Gmail)."""
    if result is None:
        return None
    if isinstance(result, dict):
        inner = result.get("result")
        if isinstance(inner, dict) and inner.get("message_id") is not None:
            return str(inner["message_id"])
        return None
    if isinstance(result, str | int):
        return str(result)
    message_id = getattr(result, "message_id", None)
    return None if message_id is None else str(message_id)
