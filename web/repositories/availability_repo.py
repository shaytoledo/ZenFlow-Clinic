"""
web/repositories/availability_repo.py
──────────────────────────────────────
All SQL access for the local-mode `availability` table (used when a therapist
has not connected Google Calendar).
"""
from __future__ import annotations

import secrets


def _conn():
    from bot.db import get_db
    return get_db()


def list_for_therapist(therapist_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM availability WHERE therapist_id=? ORDER BY start_dt",
        (therapist_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def insert(therapist_id: str, start_dt: str, end_dt: str) -> str:
    """Create one availability row. Returns the slot id."""
    slot_id = "loc_" + secrets.token_hex(6)
    _conn().execute(
        """INSERT INTO availability (id, therapist_id, start_dt, end_dt)
           VALUES (?, ?, ?, ?)""",
        (slot_id, therapist_id, start_dt, end_dt),
    )
    return slot_id


def delete(slot_id: str) -> None:
    _conn().execute("DELETE FROM availability WHERE id=?", (slot_id,))
