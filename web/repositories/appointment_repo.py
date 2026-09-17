"""
web/repositories/appointment_repo.py
─────────────────────────────────────
All SQL access for the `appointments` table (and joined `intake_sessions`).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from zenflow.clock import SQL_NOW


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def _parse(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    hj = d.pop("history_json", None)
    d["intake_history"] = json.loads(hj) if hj else []
    return d


def list_all() -> list[dict[str, Any]]:
    """Every appointment with its intake history (left-joined)."""
    rows = _conn().execute("""SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id""").fetchall()
    return [_parse(r) for r in rows]


def get_by_id(appointment_id: int) -> dict[str, Any] | None:
    """Fetch one appointment row by primary key (no intake join)."""
    row = _conn().execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
    return dict(row) if row else None


def list_by_patient(patient_id: int, therapist_id: str | None = None) -> list[dict[str, Any]]:
    """All appointments for a patient — optionally only those owned by `therapist_id`."""
    sql = """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id=?"""
    params: list[Any] = [patient_id]
    if therapist_id:
        sql += " AND a.therapist_id=?"
        params.append(therapist_id)
    sql += " ORDER BY a.date, a.time"
    rows = _conn().execute(sql, params).fetchall()
    return [_parse(r) for r in rows]


def list_active_by_patient(patient_id: int) -> list[dict[str, Any]]:
    rows = (
        _conn()
        .execute(
            """SELECT * FROM appointments
           WHERE patient_id=? AND status='active'
           ORDER BY date, time""",
            (patient_id,),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def get_by_patient_date_time(
    patient_id: int, apt_date: str, apt_time: str, therapist_id: str | None = None
) -> dict[str, Any] | None:
    """Fetch a single appointment record (apt_time accepts HH:MM or HH-MM).

    With `therapist_id` the lookup is tenant-scoped: another therapist's appointment is
    simply "not found".
    """
    time_str = apt_time.replace("-", ":")
    sql = """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id=? AND a.date=? AND a.time=?"""
    params: list[Any] = [patient_id, apt_date, time_str]
    if therapist_id:
        sql += " AND a.therapist_id=?"
        params.append(therapist_id)
    sql += " ORDER BY a.created_at DESC LIMIT 1"
    row = _conn().execute(sql, params).fetchone()
    return _parse(row) if row else None


def get_id(
    patient_id: int, apt_date: str, apt_time: str, therapist_id: str | None = None
) -> int | None:
    """Return just the appointment id (HH:MM or HH-MM accepted); tenant-scoped when given."""
    time_str = apt_time.replace("-", ":")
    sql = "SELECT id FROM appointments WHERE patient_id=? AND date=? AND time=?"
    params: list[Any] = [patient_id, apt_date, time_str]
    if therapist_id:
        sql += " AND therapist_id=?"
        params.append(therapist_id)
    sql += " ORDER BY created_at DESC LIMIT 1"
    row = _conn().execute(sql, params).fetchone()
    return row[0] if row else None


def update_status(appointment_id: int, status: str) -> None:
    """Set status to 'active' or 'cancelled' (soft delete only — record preserved)."""
    _conn().execute("UPDATE appointments SET status=? WHERE id=?", (status, appointment_id))


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
    patient_email: str = "",
    summary: str = "",
    existing_patient_id: int | None = None,
) -> tuple[int, int]:
    """Insert a manually-created appointment (no Telegram intake).

    If `existing_patient_id` is provided, the new appointment is attached to that existing
    patient (so the EHR groups all sessions together) — the caller has checked it is one of the
    therapist's own. Otherwise a new patient is created, with no messaging channel (Phase 7.2).
    Contact details given here are also kept on the patient. Returns
    (appointment_id, patient_id).
    """
    from web.repositories import patient_repo

    conn = _conn()
    # One unit: a slot that turns out to be taken must not leave a patient behind.
    with patient_repo.atomic(conn, "manual_booking"):
        if existing_patient_id is not None:
            patient_id = int(existing_patient_id)
            patient_repo.update_contact(patient_id, phone=patient_phone, email=patient_email)
        else:
            patient_id = patient_repo.create(patient_name, phone=patient_phone, email=patient_email)
        cur = conn.execute(
            f"""INSERT INTO appointments
               (patient_id, patient_name, therapist_id, date, time, status, summary,
                source, patient_phone, patient_email, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?, 'manual', ?, ?, {SQL_NOW})""",
            (
                patient_id,
                patient_name,
                therapist_id,
                apt_date,
                apt_time,
                summary,
                patient_phone,
                patient_email,
            ),
        )
    if cur.lastrowid is None:  # pragma: no cover - sqlite always sets it after INSERT
        raise RuntimeError("INSERT did not return a rowid")
    return int(cur.lastrowid), patient_id


def search_patients(
    query: str, limit: int = 10, therapist_id: str | None = None
) -> list[dict[str, Any]]:
    """Search distinct patients by name (latest contact info per patient).

    Returns: [{patient_id, patient_name, patient_phone, patient_email, source, last_seen}]
    With `therapist_id`, only patients who have an appointment with that therapist.
    """
    q = (query or "").strip()
    where: list[str] = []
    params: list[Any] = []
    if q:
        where.append("patient_name LIKE ?")
        params.append(f"%{q}%")
    if therapist_id:
        where.append("therapist_id = ?")
        params.append(therapist_id)
    sql = """SELECT patient_id, patient_name, patient_phone, patient_email, source,
                  MAX(created_at) AS last_seen
           FROM appointments"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY patient_id ORDER BY last_seen DESC LIMIT ?"
    params.append(limit)
    rows = _conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def list_in_date_range(therapist_id: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Return active appointments for `therapist_id` whose date is in [start_date, end_date].

    Both bounds are 'YYYY-MM-DD' strings. Used by the schedule page to overlay
    booked appointments on the calendar.
    """
    rows = (
        _conn()
        .execute(
            """SELECT id, patient_id, patient_name, therapist_id, date, time, source,
                  gcal_apt_event_id
           FROM appointments
           WHERE therapist_id=? AND status='active'
             AND date >= ? AND date <= ?
           ORDER BY date, time""",
            (therapist_id, start_date, end_date),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def list_completed_in_window(start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    """Return all completed appointments whose `completed_at` falls in [start, end].

    Used by the 24h follow-up scheduler.
    """
    rows = (
        _conn()
        .execute(
            """SELECT a.id, a.patient_id, a.patient_name, a.therapist_id,
                  a.date, a.time, t.completed_at
           FROM appointments a
           JOIN treatment_notes t ON t.appointment_id = a.id
           WHERE t.completed_at IS NOT NULL
             AND t.completed_at >= ?
             AND t.completed_at <= ?
             AND a.status='active'""",
            (start_iso, end_iso),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]
