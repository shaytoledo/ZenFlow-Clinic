"""
web/services/treatment_service.py
──────────────────────────────────
CRUD abstraction for the `treatment_notes` table.
"""
import datetime
import logging

logger = logging.getLogger(__name__)


def get_appointment_id(patient_id: int, apt_date: str, apt_time: str) -> int | None:
    """Resolve an appointment row ID from patient/date/time."""
    time_str = apt_time.replace("-", ":")
    from bot.db import get_db
    row = get_db().execute(
        """SELECT id FROM appointments
           WHERE patient_id=? AND date=? AND time=?
           ORDER BY created_at DESC LIMIT 1""",
        (patient_id, apt_date, time_str),
    ).fetchone()
    return row["id"] if row else None


def get_notes(appointment_id: int) -> dict | None:
    """Return treatment notes for a given appointment_id, or None."""
    from bot.patient_bot.services.appointments import get_treatment_notes
    return get_treatment_notes(appointment_id)


def save_notes(appointment_id: int, patient_id: int, data: dict) -> None:
    """Upsert treatment notes for a given appointment."""
    from bot.patient_bot.services.appointments import save_treatment_notes
    save_treatment_notes(appointment_id, patient_id, data)


def complete_session(appointment_id: int, patient_id: int) -> None:
    """Mark a treatment session as completed (records completion timestamp)."""
    save_notes(appointment_id, patient_id, {
        "completed_at": datetime.datetime.now().isoformat()
    })


def list_completed_sessions(therapist_id: str | None = None) -> list[dict]:
    """List all treatment sessions that have been completed, newest first."""
    from bot.db import get_db
    import json
    query = """
        SELECT
            tn.appointment_id,
            tn.patient_id,
            tn.tcm_pattern,
            tn.diagnosis_certainty,
            tn.completed_at,
            tn.updated_at,
            a.patient_name,
            a.date,
            a.time,
            a.therapist_id
        FROM treatment_notes tn
        JOIN appointments a ON a.id = tn.appointment_id
        WHERE tn.completed_at IS NOT NULL
    """
    params: list = []
    if therapist_id:
        query += " AND a.therapist_id = ?"
        params.append(therapist_id)
    query += " ORDER BY tn.completed_at DESC"
    rows = get_db().execute(query, params).fetchall()
    return [dict(r) for r in rows]


def list_all_sessions(therapist_id: str | None = None, sort_by: str = "date") -> list[dict]:
    """List all treatment notes sessions, optionally filtered by therapist."""
    from bot.db import get_db
    order_map = {
        "name": "a.patient_name ASC, a.date DESC",
        "date": "a.date DESC, a.time DESC",
        "last_access": "tn.updated_at DESC",
    }
    order_clause = order_map.get(sort_by, "a.date DESC, a.time DESC")
    query = f"""
        SELECT
            tn.appointment_id,
            tn.patient_id,
            tn.tcm_pattern,
            tn.diagnosis_certainty,
            tn.completed_at,
            tn.updated_at,
            tn.session_notes,
            a.patient_name,
            a.date,
            a.time,
            a.therapist_id,
            a.status
        FROM treatment_notes tn
        JOIN appointments a ON a.id = tn.appointment_id
    """
    params: list = []
    if therapist_id:
        query += " WHERE a.therapist_id = ?"
        params.append(therapist_id)
    query += f" ORDER BY {order_clause}"
    rows = get_db().execute(query, params).fetchall()
    return [dict(r) for r in rows]
