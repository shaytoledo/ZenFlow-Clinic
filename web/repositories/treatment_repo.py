"""
web/repositories/treatment_repo.py
───────────────────────────────────
All SQL access for the `treatment_notes` table.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from zenflow.events import notify_treatment


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a SQLite row to a dict, parsing the JSON columns back to Python."""
    d = dict(row)
    for key in ("ai_suggested_points", "ai_recommendations", "used_points"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = [] if key != "ai_recommendations" else {}
    for key in ("followup_conversation",):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = None
    return d


def get_by_appointment(appointment_id: int) -> dict[str, Any] | None:
    row = (
        _conn()
        .execute("SELECT * FROM treatment_notes WHERE appointment_id=?", (appointment_id,))
        .fetchone()
    )
    return _decode(row) if row else None


def _json_or_none(value: Any) -> str | None:
    """Serialise a list/dict to JSON only when it is non-empty.

    Returning None for empty collections lets COALESCE in the UPSERT keep any
    previously-saved non-empty value instead of overwriting it with "[]" or "{}".
    """
    if value is None:
        return None
    if isinstance(value, list | dict) and not value:
        return None
    return json.dumps(value, ensure_ascii=False)


def upsert(appointment_id: int, patient_id: int, notes: dict[str, Any]) -> None:
    """Insert or update treatment notes for an appointment.

    All JSON-typed fields (ai_suggested_points, ai_recommendations, used_points)
    are serialised here; callers pass them as Python lists/dicts.
    Empty lists/dicts are treated as NULL so COALESCE never overwrites an existing
    non-empty value with an empty placeholder.

    The row's patient is the appointment's (Phase 7.2); `patient_id` is only used for notes whose
    appointment row does not exist.
    """
    _conn().execute(
        """INSERT INTO treatment_notes
           (appointment_id, patient_id, tcm_pattern, treatment_principles,
            diagnosis_certainty, ai_suggested_points, ai_recommendations,
            tongue_observation, pulse_observation, session_notes, used_points,
            recommendations_sent_at, completed_at,
            therapist_diagnosis, therapist_notes, created_at, updated_at)
           VALUES (?,COALESCE((SELECT patient_id FROM appointments WHERE id=?), ?),
                   ?,?,?,?,?,?,?,?,?,?,?,?,?,
                   strftime('%Y-%m-%dT%H:%M:%SZ','now'),strftime('%Y-%m-%dT%H:%M:%SZ','now'))
           ON CONFLICT(appointment_id) DO UPDATE SET
             tcm_pattern=COALESCE(excluded.tcm_pattern, tcm_pattern),
             treatment_principles=COALESCE(excluded.treatment_principles, treatment_principles),
             diagnosis_certainty=COALESCE(excluded.diagnosis_certainty, diagnosis_certainty),
             ai_suggested_points=COALESCE(excluded.ai_suggested_points, ai_suggested_points),
             ai_recommendations=COALESCE(excluded.ai_recommendations, ai_recommendations),
             tongue_observation=COALESCE(excluded.tongue_observation, tongue_observation),
             pulse_observation=COALESCE(excluded.pulse_observation, pulse_observation),
             session_notes=COALESCE(excluded.session_notes, session_notes),
             used_points=COALESCE(excluded.used_points, used_points),
             recommendations_sent_at=COALESCE(excluded.recommendations_sent_at, recommendations_sent_at),
             completed_at=COALESCE(excluded.completed_at, completed_at),
             therapist_diagnosis=COALESCE(excluded.therapist_diagnosis, therapist_diagnosis),
             therapist_notes=COALESCE(excluded.therapist_notes, therapist_notes),
             updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
        (
            appointment_id,
            appointment_id,
            patient_id,
            notes.get("tcm_pattern"),
            notes.get("treatment_principles"),
            (
                int(notes["diagnosis_certainty"])
                if notes.get("diagnosis_certainty") is not None
                else None
            ),
            _json_or_none(notes.get("ai_suggested_points")),
            _json_or_none(notes.get("ai_recommendations")),
            notes.get("tongue_observation"),
            notes.get("pulse_observation"),
            notes.get("session_notes"),
            _json_or_none(notes.get("used_points")),
            notes.get("recommendations_sent_at"),
            notes.get("completed_at"),
            notes.get("therapist_diagnosis"),
            notes.get("therapist_notes"),
        ),
    )


def _notified(appointment_id: int, wrote: bool) -> bool:
    """Wake live pages after a write that changed something (Phase 3.4)."""
    if wrote:
        notify_treatment(appointment_id)
    return wrote


def set_points_status(appointment_id: int, status: str) -> None:
    """Update the Stage-2 pipeline status (GENERATING | COMPLETED | FAILED)."""
    _conn().execute(
        "UPDATE treatment_notes SET points_status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE appointment_id=?",
        (status, appointment_id),
    )
    notify_treatment(appointment_id)


def reset_points(appointment_id: int, status: str) -> None:
    """Clear the suggested points and set the status in one statement (a fresh point run)."""
    _conn().execute(
        "UPDATE treatment_notes SET ai_suggested_points=NULL, points_status=?, "
        "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE appointment_id=?",
        (status, appointment_id),
    )
    notify_treatment(appointment_id)


def advance_points_status(
    appointment_id: int, status: str, *, only_from: tuple[str, ...], allow_empty: bool = True
) -> bool:
    """Set the pipeline status only when the current one is in `only_from` (or empty, unless
    `allow_empty=False`).

    One statement, so a replayed or late job can never move a session backwards (for example from
    COMPLETED back to GENERATING). Returns whether the row changed.
    """
    cur = _conn().execute(
        """UPDATE treatment_notes
           SET points_status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE appointment_id=?
             AND ((? AND COALESCE(points_status, '') = '')
                  OR points_status IN (SELECT value FROM json_each(?)))""",
        (status, appointment_id, int(allow_empty), json.dumps(list(only_from))),
    )
    changed = bool(cur.rowcount)
    if changed:
        notify_treatment(appointment_id)
    return changed


def save_points(
    appointment_id: int, points: list[dict[str, Any]], expect_status: str | None = None
) -> bool:
    """Dedicated Stage-2 writer: unconditionally overwrites ai_suggested_points.

    With `expect_status`, nothing is written unless the status is still that value — a run the
    therapist cancelled meanwhile must not land (Phase 3.3). Returns whether it wrote.

    Uses a direct UPDATE (not UPSERT) so COALESCE cannot block a non-empty
    result from landing in the DB.  The row must already exist (created by
    the Stage-0 placeholder save).

    Retries up to 5 times with exponential back-off to survive transient
    SQLITE_LOCKED errors that can occur when the bot and web processes write
    simultaneously (WAL mode reduces but does not eliminate contention).
    """
    if not points:
        return False  # never persist an empty list — leave existing value intact

    import time

    payload = json.dumps(points, ensure_ascii=False)
    for attempt in range(5):
        try:
            cur = _conn().execute(
                """UPDATE treatment_notes
                   SET ai_suggested_points=?, points_status='COMPLETED', updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
                   WHERE appointment_id=? AND (? IS NULL OR COALESCE(points_status, '') = ?)""",
                (payload, appointment_id, expect_status, expect_status),
            )
            return _notified(appointment_id, bool(cur.rowcount))
        except Exception as exc:
            if "locked" in str(exc).lower() and attempt < 4:
                time.sleep(0.2 * (2**attempt))  # 0.2 s, 0.4 s, 0.8 s, 1.6 s
                continue
            raise
    return False  # unreachable: the last attempt either returns or re-raises


def append_points(
    appointment_id: int,
    new_points: list[dict[str, Any]],
    status: str | None = None,
    expect_status: str | None = None,
) -> bool:
    """Append a batch of points to ai_suggested_points without overwriting existing ones.

    Safe against concurrent writes: reads current value, merges in Python, writes back.
    Retries up to 5 times with exponential back-off on SQLITE_LOCKED errors.
    `status`, when given, is written in the same statement, so "batch saved" and "stage advanced"
    can never disagree after a crash (Phase 3.1). With `expect_status`, nothing is written unless
    the status is still that value (a cancelled run's batch is discarded — Phase 3.3).
    Returns whether it wrote.
    """
    if not new_points:
        if status is None:
            return False
        cur = _conn().execute(
            "UPDATE treatment_notes SET points_status=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
            "WHERE appointment_id=? AND (? IS NULL OR COALESCE(points_status, '') = ?)",
            (status, appointment_id, expect_status, expect_status),
        )
        return _notified(appointment_id, bool(cur.rowcount))

    import time

    for attempt in range(5):
        try:
            row = (
                _conn()
                .execute(
                    "SELECT ai_suggested_points FROM treatment_notes WHERE appointment_id=?",
                    (appointment_id,),
                )
                .fetchone()
            )
            existing: list[Any] = []
            if row and row["ai_suggested_points"]:
                try:
                    existing = json.loads(row["ai_suggested_points"])
                except Exception:
                    existing = []
            existing_codes = {p.get("code") for p in existing if isinstance(p, dict)}
            # Deduplicate: skip any point whose code is already present
            to_add = [
                p
                for p in new_points
                if not (isinstance(p, dict) and p.get("code") in existing_codes)
            ]
            merged = json.dumps(existing + to_add, ensure_ascii=False)
            cur = _conn().execute(
                "UPDATE treatment_notes SET ai_suggested_points=?, "
                "points_status=COALESCE(?, points_status), "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                "WHERE appointment_id=? AND (? IS NULL OR COALESCE(points_status, '') = ?)",
                (merged, status, appointment_id, expect_status, expect_status),
            )
            return _notified(appointment_id, bool(cur.rowcount))
        except Exception as exc:
            if "locked" in str(exc).lower() and attempt < 4:
                time.sleep(0.2 * (2**attempt))
                continue
            raise
    return False  # unreachable: the last attempt either returns or re-raises


def save_followup_conversation(appointment_id: int, conversation_data: dict[str, Any]) -> None:
    """Persist the structured follow-up conversation (replaces simple followup_rating)."""
    _conn().execute(
        """UPDATE treatment_notes
           SET followup_conversation=?, followup_rating=?, followup_sent_at=COALESCE(followup_sent_at, strftime('%Y-%m-%dT%H:%M:%SZ','now')), updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE appointment_id=?""",
        (
            json.dumps(conversation_data, ensure_ascii=False),
            conversation_data.get("improvement_rating"),
            appointment_id,
        ),
    )


def save_manual_feedback(appointment_id: int, rating: int | None, notes: str) -> None:
    """Save therapist-entered patient feedback from the web dashboard."""
    _conn().execute(
        """UPDATE treatment_notes
           SET manual_feedback_rating=?, manual_feedback_notes=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE appointment_id=?""",
        (rating, notes or None, appointment_id),
    )


def save_pending_recommendations(appointment_id: int, items: list[Any], send_at_iso: str) -> None:
    """Store lifestyle recommendations to be auto-sent 24h after session completion."""
    _conn().execute(
        """UPDATE treatment_notes
           SET pending_recommendations=?, pending_rec_send_at=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE appointment_id=?""",
        (json.dumps(items, ensure_ascii=False), send_at_iso, appointment_id),
    )


def clear_pending_recommendations(appointment_id: int) -> None:
    _conn().execute(
        """UPDATE treatment_notes
           SET pending_recommendations=NULL, pending_rec_send_at=NULL, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now')
           WHERE appointment_id=?""",
        (appointment_id,),
    )


def mark_recommendations_delivered(appointment_id: int) -> None:
    """The queued recommendations went out: stamp `recommendations_sent_at` and drop the queue
    entry in one statement (Phase 5.5). Without the stamp, completing the session again would
    queue — and send — them a second time."""
    from zenflow import clock

    _conn().execute(
        """UPDATE treatment_notes
           SET recommendations_sent_at=?, pending_recommendations=NULL, pending_rec_send_at=NULL,
               updated_at=?
           WHERE appointment_id=?""",
        (clock.iso_now(), clock.iso_now(), appointment_id),
    )


def list_due_pending_recommendations(now_iso: str) -> list[dict[str, Any]]:
    """Return sessions whose pending recommendations are due to send."""
    rows = (
        _conn()
        .execute(
            """SELECT t.appointment_id, t.patient_id, t.pending_recommendations,
                  a.patient_name, a.patient_phone, a.source, a.therapist_id
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           WHERE t.pending_recommendations IS NOT NULL
             AND t.pending_rec_send_at <= ?
             AND a.status = 'active'""",
            (now_iso,),
        )
        .fetchall()
    )
    rows_out = []
    for r in rows:
        d = dict(r)
        try:
            d["pending_recommendations"] = json.loads(d["pending_recommendations"])
        except Exception:
            d["pending_recommendations"] = []
        rows_out.append(d)
    return rows_out


def list_completed_for_followup(window_start_iso: str, window_end_iso: str) -> list[dict[str, Any]]:
    """Completed sessions whose `completed_at` is in [start, end] — used by 24h follow-up."""
    rows = (
        _conn()
        .execute(
            """SELECT t.appointment_id, t.patient_id, a.patient_name, a.therapist_id,
                  t.completed_at, t.tcm_pattern
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           WHERE t.completed_at IS NOT NULL
             AND t.completed_at >= ?
             AND t.completed_at <= ?
             AND a.status='active'""",
            (window_start_iso, window_end_iso),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def list_uncompleted_since(first_date: str) -> list[dict[str, Any]]:
    """Active appointments on or after `first_date` (clinic-local YYYY-MM-DD) that were never
    marked complete — candidates for an automatic check-in (Q7, ZF_AUTO_FOLLOWUP)."""
    rows = (
        _conn()
        .execute(
            """SELECT a.id AS appointment_id, a.date, a.time
           FROM appointments a
           LEFT JOIN treatment_notes t ON t.appointment_id = a.id
           WHERE a.status = 'active' AND a.date >= ? AND t.completed_at IS NULL""",
            (first_date,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def get_auto_followup_candidate(appointment_id: int) -> dict[str, Any] | None:
    """An active appointment for an automatic check-in, with or without treatment notes."""
    row = (
        _conn()
        .execute(
            """SELECT a.id AS appointment_id, a.patient_id, a.patient_name, a.therapist_id,
                  a.source, t.completed_at, c.channel AS contact_channel, c.external_id AS contact_id
           FROM appointments a
           LEFT JOIN treatment_notes t ON t.appointment_id = a.id
           LEFT JOIN patient_contacts c ON c.patient_id = a.patient_id
           WHERE a.id = ? AND a.status = 'active'""",
            (appointment_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def get_followup_candidate(appointment_id: int) -> dict[str, Any] | None:
    """The completed session behind a follow-up job, or None if it is gone or cancelled."""
    row = (
        _conn()
        .execute(
            """SELECT t.appointment_id, a.patient_id, a.patient_name, a.therapist_id, a.source,
                  t.completed_at, t.followup_sent_at, t.followup_conversation, t.followup_rating,
                  c.channel AS contact_channel, c.external_id AS contact_id
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           LEFT JOIN patient_contacts c ON c.patient_id = a.patient_id
           WHERE t.appointment_id = ? AND a.status = 'active'""",
            (appointment_id,),
        )
        .fetchone()
    )
    return dict(row) if row else None


def _decode_pending(d: dict[str, Any]) -> dict[str, Any]:
    try:
        d["pending_recommendations"] = json.loads(d["pending_recommendations"])
    except Exception:
        d["pending_recommendations"] = []
    return d


def get_pending_recommendation(appointment_id: int) -> dict[str, Any] | None:
    """Queued recommendations for one appointment (any send time), or None if nothing is queued."""
    row = (
        _conn()
        .execute(
            """SELECT t.appointment_id, a.patient_id, t.pending_recommendations,
                  t.pending_rec_send_at, a.patient_name, a.patient_phone, a.patient_email,
                  a.source, a.therapist_id, c.channel AS contact_channel, c.external_id AS contact_id
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           LEFT JOIN patient_contacts c ON c.patient_id = a.patient_id
           WHERE t.appointment_id = ? AND t.pending_recommendations IS NOT NULL
             AND a.status = 'active'""",
            (appointment_id,),
        )
        .fetchone()
    )
    return _decode_pending(dict(row)) if row else None


def list_recent_completions_without_followup(since_iso: str) -> list[dict[str, Any]]:
    """Completed since `since_iso` (canonical UTC) and not yet followed up — reconciliation."""
    rows = (
        _conn()
        .execute(
            """SELECT t.appointment_id, t.completed_at
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           WHERE t.completed_at IS NOT NULL AND t.completed_at >= ?
             AND t.followup_sent_at IS NULL
             AND t.followup_conversation IS NULL
             AND (t.followup_rating IS NULL OR t.followup_rating = 0)
             AND a.status = 'active'""",
            (since_iso,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def list_all_pending_recommendations() -> list[dict[str, Any]]:
    """Every queued recommendation entry (any send time) — reconciliation."""
    rows = (
        _conn()
        .execute(
            """SELECT t.appointment_id, t.pending_rec_send_at
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           WHERE t.pending_recommendations IS NOT NULL AND t.pending_rec_send_at IS NOT NULL
             AND a.status = 'active'""",
        )
        .fetchall()
    )
    return [dict(r) for r in rows]
