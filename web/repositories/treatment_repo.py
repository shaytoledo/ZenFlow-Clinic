"""
web/repositories/treatment_repo.py
───────────────────────────────────
All SQL access for the `treatment_notes` table.
"""
from __future__ import annotations

import json


def _conn():
    from bot.db import get_db
    return get_db()


def _decode(row) -> dict:
    """Convert a SQLite row to a dict, parsing the JSON columns back to Python."""
    d = dict(row)
    for key in ("ai_suggested_points", "ai_recommendations", "used_points"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = [] if key != "ai_recommendations" else {}
    return d


def get_by_appointment(appointment_id: int) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM treatment_notes WHERE appointment_id=?", (appointment_id,)
    ).fetchone()
    return _decode(row) if row else None


def upsert(appointment_id: int, patient_id: int, notes: dict) -> None:
    """Insert or update treatment notes for an appointment.

    All JSON-typed fields (ai_suggested_points, ai_recommendations, used_points)
    are serialised here; callers pass them as Python lists/dicts.
    """
    _conn().execute(
        """INSERT INTO treatment_notes
           (appointment_id, patient_id, tcm_pattern, treatment_principles,
            diagnosis_certainty, ai_suggested_points, ai_recommendations,
            tongue_observation, pulse_observation, session_notes, used_points,
            recommendations_sent_at, completed_at,
            therapist_diagnosis, therapist_notes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
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
             updated_at=datetime('now')""",
        (
            appointment_id, patient_id,
            notes.get("tcm_pattern"),
            notes.get("treatment_principles"),
            int(notes["diagnosis_certainty"])
                if notes.get("diagnosis_certainty") is not None else None,
            json.dumps(notes["ai_suggested_points"], ensure_ascii=False)
                if notes.get("ai_suggested_points") is not None else None,
            json.dumps(notes["ai_recommendations"], ensure_ascii=False)
                if notes.get("ai_recommendations") is not None else None,
            notes.get("tongue_observation"),
            notes.get("pulse_observation"),
            notes.get("session_notes"),
            json.dumps(notes["used_points"], ensure_ascii=False)
                if notes.get("used_points") is not None else None,
            notes.get("recommendations_sent_at"),
            notes.get("completed_at"),
            notes.get("therapist_diagnosis"),
            notes.get("therapist_notes"),
        ),
    )


def list_completed_for_followup(window_start_iso: str, window_end_iso: str) -> list[dict]:
    """Completed sessions whose `completed_at` is in [start, end] — used by 24h follow-up."""
    rows = _conn().execute(
        """SELECT t.appointment_id, t.patient_id, a.patient_name, a.therapist_id,
                  t.completed_at, t.tcm_pattern
           FROM treatment_notes t
           JOIN appointments a ON a.id = t.appointment_id
           WHERE t.completed_at IS NOT NULL
             AND t.completed_at >= ?
             AND t.completed_at <= ?
             AND a.status='active'""",
        (window_start_iso, window_end_iso),
    ).fetchall()
    return [dict(r) for r in rows]
