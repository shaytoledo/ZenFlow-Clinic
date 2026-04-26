"""
web/repositories/intake_repo.py
────────────────────────────────
All SQL access for the `intake_sessions` table.
"""
from __future__ import annotations

import json


def _conn():
    from bot.db import get_db
    return get_db()


def get_for_appointment(appointment_id: int) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM intake_sessions WHERE appointment_id=? LIMIT 1",
        (appointment_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("history_json"):
        try:
            d["history"] = json.loads(d["history_json"])
        except Exception:
            d["history"] = []
    else:
        d["history"] = []
    return d


def insert(appointment_id: int, patient_id: int, therapist_id: str, history: list[dict]) -> None:
    _conn().execute(
        """INSERT INTO intake_sessions
           (appointment_id, patient_id, therapist_id, history_json)
           VALUES (?, ?, ?, ?)""",
        (appointment_id, patient_id, therapist_id,
         json.dumps(history, ensure_ascii=False)),
    )
