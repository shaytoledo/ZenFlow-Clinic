"""
web/services/availability_service.py
──────────────────────────────────────
CRUD abstraction for local availability slots (SQLite `availability` table).
Google Calendar operations remain in web/gcal.py.
"""

import logging
import uuid

logger = logging.getLogger(__name__)


def list_local(therapist_id: str | None) -> list[dict]:
    """Read all local availability slots for a therapist."""
    from bot.db import get_db

    rows = (
        get_db()
        .execute(
            "SELECT id, start_dt AS start, end_dt AS end FROM availability WHERE therapist_id=?",
            (therapist_id or "default",),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]


def add_local(therapist_id: str, start: str, end: str) -> dict:
    """Insert a new local availability slot and return the FullCalendar-ready dict."""
    from bot.db import get_db

    new_id = uuid.uuid4().hex
    conn = get_db()
    conn.execute(
        "INSERT INTO availability (id, therapist_id, start_dt, end_dt) VALUES (?, ?, ?, ?)",
        (new_id, therapist_id or "default", start, end),
    )
    conn.commit()
    # Invalidate bot availability cache for this therapist
    try:
        from bot.redis_client import get_sync_redis

        r = get_sync_redis()
        for key in r.scan_iter(f"zenflow:avail:*:{therapist_id}:*"):
            r.delete(key)
    except Exception:
        pass
    return to_fc_event({"id": new_id, "start": start, "end": end})


def remove_local(slot_id: str, therapist_id: str | None = None) -> int:
    """Delete a local availability slot by ID (tenant-scoped when `therapist_id` is given).

    Returns the number of rows deleted (0 = not found / not yours).
    """
    from web.repositories import availability_repo

    return availability_repo.delete(slot_id, therapist_id)


def to_fc_event(slot: dict) -> dict:
    """Convert a local slot dict to a FullCalendar event dict."""
    return {
        "id": slot["id"],
        "title": "✅ Available",
        "start": slot["start"],
        "end": slot["end"],
        "backgroundColor": "#27ae60",
        "borderColor": "#1e8449",
        "editable": False,
        "extendedProps": {"type": "available", "calendarId": "local"},
    }


def to_fc_events(slots: list[dict]) -> list[dict]:
    return [to_fc_event(s) for s in slots]
