"""zenflow.clock — the ONE clock (Phase 1.1, ADR-19).

Rules
-----
* Every instant is stored as the canonical string ``YYYY-MM-DDTHH:MM:SSZ`` (UTC, seconds,
  ``Z`` suffix). Canonical strings compare correctly with ``<``/``>`` in SQL and in Python.
* SQL that stamps "now" uses :data:`SQL_NOW`, never ``datetime('now')`` (which is UTC but
  space-separated and therefore not comparable with the canonical form).
* Python never calls ``datetime.now()`` / ``date.today()`` (ruff ``DTZ`` rules enforce it):
  use :func:`now_utc`, :func:`iso_now`, :func:`today` (clinic-local calendar date).
* Reads accept every legacy shape (:func:`parse_iso`); the one-time migration
  ``python -m zenflow.migrate_timestamps`` rewrites stored legacy values to canonical.

Calendar dates such as ``appointments.date`` / ``appointments.time`` are *clinic wall-clock*
values, not instants; they stay as they are and :func:`today` is the clinic-local date.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

CANONICAL_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
CANONICAL_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

#: SQL expression producing the canonical form for "now" (SQLite's clock is UTC).
SQL_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"

_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


# ── now ──────────────────────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    """Current instant, timezone-aware UTC (freezegun-friendly)."""
    return datetime.now(UTC)


def iso_now() -> str:
    return to_iso(now_utc())


def clinic_tz() -> tzinfo:
    from zenflow.settings import get_settings

    return ZoneInfo(get_settings().clinic_tz)


def now_local() -> datetime:
    """Current instant in the clinic's timezone."""
    return now_utc().astimezone(clinic_tz())


def today() -> date:
    """The clinic's calendar date right now (NOT the host's local date, NOT the UTC date)."""
    return now_local().date()


# ── formatting / parsing ─────────────────────────────────────────────────────────────────────
def to_iso(dt: datetime) -> str:
    """Canonical string for an aware datetime. Naive input is a bug and is refused."""
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError("to_iso() requires a timezone-aware datetime (got naive)")
    return dt.astimezone(UTC).replace(microsecond=0).strftime(CANONICAL_FORMAT)


def is_canonical(value: str | None) -> bool:
    return bool(value) and bool(CANONICAL_RE.match(value or ""))


def parse_iso(value: str, *, naive_tz: tzinfo = UTC) -> datetime:
    """Parse any timestamp shape this system has ever written; return aware UTC.

    Accepted: canonical ``…Z``; ``+00:00`` / other offsets; SQLite ``YYYY-MM-DD HH:MM:SS``
    (UTC); naive ISO with ``T`` (interpreted in ``naive_tz``, default UTC); fractional seconds.
    """
    s = (value or "").strip()
    if not s:
        raise ValueError("empty timestamp")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=naive_tz)
    return dt.astimezone(UTC)


def normalize(value: str, *, naive_tz: tzinfo = UTC) -> str:
    """Canonical string for any accepted input (see :func:`parse_iso`)."""
    return to_iso(parse_iso(value, naive_tz=naive_tz))


def hours_ago(hours: float) -> str:
    return to_iso(now_utc() - timedelta(hours=hours))


def hours_ahead(hours: float) -> str:
    return to_iso(now_utc() + timedelta(hours=hours))


# ── legacy classification (used by the migration and by tests) ───────────────────────────────
def classify(value: str | None) -> str:
    """'canonical' | 'aware' | 'sqlite-utc' | 'naive-local' | 'empty' | 'unparseable'."""
    if value is None or not str(value).strip():
        return "empty"
    s = str(value).strip()
    if CANONICAL_RE.match(s):
        return "canonical"
    try:
        datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
    except ValueError:
        return "unparseable"
    if _OFFSET_RE.search(s):
        return "aware"
    if "T" not in s and " " in s:
        return "sqlite-utc"  # written by SQLite datetime('now') — UTC, space-separated
    return "naive-local"  # written by Python datetime.now().isoformat() — host-local, no tz
