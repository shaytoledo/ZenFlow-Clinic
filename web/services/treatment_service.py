"""
web/services/treatment_service.py
──────────────────────────────────
Domain logic for treatment notes. SQL access goes through repositories.
"""
import datetime
import logging

from web.repositories import appointment_repo, treatment_repo

logger = logging.getLogger(__name__)


def get_appointment_id(patient_id: int, apt_date: str, apt_time: str) -> int | None:
    """Resolve an appointment row ID from patient/date/time."""
    return appointment_repo.get_id(patient_id, apt_date, apt_time)


def get_notes(appointment_id: int) -> dict | None:
    """Return treatment notes for a given appointment_id, or None."""
    return treatment_repo.get_by_appointment(appointment_id)


def save_notes(appointment_id: int, patient_id: int, data: dict) -> None:
    """Upsert treatment notes for a given appointment."""
    treatment_repo.upsert(appointment_id, patient_id, data)


def complete_session(appointment_id: int, patient_id: int) -> None:
    """Mark a treatment session as completed (records completion timestamp)."""
    save_notes(appointment_id, patient_id, {
        "completed_at": datetime.datetime.now().isoformat()
    })


def list_completed_sessions(therapist_id: str | None = None) -> list[dict]:
    """List all treatment sessions that have been completed, newest first."""
    from bot.db import get_db
    query = """
        SELECT
            tn.appointment_id, tn.patient_id, tn.tcm_pattern,
            tn.diagnosis_certainty, tn.completed_at, tn.updated_at,
            a.patient_name, a.date, a.time, a.therapist_id
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
    """List every appointment (with treatment notes if any).

    Drives the /sessions page. We LEFT JOIN treatment_notes so manually-created
    appointments — which have no notes row until the therapist opens the
    treatment screen and types something — still show up in the history list.
    """
    from bot.db import get_db
    order_map = {
        "name": "a.patient_name ASC, a.date DESC",
        "date": "a.date DESC, a.time DESC",
        "last_access": "COALESCE(tn.updated_at, a.created_at) DESC",
    }
    order_clause = order_map.get(sort_by, "a.date DESC, a.time DESC")
    query = f"""
        SELECT
            a.id AS appointment_id,
            a.patient_id, a.patient_name, a.date, a.time,
            a.therapist_id, a.status, a.source,
            tn.tcm_pattern, tn.diagnosis_certainty,
            tn.completed_at, tn.updated_at, tn.session_notes
        FROM appointments a
        LEFT JOIN treatment_notes tn ON tn.appointment_id = a.id
    """
    params: list = []
    if therapist_id:
        query += " WHERE a.therapist_id = ?"
        params.append(therapist_id)
    query += f" ORDER BY {order_clause}"
    rows = get_db().execute(query, params).fetchall()
    return [dict(r) for r in rows]
