"""
web/repositories/therapist_repo.py
───────────────────────────────────
All SQL access for the `therapists` table.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from zenflow.clock import SQL_NOW


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def list_all() -> list[dict[str, Any]]:
    """Return every therapist row as a dict."""
    rows = _conn().execute("SELECT * FROM therapists").fetchall()
    return [dict(r) for r in rows]


def get_by_id(therapist_id: str) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM therapists WHERE id=?", (therapist_id,)).fetchone()
    return dict(row) if row else None


def get_by_email(email: str) -> dict[str, Any] | None:
    if not email:
        return None
    row = (
        _conn()
        .execute("SELECT * FROM therapists WHERE LOWER(email)=LOWER(?) LIMIT 1", (email,))
        .fetchone()
    )
    return dict(row) if row else None


def get_by_google_id(google_id: str) -> dict[str, Any] | None:
    if not google_id:
        return None
    row = (
        _conn()
        .execute("SELECT * FROM therapists WHERE google_id=? LIMIT 1", (google_id,))
        .fetchone()
    )
    return dict(row) if row else None


def get_by_telegram_id(telegram_id: int) -> dict[str, Any] | None:
    if not telegram_id:
        return None
    row = (
        _conn()
        .execute("SELECT * FROM therapists WHERE telegram_id=? LIMIT 1", (telegram_id,))
        .fetchone()
    )
    return dict(row) if row else None


def insert(entry: dict[str, Any]) -> str:
    """Insert a new therapist. Returns the assigned id."""
    _conn().execute(
        f"""INSERT INTO therapists
           (id, name, telegram_id, email, password_hash, google_id, calendar_name, active,
            created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, {SQL_NOW})""",
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
    therapist_id: str = entry["id"]
    return therapist_id


def update_activation(therapist_id: str, telegram_id: int) -> None:
    """Mark a therapist active and link their Telegram ID (called from bot)."""
    _conn().execute(
        "UPDATE therapists SET telegram_id=?, active=1 WHERE id=?",
        (int(telegram_id), therapist_id),
    )


def update_calendar_name(therapist_id: str, name: str) -> None:
    _conn().execute("UPDATE therapists SET calendar_name=? WHERE id=?", (name, therapist_id))


def next_id() -> str:
    """Generate the next sequential therapist id (t1, t2, …)."""
    row = _conn().execute("SELECT id FROM therapists WHERE id LIKE 't%' ORDER BY id").fetchall()
    nums = []
    for r in row:
        with contextlib.suppress(ValueError, IndexError):
            nums.append(int(r[0][1:]))
    return f"t{(max(nums) + 1) if nums else 1}"


# ── UI preferences (Phase 4.2d) ────────────────────────────────────────────────
#: The only preferences stored, each with its allowed values; the first value is the default.
UI_PREF_CHOICES: dict[str, tuple[str, ...]] = {
    "point_density": ("detailed", "compact"),
}


def _default_ui_prefs() -> dict[str, str]:
    return {key: choices[0] for key, choices in UI_PREF_CHOICES.items()}


def get_ui_prefs(therapist_id: str) -> dict[str, str]:
    """The therapist's preferences; anything missing, unknown or invalid reads as the default."""
    prefs = _default_ui_prefs()
    row = _conn().execute("SELECT ui_prefs FROM therapists WHERE id=?", (therapist_id,)).fetchone()
    try:
        stored = json.loads(row[0]) if row and row[0] else {}
    except ValueError:
        stored = {}
    if isinstance(stored, dict):
        for key, choices in UI_PREF_CHOICES.items():
            if stored.get(key) in choices:
                prefs[key] = stored[key]
    return prefs


def update_ui_prefs(therapist_id: str, updates: Mapping[str, object]) -> dict[str, str]:
    """Merge `updates` into the stored preferences and return the result.

    Raises ValueError, and saves nothing, unless every key is known and every value allowed.
    """
    if not updates:
        raise ValueError("no preference given")
    for key, value in updates.items():
        if key not in UI_PREF_CHOICES:
            raise ValueError(f"unknown preference: {key}")
        if value not in UI_PREF_CHOICES[key]:
            raise ValueError(f"{key} must be one of {', '.join(UI_PREF_CHOICES[key])}")
    prefs = get_ui_prefs(therapist_id)
    prefs.update({key: str(value) for key, value in updates.items()})
    _conn().execute(
        "UPDATE therapists SET ui_prefs=? WHERE id=?",
        (json.dumps(prefs, sort_keys=True), therapist_id),
    )
    return prefs
