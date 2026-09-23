"""
web/session_policy.py
──────────────────────
When a dashboard session begins, how long it may live, and how it ends (Phase 9.2).

The session is a **signed cookie**: the server keeps no copy, which is why it survives restarts and
scales without shared state — and why the three things below have to be built deliberately.

**It begins clean.** `start()` clears whatever was in the session before writing the therapist's
id, so nothing an anonymous visitor planted (an OAuth `next`, a registration marker) can survive
into a signed-in session — session fixation.

**It ends by time.** Two limits, both checked on every authenticated request: an *idle* limit
(`ZF_SESSION_IDLE_MINUTES`, 12 hours) and an *absolute* one (`ZF_SESSION_MAX_HOURS`, 7 days). A
clinic's dashboard shows medical records on a screen in a treatment room; a session that never
expires is a record left open.

**It ends on logout, for real.** A cookie cannot be taken back — a copy taken before signing out
still verifies. So `revoke()` writes the session's id into `revoked_sessions`, and every request
checks it. The row is kept only until the session would have expired anyway, so the table stays
the size of "people who signed out this week", and `prune()` empties it.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from typing import Any

from zenflow import clock

logger = logging.getLogger(__name__)

THERAPIST_KEY = "therapist_id"
SID_KEY = "sid"
STARTED_KEY = "started_at"
SEEN_KEY = "seen_at"
#: how stale `seen_at` may get before it is rewritten — a Set-Cookie on every request is noise
REFRESH_SECONDS = 60

CREATE_REVOKED_SESSIONS = """CREATE TABLE IF NOT EXISTS revoked_sessions (
    sid TEXT PRIMARY KEY,
    revoked_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
)"""
CREATE_REVOKED_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_revoked_sessions_expiry ON revoked_sessions(expires_at)"
)


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_REVOKED_SESSIONS)
    conn.execute(CREATE_REVOKED_INDEX)


# ── the policy's numbers ──
def idle_minutes() -> int:
    from zenflow.settings import get_settings

    return int(get_settings().flags.session_idle_minutes)


def max_hours() -> int:
    from zenflow.settings import get_settings

    return int(get_settings().flags.session_max_hours)


# ── beginning ──
def start(session: dict[str, Any], therapist_id: str) -> None:
    """Sign in: a new session, keeping nothing from the old one."""
    session.clear()
    now = clock.iso_now()
    session.update(
        {
            THERAPIST_KEY: therapist_id,
            SID_KEY: secrets.token_urlsafe(18),
            STARTED_KEY: now,
            SEEN_KEY: now,
        }
    )


# ── living ──
def is_usable(session: dict[str, Any]) -> bool:
    """May this session act? Refreshes `seen_at` when it may, clears it when it may not.

    A session from before 9.2 has no stamps: it is treated as starting now, so nobody is signed
    out by a deployment.
    """
    if not session.get(THERAPIST_KEY):
        return False
    now = clock.now_utc()
    started = _parse(session.get(STARTED_KEY))
    seen = _parse(session.get(SEEN_KEY))
    if started is None or seen is None:  # pre-9.2 cookie: adopt it
        session[STARTED_KEY] = session[SEEN_KEY] = clock.iso_now()
        session.setdefault(SID_KEY, secrets.token_urlsafe(18))
        return not is_revoked(str(session.get(SID_KEY) or ""))

    if (now - started).total_seconds() > max_hours() * 3600:
        return _expire(session, "absolute")
    if (now - seen).total_seconds() > idle_minutes() * 60:
        return _expire(session, "idle")
    if is_revoked(str(session.get(SID_KEY) or "")):
        return _expire(session, "revoked")

    if (now - seen).total_seconds() > REFRESH_SECONDS:
        session[SEEN_KEY] = clock.iso_now()
    return True


def _expire(session: dict[str, Any], why: str) -> bool:
    logger.info("session ended (%s)", why)
    session.clear()
    return False


# ── ending ──
def revoke(session: dict[str, Any]) -> None:
    """Sign out. The cookie is cleared *and* remembered, because a copy of it still verifies."""
    sid = str(session.get(SID_KEY) or "")
    session.clear()
    if not sid:
        return
    try:
        _conn().execute(
            """INSERT INTO revoked_sessions (sid, revoked_at, expires_at) VALUES (?, ?, ?)
               ON CONFLICT(sid) DO UPDATE SET revoked_at=excluded.revoked_at""",
            (sid, clock.iso_now(), clock.hours_ahead(max_hours())),
        )
    except Exception:  # signing out must never fail
        logger.exception("session %s not added to the revocation list", sid[:6])


def is_revoked(sid: str) -> bool:
    if not sid:
        return False
    try:
        row = _conn().execute("SELECT 1 FROM revoked_sessions WHERE sid=?", (sid,)).fetchone()
    except Exception:  # an unreadable table must not lock everyone out
        logger.exception("revocation list unreadable")
        return False
    return row is not None


def prune(now_iso: str | None = None) -> int:
    """Forget revocations that can no longer matter. Returns how many rows went."""
    cursor = _conn().execute(
        "DELETE FROM revoked_sessions WHERE expires_at <= ?", (now_iso or clock.iso_now(),)
    )
    return int(cursor.rowcount or 0)


def _parse(value: Any) -> Any:
    if not value:
        return None
    try:
        return clock.parse_iso(str(value))
    except Exception:
        return None
