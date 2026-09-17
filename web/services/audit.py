"""
web/services/audit.py
──────────────────────
The audit trail (Phase 8.1): what happened to a patient's data, when, and who did it.

    from web.services import audit
    audit.record("appointment.cancelled", "appointment", apt_id, before=row)

**Append-only.** Two SQLite triggers refuse an UPDATE or a DELETE, so a trail nobody can edit is
a property of the database rather than a promise in a review.

**The actor** is whoever the change really came from, and is carried in a context variable so a
call deep in a service does not have to pass it down:

| Actor | Set by |
|---|---|
| `therapist` | the dashboard's request middleware, from the session |
| `api` | the booking API's guard, from the API key |
| `patient` | the bot, when a patient books, cancels or answers |
| `ai` | the generation pipeline around its writes |
| `system` | the default: jobs, sweeps, start-up backfills |

**Best effort.** A failed insert is logged and swallowed: losing an audit row must never lose the
appointment it describes. The trail is evidence, not a lock.

**Retention.** Rows are kept as long as the clinical record they describe — they are part of it.
Nothing prunes them today; a retention policy belongs with the patient-data review (plan 9.9, Q5).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, NamedTuple

from zenflow import clock
from zenflow.logging import get_context, redact

logger = logging.getLogger(__name__)

ACTOR_TYPES = ("therapist", "patient", "system", "ai", "api")
MAX_JSON_LEN = 4000
#: values whose name says they must never be copied into the trail
_SECRET_KEYS = ("password", "token", "secret", "key_hash", "encrypted")

CREATE_AUDIT_LOG = """CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('therapist','patient','system','ai','api')),
    actor_id TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT,
    ip TEXT,
    user_agent TEXT,
    request_id TEXT
)"""
CREATE_AUDIT_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_type, actor_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts)",
)
#: the append-only guarantee, enforced by the database itself
CREATE_AUDIT_GUARDS = (
    """CREATE TRIGGER IF NOT EXISTS audit_log_is_append_only_update
       BEFORE UPDATE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_log_is_append_only_delete
       BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
)


class Actor(NamedTuple):
    actor_type: str = "system"
    actor_id: str = ""
    ip: str | None = None
    user_agent: str | None = None


_actor: ContextVar[Actor] = ContextVar("zenflow_audit_actor", default=Actor())


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_AUDIT_LOG)
    for statement in (*CREATE_AUDIT_INDEXES, *CREATE_AUDIT_GUARDS):
        conn.execute(statement)


# ── who is acting ──
def current_actor() -> Actor:
    return _actor.get()


def set_actor(
    actor_type: str, actor_id: Any = "", *, ip: str | None = None, user_agent: str | None = None
) -> None:
    """Name the actor for the rest of this context (a request, a job, a handler)."""
    _actor.set(_as_actor(actor_type, actor_id, ip, user_agent))


@contextmanager
def acting_as(
    actor_type: str, actor_id: Any = "", *, ip: str | None = None, user_agent: str | None = None
) -> Iterator[None]:
    token = _actor.set(_as_actor(actor_type, actor_id, ip, user_agent))
    try:
        yield
    finally:
        _actor.reset(token)


def _as_actor(actor_type: str, actor_id: Any, ip: str | None, user_agent: str | None) -> Actor:
    kind = actor_type if actor_type in ACTOR_TYPES else "system"
    return Actor(kind, "" if actor_id is None else str(actor_id), ip, _trim(user_agent, 200))


# ── writing ──
def record(
    action: str,
    entity_type: str,
    entity_id: Any,
    *,
    before: Any = None,
    after: Any = None,
    actor: Actor | None = None,
) -> None:
    """Append one row. Never raises: the change it describes has already happened."""
    who = actor or current_actor()
    try:
        _insert(
            (
                clock.iso_now(),
                who.actor_type,
                who.actor_id,
                action,
                entity_type,
                str(entity_id),
                _payload(before),
                _payload(after),
                who.ip,
                who.user_agent,
                get_context().get("request_id"),
            )
        )
    except Exception:
        logger.exception("audit row not written: %s %s %s", action, entity_type, entity_id)


def _insert(values: tuple[Any, ...]) -> None:
    _conn().execute(
        """INSERT INTO audit_log (ts, actor_type, actor_id, action, entity_type, entity_id,
                                  before_json, after_json, ip, user_agent, request_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )


#: bookkeeping columns move on every write and say nothing about the edit
BOOKKEEPING = ("created_at", "updated_at", "id")


def changes(before: Any, after: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The fields that actually changed, so a row shows the edit and not the whole record."""
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    keys = [k for k in after if k not in BOOKKEEPING and before.get(k) != after.get(k)]
    return ({k: before.get(k) for k in keys if k in before}, {k: after[k] for k in keys})


def _payload(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        value = {k: _safe(k, v) for k, v in value.items()}
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return redact(_trim(text, MAX_JSON_LEN) or "")


def _safe(key: str, value: Any) -> Any:
    return "<redacted>" if any(s in key.lower() for s in _SECRET_KEYS) else value


def _trim(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ── reading (8.5 surfaces this per appointment) ──
def for_entity(entity_type: str, entity_id: Any, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        _conn()
        .execute(
            """SELECT * FROM audit_log WHERE entity_type=? AND entity_id=?
               ORDER BY id DESC LIMIT ?""",
            (entity_type, str(entity_id), int(limit)),
        )
        .fetchall()
    )
    return [dict(r) for r in rows]
