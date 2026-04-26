"""
web/repositories/appointment_repo.py
─────────────────────────────────────
All SQL access for the `appointments` table (and joined `intake_sessions`).
"""
from __future__ import annotations

import json


def _conn():
    from bot.db import get_db
    return get_db()


def _parse(row) -> dict:
    d = dict(row)
    hj = d.pop("history_json", None)
    d["intake_history"] = json.loads(hj) if hj else []
    return d


def list_all() -> list[dict]:
    """Every appointment with its intake history (left-joined)."""
    rows = _conn().execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id"""
    ).fetchall()
    return [_parse(r) for r in rows]


def list_by_patient(patient_id: int) -> list[dict]:
    rows = _conn().execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id=?
           ORDER BY a.date, a.time""",
        (patient_id,),
    ).fetchall()
    return [_parse(r) for r in rows]


def list_active_by_patient(patient_id: int) -> list[dict]:
    rows = _conn().execute(
        """SELECT * FROM appointments
           WHERE patient_id=? AND status='active'
           ORDER BY date, time""",
        (patient_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_by_patient_date_time(
    patient_id: int, apt_date: str, apt_time: str
) -> dict | None:
    """Fetch a single appointment record (apt_time accepts HH:MM or HH-MM)."""
    time_str = apt_time.replace("-", ":")
    row = _conn().execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id=? AND a.date=? AND a.time=?
           ORDER BY a.created_at DESC LIMIT 1""",
        (patient_id, apt_date, time_str),
    ).fetchone()
    return _parse(row) if row else None


def get_id(patient_id: int, apt_date: str, apt_time: str) -> int | None:
    """Return just the appointment id (HH:MM or HH-MM accepted)."""
    time_str = apt_time.replace("-", ":")
    row = _conn().execute(
        """SELECT id FROM appointments
           WHERE patient_id=? AND date=? AND time=?
           ORDER BY created_at DESC LIMIT 1""",
        (patient_id, apt_date, time_str),
    ).fetchone()
    return row[0] if row else None


def update_status(appointment_id: int, status: str) -> None:
    """Set status to 'active' or 'cancelled' (soft delete only — record preserved)."""
    _conn().execute(
        "UPDATE appointments SET status=? WHERE id=?", (status, appointment_id)
    )


def set_gcal_event_id(appointment_id: int, event_id: str | None) -> None:
    """Stamp the Google-Calendar event id on an appointment after booking."""
    _conn().execute(
        "UPDATE appointments SET gcal_apt_event_id=? WHERE id=?",
        (event_id, appointment_id),
    )


def insert_manual(
    patient_name: str,
    therapist_id: str,
    apt_date: str,
    apt_time: str,
    patient_phone: str = "",
    summary: str = "",
) -> tuple[int, int]:
    """Insert a manually-created appointment (no Telegram intake).

    Generates a unique negative `patient_id` so it cannot collide with real
    Telegram user IDs (which are positive). Returns (appointment_id, patient_id).
    """
    import time
    patient_id = -int(time.time() * 1000)  # always negative, monotonic
    cur = _conn().execute(
        """INSERT INTO appointments
           (patient_id, patient_name, therapist_id, date, time, status, summary, source, patient_phone)
           VALUES (?, ?, ?, ?, ?, 'active', ?, 'manual', ?)""",
        (patient_id, patient_name, therapist_id, apt_date, apt_time, summary, patient_phone),
    )
    return int(cur.lastrowid), patient_id


def list_in_date_range(therapist_id: str, start_date: str, end_date: str) -> list[dict]:
    """Return active appointments for `therapist_id` whose date is in [start_date, end_date].

    Both bounds are 'YYYY-MM-DD' strings. Used by the schedule page to overlay
    booked appointments on the calendar.
    """
    rows = _conn().execute(
        """SELECT id, patient_id, patient_name, therapist_id, date, time, source,
                  gcal_apt_event_id
           FROM appointments
           WHERE therapist_id=? AND status='active'
             AND date >= ? AND date <= ?
           ORDER BY date, time""",
        (therapist_id, start_date, end_date),
    ).fetchall()
    return [dict(r) for r in rows]


def list_completed_in_window(start_iso: str, end_iso: str) -> list[dict]:
    """Return all completed appointments whose `completed_at` falls in [start, end].

    Used by the 24h follow-up scheduler.
    """
    rows = _conn().execute(
        """SELECT a.id, a.patient_id, a.patient_name, a.therapist_id,
                  a.date, a.time, t.completed_at
           FROM appointments a
           JOIN treatment_notes t ON t.appointment_id = a.id
           WHERE t.completed_at IS NOT NULL
             AND t.completed_at >= ?
             AND t.completed_at <= ?
             AND a.status='active'""",
        (start_iso, end_iso),
    ).fetchall()
    return [dict(r) for r in rows]
