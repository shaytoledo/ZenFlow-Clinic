"""
bot/therapists.py — the therapist registry, read from the database on every call (Phase 12.2.4).

Before 12.2.4 the registry was three module-level containers in `bot.config` (`THERAPISTS`,
`THERAPIST_MAP`, `THERAPIST_BY_ID`), loaded at import and mutated in place from four places. Every
process had its own copy: a therapist activated through the bot stayed unknown to the web process
until a restart, and with several containers (AWS) to every other container. The table holds a
handful of rows, so one indexed query per lookup is cheaper than any cache-invalidation scheme, and
it can never be stale.

    active()               the active therapists, in registration order (the patient's choice list)
    get(id)                one therapist, active or not
    get_active(id)         one therapist, only while active (where patients are routed)
    get_by_telegram(uid)   the active therapist linked to this Telegram account (relay, commands)
"""

from __future__ import annotations

from typing import Any

#: registration order: t9 before t10 (string order would not be)
_ORDER = "ORDER BY created_at, length(id), id"


def _rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    from bot.db import get_db

    out = []
    for row in get_db().execute(sql, params).fetchall():
        therapist = dict(row)
        therapist["active"] = bool(therapist.get("active"))  # INTEGER 0/1 in the database
        out.append(therapist)
    return out


def active() -> list[dict[str, Any]]:
    return _rows(f"SELECT * FROM therapists WHERE active = 1 {_ORDER}")  # noqa: S608 - constant


def get(therapist_id: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM therapists WHERE id = ?", (therapist_id,))
    return rows[0] if rows else None


def get_active(therapist_id: str) -> dict[str, Any] | None:
    rows = _rows("SELECT * FROM therapists WHERE id = ? AND active = 1", (therapist_id,))
    return rows[0] if rows else None


def get_by_telegram(telegram_id: int | None) -> dict[str, Any] | None:
    if not telegram_id:
        return None
    rows = _rows(
        f"SELECT * FROM therapists WHERE telegram_id = ? AND active = 1 {_ORDER}",  # noqa: S608
        (int(telegram_id),),
    )
    return rows[0] if rows else None
