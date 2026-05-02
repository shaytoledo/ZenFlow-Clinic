"""
web/repositories/patient_repo.py
──────────────────────────────────
Full patient history: appointments JOIN treatment_notes JOIN intake_sessions.
"""
from __future__ import annotations

import json


def _conn():
    from bot.db import get_db
    return get_db()


def _parse_json_cols(d: dict) -> dict:
    for key in ("ai_suggested_points", "used_points"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = []
    for key in ("ai_recommendations", "followup_conversation"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = None
    return d


def get_full_history(patient_id: int) -> dict | None:
    """Return patient summary + all appointments with treatment and intake data.

    Returns None if the patient_id does not exist.
    """
    rows = _conn().execute(
        """SELECT a.id            AS appointment_id,
                  a.patient_id,
                  a.patient_name,
                  a.therapist_id,
                  a.date,
                  a.time,
                  a.status,
                  a.summary,
                  a.source,
                  a.patient_phone,
                  a.created_at   AS appointment_created_at,
                  t.tcm_pattern,
                  t.treatment_principles,
                  t.diagnosis_certainty,
                  t.ai_suggested_points,
                  t.ai_recommendations,
                  t.tongue_observation,
                  t.pulse_observation,
                  t.session_notes,
                  t.used_points,
                  t.recommendations_sent_at,
                  t.completed_at,
                  t.followup_rating,
                  t.followup_sent_at,
                  t.followup_conversation,
                  t.therapist_diagnosis,
                  t.therapist_notes,
                  t.manual_feedback_rating,
                  t.manual_feedback_notes,
                  i.history_json AS intake_history_json
           FROM appointments a
           LEFT JOIN treatment_notes t ON t.appointment_id = a.id
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id = ?
             AND a.status = 'active'
           ORDER BY a.date DESC, a.time DESC""",
        (patient_id,),
    ).fetchall()

    if not rows:
        return None

    appointments = []
    for row in rows:
        d = _parse_json_cols(dict(row))
        intake_raw = d.pop("intake_history_json", None)
        d["intake_history"] = json.loads(intake_raw) if intake_raw else []
        appointments.append(d)

    first = appointments[0]
    return {
        "patient_id": patient_id,
        "name": first["patient_name"],
        "source": first.get("source") or "telegram",
        "patient_phone": first.get("patient_phone"),
        "appointments": appointments,
    }


def get_single_appointment(patient_id: int, appointment_id: int) -> dict | None:
    """Fetch one appointment's full record for a patient."""
    history = get_full_history(patient_id)
    if not history:
        return None
    return next(
        (a for a in history["appointments"] if a["appointment_id"] == appointment_id),
        None,
    )
