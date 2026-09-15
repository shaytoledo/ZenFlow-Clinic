"""
web/repositories/intake_repo.py
────────────────────────────────
All SQL access for the `intake_sessions` table.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from zenflow.clock import SQL_NOW


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def get_for_appointment(appointment_id: int) -> dict[str, Any] | None:
    row = (
        _conn()
        .execute(
            "SELECT * FROM intake_sessions WHERE appointment_id=? LIMIT 1",
            (appointment_id,),
        )
        .fetchone()
    )
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


def insert(
    appointment_id: int, patient_id: int, therapist_id: str, history: list[dict[str, Any]]
) -> None:
    _conn().execute(
        f"""INSERT INTO intake_sessions
           (appointment_id, patient_id, therapist_id, history_json, created_at)
           VALUES (?, ?, ?, ?, {SQL_NOW})""",
        (appointment_id, patient_id, therapist_id, json.dumps(history, ensure_ascii=False)),
    )
