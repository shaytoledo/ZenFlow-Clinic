"""
web/repositories/api_client_repo.py
────────────────────────────────────
API keys for machine clients of the booking API (Phase 7.3).

A key is `zf_` + 32 random URL-safe bytes, shown once when it is created and stored only as a
SHA-256 hash: a leaked database gives nobody a working key. Keys are clinic-wide — they may book
for any therapist — so they belong to servers the clinic runs (the WhatsApp bridge, a website),
never to a browser.

Manage them with `python -m zenflow.api_keys`.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from typing import Any

from zenflow import clock

KEY_PREFIX = "zf_"
KEY_BYTES = 32

CREATE_API_CLIENTS = """CREATE TABLE IF NOT EXISTS api_clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    key_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
)"""


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_API_CLIENTS)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def create(name: str) -> tuple[int, str]:
    """A new client and its key. The key is returned once and never stored."""
    name = (name or "").strip()
    if not name:
        raise ValueError("an API client needs a name")
    key = KEY_PREFIX + secrets.token_urlsafe(KEY_BYTES)
    cur = _conn().execute(
        "INSERT INTO api_clients (name, key_hash, created_at) VALUES (?, ?, ?)",
        (name, key_hash(key), clock.iso_now()),
    )
    return int(cur.lastrowid or 0), key


def verify(key: str) -> dict[str, Any] | None:
    """The client behind a key, or None when it is unknown or revoked."""
    if not key or not key.startswith(KEY_PREFIX):
        return None
    row = (
        _conn()
        .execute(
            "SELECT * FROM api_clients WHERE key_hash=? AND revoked_at IS NULL",
            (key_hash(key),),
        )
        .fetchone()
    )
    if row is None:
        return None
    _conn().execute(
        "UPDATE api_clients SET last_used_at=? WHERE id=?", (clock.iso_now(), row["id"])
    )
    return dict(row)


def revoke(name: str) -> bool:
    cur = _conn().execute(
        "UPDATE api_clients SET revoked_at=? WHERE name=? AND revoked_at IS NULL",
        (clock.iso_now(), name),
    )
    return bool(cur.rowcount)


def list_clients() -> list[dict[str, Any]]:
    rows = _conn().execute("SELECT * FROM api_clients ORDER BY id").fetchall()
    return [dict(r) for r in rows]
