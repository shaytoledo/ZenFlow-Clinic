"""
web/repositories/followup_repo.py
──────────────────────────────────
SQL access for the `followups` table (Phase 6.3, docs/FOLLOWUP.md §2): one row per appointment
for its 24h check-in — schedule, delivery, the answers, and the open conversation (which used to
live only in Redis).

`treatment_notes.followup_*` is still written in parallel for one release (plan 6.3); the
callers do both. All timestamps are canonical UTC strings from `zenflow.clock`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

from zenflow import clock

STATUSES = ("scheduled", "sent", "in_progress", "completed", "expired", "no_channel")
#: a check-in that is awaiting the patient
OPEN_STATUSES = ("sent", "in_progress")
SOURCES = ("patient", "therapist_manual")
SLEEP_QUALITY = ("worse", "same", "better")
ADHERENCE = ("yes", "partly", "no")
#: the patient has this long after step 1 to finish the check-in
ANSWER_WINDOW_HOURS = 48

CREATE_FOLLOWUPS = """CREATE TABLE IF NOT EXISTS followups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL UNIQUE REFERENCES appointments(id) ON DELETE CASCADE,
    patient_id INTEGER NOT NULL,
    therapist_id TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT 'telegram',
    status TEXT NOT NULL DEFAULT 'scheduled'
        CHECK (status IN ('scheduled','sent','in_progress','completed','expired','no_channel')),
    auto INTEGER NOT NULL DEFAULT 0,
    scheduled_for TEXT,
    sent_at TEXT,
    completed_at TEXT,
    step INTEGER,
    pain_level INTEGER CHECK (pain_level BETWEEN 0 AND 10),
    improvement_rating INTEGER CHECK (improvement_rating BETWEEN 1 AND 5),
    side_effects TEXT,
    sleep_quality TEXT CHECK (sleep_quality IN ('worse','same','better')),
    adherence TEXT CHECK (adherence IN ('yes','partly','no')),
    free_text TEXT,
    ai_summary TEXT,
    needs_attention INTEGER NOT NULL DEFAULT 0,
    conversation_json TEXT,
    source TEXT NOT NULL DEFAULT 'patient' CHECK (source IN ('patient','therapist_manual')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""
CREATE_FOLLOWUPS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_followups_patient_status ON followups(patient_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_followups_status_sent ON followups(status, sent_at)",
)


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def _json_list(raw: Any) -> list[Any]:
    try:
        value = json.loads(raw) if isinstance(raw, str) else []
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["conversation"] = _json_list(d.pop("conversation_json", None))
    d["side_effects"] = _json_list(d.get("side_effects"))
    d["needs_attention"] = bool(d.get("needs_attention"))
    d["auto"] = bool(d.get("auto"))
    return d


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_FOLLOWUPS)
    for statement in CREATE_FOLLOWUPS_INDEXES:
        conn.execute(statement)


# ── reads ──
def get(appointment_id: int) -> dict[str, Any] | None:
    row = (
        _conn()
        .execute("SELECT * FROM followups WHERE appointment_id=?", (appointment_id,))
        .fetchone()
    )
    return _decode(row)


def open_for_patient(patient_id: int, now_iso: str | None = None) -> dict[str, Any] | None:
    """The patient's newest check-in still awaiting answers (sent within the answer window)."""
    now = clock.parse_iso(now_iso or clock.iso_now())
    since = clock.to_iso(now - timedelta(hours=ANSWER_WINDOW_HOURS))
    row = (
        _conn()
        .execute(
            """SELECT * FROM followups
               WHERE patient_id=? AND status IN ('sent','in_progress') AND sent_at >= ?
               ORDER BY sent_at DESC, id DESC LIMIT 1""",
            (patient_id, since),
        )
        .fetchone()
    )
    return _decode(row)


