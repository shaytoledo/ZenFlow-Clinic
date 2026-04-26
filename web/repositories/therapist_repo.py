"""
web/repositories/therapist_repo.py
───────────────────────────────────
All SQL access for the `therapists` table.
"""
from __future__ import annotations

from typing import Any


def _conn():
    from bot.db import get_db
    return get_db()


def list_all() -> list[dict]:
    """Return every therapist row as a dict."""
    rows = _conn().execute("SELECT * FROM therapists").fetchall()
    return [dict(r) for r in rows]


def get_by_id(therapist_id: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM therapists WHERE id=?", (therapist_id,)
    ).fetchone()
    return dict(row) if row else None


def get_by_email(email: str) -> dict | None:
    if not email:
        return None
    row = _conn().execute(
        "SELECT * FROM therapists WHERE LOWER(email)=LOWER(?) LIMIT 1", (email,)
    ).fetchone()
    return dict(row) if row else None


def get_by_google_id(google_id: str) -> dict | None:
    if not google_id:
        return None
    row = _conn().execute(
        "SELECT * FROM therapists WHERE google_id=? LIMIT 1", (google_id,)
    ).fetchone()
    return dict(row) if row else None


def get_by_telegram_id(telegram_id: int) -> dict | None:
    if not telegram_id:
        return None
    row = _conn().execute(
        "SELECT * FROM therapists WHERE telegram_id=? LIMIT 1", (telegram_id,)
    ).fetchone()
    return dict(row) if row else None


def insert(entry: dict[str, Any]) -> str:
    """Insert a new therapist. Returns the assigned id."""
    _conn().execute(
        """INSERT INTO therapists
           (id, name, telegram_id, email, password_hash, google_id, calendar_name, active)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            entry["id"],
            entry.get("name", ""),
            int(entry.get("telegram_id") or 0),
            entry.get("email"),
            entry.get("password_hash"),
            entry.get("google_id"),
            entry.get("calendar_name") or "ZenFlow Availability",
            int(bool(entry.get("active"))),
        ),
    )
    return entry["id"]


def update_activation(therapist_id: str, telegram_id: int) -> None:
    """Mark a therapist active and link their Telegram ID (called from bot)."""
    _conn().execute(
        "UPDATE therapists SET telegram_id=?, active=1 WHERE id=?",
        (int(telegram_id), therapist_id),
    )


def update_calendar_name(therapist_id: str, name: str) -> None:
    _conn().execute(
        "UPDATE therapists SET calendar_name=? WHERE id=?", (name, therapist_id)
    )


def next_id() -> str:
    """Generate the next sequential therapist id (t1, t2, …)."""
    row = _conn().execute(
        "SELECT id FROM therapists WHERE id LIKE 't%' ORDER BY id"
    ).fetchall()
    nums = []
    for r in row:
        try:
            nums.append(int(r[0][1:]))
        except (ValueError, IndexError):
            pass
    return f"t{(max(nums) + 1) if nums else 1}"
