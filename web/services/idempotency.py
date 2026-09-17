"""
web/services/idempotency.py
────────────────────────────
`Idempotency-Key` for the booking API (Phase 7.3).

A client that retries after a timeout must not create a second appointment. The first request
under a key claims it; the answer is stored and replayed to every later request with the same key
and the same body. A different body under the same key is a mistake, and is refused.

Entries are kept for `RETENTION_HOURS`, which is far longer than any client's retry window.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, NamedTuple

from zenflow import clock

RETENTION_HOURS = 24
MAX_KEY_LEN = 128

CREATE_API_IDEMPOTENCY = """CREATE TABLE IF NOT EXISTS api_idempotency (
    client TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status_code INTEGER,
    response_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (client, key)
)"""


class Replay(NamedTuple):
    status_code: int
    body: dict[str, Any]


class KeyReused(Exception):
    """The same key came back with a different request."""


class InProgress(Exception):
    """The first request under this key has not answered yet."""


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_API_IDEMPOTENCY)


def request_hash(payload: Any) -> str:
    """A stable hash of the request body (key order and spacing do not matter)."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()


def begin(client: str, key: str, payload: Any) -> Replay | None:
    """Claim `key` for this request, or return the answer the first one got.

    Raises `KeyReused` (a different body) or `InProgress` (the first request is still running).
    """
    from web.repositories import patient_repo  # savepoint helper

    conn = _conn()
    digest = request_hash(payload)
    with patient_repo.atomic(conn, "idempotency"):
        _purge(conn)
        row = conn.execute(
            "SELECT * FROM api_idempotency WHERE client=? AND key=?", (client, key)
        ).fetchone()
        if row is not None:
            if row["request_hash"] != digest:
                raise KeyReused(key)
            if row["status_code"] is None:
                raise InProgress(key)
            return Replay(int(row["status_code"]), json.loads(row["response_json"] or "{}"))
        conn.execute(
            """INSERT INTO api_idempotency (client, key, request_hash, created_at)
               VALUES (?, ?, ?, ?)""",
            (client, key, digest, clock.iso_now()),
        )
    return None


def finish(client: str, key: str, status_code: int, body: Any) -> None:
    """Store the answer this key's request produced (a refusal is an answer too)."""
    _conn().execute(
        """UPDATE api_idempotency SET status_code=?, response_json=?
           WHERE client=? AND key=?""",
        (int(status_code), json.dumps(body, ensure_ascii=False, default=str), client, key),
    )


def abandon(client: str, key: str) -> None:
    """The request failed in a way that says nothing about a retry — let the key be used again."""
    _conn().execute(
        "DELETE FROM api_idempotency WHERE client=? AND key=? AND status_code IS NULL",
        (client, key),
    )


def _purge(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM api_idempotency WHERE created_at < ?", (clock.hours_ago(RETENTION_HOURS),)
    )