def list_due_no_channel(therapist_id: str, now_iso: str | None = None) -> list[dict[str, Any]]:
    """Check-ins that are due and can only be done by phone — the dashboard's call list."""
    rows = (
        _conn()
        .execute(
            """SELECT f.appointment_id, f.patient_id, f.scheduled_for, a.patient_name,
                      a.date AS apt_date, a.time AS apt_time
               FROM followups f JOIN appointments a ON a.id = f.appointment_id
               WHERE f.therapist_id=? AND f.status='no_channel' AND f.scheduled_for <= ?
                 AND a.status='active' AND a.therapist_id = f.therapist_id
               ORDER BY f.scheduled_for, f.id""",
            (therapist_id, now_iso or clock.iso_now()),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


# ── the lifecycle ──
def schedule(appointment_id: int, scheduled_for: str, *, auto: bool = False) -> None:
    """Record (or move) the check-in for a completed session.

    The channel comes from the appointment: a manual booking (or negative patient id) cannot be
    messaged, so its row is `no_channel` from the start (Phase 6.4 raises the alert). A row that
    already went out is never reset — completing a session twice only moves a pending schedule.
    """
    now = clock.iso_now()
    _conn().execute(
        """INSERT INTO followups (appointment_id, patient_id, therapist_id, channel, status, auto,
                                  scheduled_for, created_at, updated_at)
           SELECT a.id, a.patient_id, COALESCE(a.therapist_id, ''),
                  CASE WHEN a.source = 'manual' OR a.patient_id < 0 THEN 'none' ELSE 'telegram' END,
                  CASE WHEN a.source = 'manual' OR a.patient_id < 0 THEN 'no_channel'
                       ELSE 'scheduled' END,
                  ?, ?, ?, ?
           FROM appointments a WHERE a.id = ?
           ON CONFLICT(appointment_id) DO UPDATE SET
               scheduled_for = excluded.scheduled_for,
               updated_at = excluded.updated_at
           WHERE followups.status IN ('scheduled', 'no_channel')""",
        (1 if auto else 0, clock.normalize(scheduled_for), now, now, appointment_id),
    )


def mark_sent(appointment_id: int, conversation: list[dict[str, Any]], step: int = 1) -> None:
    now = clock.iso_now()
    _conn().execute(
        """UPDATE followups
           SET status='sent', sent_at=COALESCE(sent_at, ?), step=?, conversation_json=?,
               updated_at=?
           WHERE appointment_id=? AND status IN ('scheduled', 'sent')""",
        (now, step, json.dumps(conversation, ensure_ascii=False), now, appointment_id),
    )


def save_progress(
    appointment_id: int,
    *,
    step: int,
    conversation: list[dict[str, Any]],
    answers: dict[str, Any],
) -> None:
    """One answer taken: the conversation moves on and survives a restart or a Redis flush."""
    _write_answers(appointment_id, "in_progress", step, conversation, answers)


def complete(
    appointment_id: int,
    *,
    conversation: list[dict[str, Any]],
    answers: dict[str, Any],
    needs_attention: bool = False,
    ai_summary: str | None = None,
) -> None:
    now = clock.iso_now()
    _write_answers(appointment_id, "completed", None, conversation, answers)
    _conn().execute(
        """UPDATE followups SET completed_at=COALESCE(completed_at, ?),
                                needs_attention=MAX(needs_attention, ?),
                                ai_summary=COALESCE(?, ai_summary), updated_at=?
           WHERE appointment_id=? AND status='completed'""",
        (now, 1 if needs_attention else 0, ai_summary, now, appointment_id),
    )


_ANSWER_COLUMNS = (
    "pain_level",
    "improvement_rating",
    "side_effects",
    "sleep_quality",
    "adherence",
    "free_text",
)


def _write_answers(
    appointment_id: int,
    status: str,
    step: int | None,
    conversation: list[dict[str, Any]],
    answers: dict[str, Any],
) -> None:
    unknown = set(answers) - set(_ANSWER_COLUMNS)
    if unknown:
        raise ValueError(f"unknown follow-up answers: {sorted(unknown)}")
    values = {
        key: (json.dumps(value, ensure_ascii=False) if key == "side_effects" else value)
        for key, value in answers.items()
    }
    # Column names come from the fixed tuple above, never from input.
    assignments = "".join(f", {key}=?" for key in values)
    _conn().execute(
        f"""UPDATE followups SET status=?, step=?, conversation_json=?, updated_at=?{assignments}
            WHERE appointment_id=? AND status IN ('sent', 'in_progress')""",  # nosec B608
        (
            status,
            step,
            json.dumps(conversation, ensure_ascii=False),
            clock.iso_now(),
            *values.values(),
            appointment_id,
        ),
    )


def flag_attention(appointment_id: int) -> None:
    """The red-flag rule fired (Phase 6.2): the therapist has to look at this check-in."""
    _conn().execute(
        "UPDATE followups SET needs_attention=1, updated_at=? WHERE appointment_id=?",
        (clock.iso_now(), appointment_id),
    )


def expire_stale(now_iso: str | None = None) -> int:
    """Check-ins nobody finished within the answer window become `expired`."""
    now = clock.parse_iso(now_iso or clock.iso_now())
    cutoff = clock.to_iso(now - timedelta(hours=ANSWER_WINDOW_HOURS))
    cur = _conn().execute(
        """UPDATE followups SET status='expired', step=NULL, updated_at=?
           WHERE status IN ('sent', 'in_progress') AND sent_at < ?""",
        (clock.to_iso(now), cutoff),
    )
    return int(cur.rowcount or 0)


def expire_unsent(appointment_id: int) -> None:
    """The send window passed before the job ran: this check-in will never go out."""
    _conn().execute(
        "UPDATE followups SET status='expired', updated_at=? "
        "WHERE appointment_id=? AND status='scheduled'",
        (clock.iso_now(), appointment_id),
    )


def record_manual(appointment_id: int, rating: int | None, notes: str) -> None:
    """The therapist entered the outcome (a call, a visit). A check-in the patient already
    answered is kept; otherwise the row becomes a completed `therapist_manual` one."""
    now = clock.iso_now()
    _conn().execute(
        """INSERT INTO followups (appointment_id, patient_id, therapist_id, channel, status,
                                  source, improvement_rating, free_text, completed_at,
                                  created_at, updated_at)
           SELECT a.id, a.patient_id, COALESCE(a.therapist_id, ''),
                  CASE WHEN a.source = 'manual' OR a.patient_id < 0 THEN 'none' ELSE 'telegram' END,
                  'completed', 'therapist_manual', ?, ?, ?, ?, ?
           FROM appointments a WHERE a.id = ?
           ON CONFLICT(appointment_id) DO UPDATE SET
               status='completed', source='therapist_manual',
               improvement_rating=excluded.improvement_rating, free_text=excluded.free_text,
               completed_at=COALESCE(followups.completed_at, excluded.completed_at),
               updated_at=excluded.updated_at
           WHERE NOT (followups.status = 'completed' AND followups.source = 'patient')""",
        (rating, notes or None, now, now, now, appointment_id),
    )


# ── backfill (runs at start-up; inserts only, never changes treatment_notes) ──
def backfill_from_treatment_notes(conn: sqlite3.Connection, now_iso: str | None = None) -> int:
    """Create a `followups` row for every completed session that has none yet.

    - answered by the patient (conversation or rating) → `completed`, answers copied;
    - only entered by the therapist                    → `completed`, `therapist_manual`;
    - sent, unanswered                                 → `sent` if inside the answer window,
                                                         else `expired`;
    - completed, not sent                              → `scheduled` for completed_at + 24h
                                                         (`no_channel` for manual bookings),
                                                         `expired` once the send window passed.
    Idempotent: `INSERT OR IGNORE` on the unique appointment id.
    """
    now = clock.parse_iso(now_iso or clock.iso_now())
    answer_cutoff = clock.to_iso(now - timedelta(hours=ANSWER_WINDOW_HOURS))
    send_cutoff = clock.to_iso(now - timedelta(hours=48))  # FOLLOWUP_EXPIRE_HOURS
    stamp = clock.to_iso(now)
    cur = conn.execute(
        """INSERT OR IGNORE INTO followups (
               appointment_id, patient_id, therapist_id, channel, status, source,
               scheduled_for, sent_at, completed_at,
               pain_level, improvement_rating, free_text, conversation_json,
               created_at, updated_at)
           SELECT t.appointment_id, t.patient_id, COALESCE(a.therapist_id, ''),
                  CASE WHEN a.source = 'manual' OR t.patient_id < 0 THEN 'none' ELSE 'telegram' END,
                  CASE
                    WHEN t.followup_conversation IS NOT NULL OR COALESCE(t.followup_rating, 0) > 0
                         OR COALESCE(t.manual_feedback_rating, 0) > 0
                         OR COALESCE(t.manual_feedback_notes, '') <> '' THEN 'completed'
                    WHEN t.followup_sent_at IS NOT NULL AND t.followup_sent_at >= ? THEN 'sent'
                    WHEN t.followup_sent_at IS NOT NULL THEN 'expired'
                    WHEN t.completed_at < ? THEN 'expired'
                    WHEN a.source = 'manual' OR t.patient_id < 0 THEN 'no_channel'
                    ELSE 'scheduled'
                  END,
                  CASE WHEN t.followup_conversation IS NULL AND COALESCE(t.followup_rating, 0) = 0
                            AND (COALESCE(t.manual_feedback_rating, 0) > 0
                                 OR COALESCE(t.manual_feedback_notes, '') <> '')
                       THEN 'therapist_manual' ELSE 'patient' END,
                  strftime('%Y-%m-%dT%H:%M:%SZ', t.completed_at, '+24 hours'),
                  t.followup_sent_at,
                  CASE WHEN t.followup_conversation IS NOT NULL
                            OR COALESCE(t.followup_rating, 0) > 0
                            OR COALESCE(t.manual_feedback_rating, 0) > 0
                            OR COALESCE(t.manual_feedback_notes, '') <> ''
                       THEN COALESCE(t.followup_sent_at, t.updated_at) END,
                  CASE WHEN json_valid(t.followup_conversation)
                       THEN json_extract(t.followup_conversation, '$.pain_level') END,
                  COALESCE(
                    NULLIF(t.followup_rating, 0),
                    CASE WHEN json_valid(t.followup_conversation)
                         THEN json_extract(t.followup_conversation, '$.improvement_rating') END,
                    NULLIF(t.manual_feedback_rating, 0)),
                  COALESCE(
                    CASE WHEN json_valid(t.followup_conversation)
                         THEN json_extract(t.followup_conversation, '$.notes') END,
                    NULLIF(t.manual_feedback_notes, '')),
                  CASE WHEN json_valid(t.followup_conversation)
                       THEN json_extract(t.followup_conversation, '$.conversation') END,
                  ?, ?
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           WHERE t.completed_at IS NOT NULL
              OR t.followup_sent_at IS NOT NULL
              OR t.followup_conversation IS NOT NULL
              OR COALESCE(t.followup_rating, 0) > 0
              OR COALESCE(t.manual_feedback_rating, 0) > 0
              OR COALESCE(t.manual_feedback_notes, '') <> ''""",
        (answer_cutoff, send_cutoff, stamp, stamp),
    )
    return int(cur.rowcount or 0)
