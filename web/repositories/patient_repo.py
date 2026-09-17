"""
web/repositories/patient_repo.py
──────────────────────────────────
Patients (Phase 7.2): who a patient is, independent of any chat platform.

* `patients` — one row per person, with an internal id. `appointments.patient_id` (and every
  table that copies it) holds this id.
* `patient_channels` — how to message them: `(channel, external_id)`, e.g. a Telegram user id.
  A patient without a row here cannot be messaged (a manual booking); linking one later makes
  the same patient reachable without changing their id.
* `patients.legacy_id` — the value `appointments.patient_id` held before 7.2 (a Telegram id, or
  a negative number for a manual booking). Kept for one release so old links still resolve.

Also: the full patient history for the profile page.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

from zenflow import clock

logger = logging.getLogger(__name__)

#: channels a patient can be messaged on (their adapters live in bot/interfaces)
MESSAGING_CHANNELS = ("telegram", "whatsapp")
MAX_EXTERNAL_ID_LEN = 64
MIGRATION = "0001_patient_identity"
#: every table that stores the patient id of an appointment
PATIENT_ID_TABLES = (
    "appointments",
    "treatment_notes",
    "intake_sessions",
    "followups",
    "message_log",
    "notifications",
)

CREATE_PATIENTS = """CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL DEFAULT '',
    phone TEXT,
    email TEXT,
    lang TEXT,
    notes TEXT,
    legacy_id INTEGER UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""
CREATE_PATIENT_CHANNELS = """CREATE TABLE IF NOT EXISTS patient_channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('telegram','whatsapp')),
    external_id TEXT NOT NULL CHECK (length(external_id) BETWEEN 1 AND 64),
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    verified_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (channel, external_id)
)"""
CREATE_PATIENT_CHANNELS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_patient_channels_patient ON patient_channels(patient_id)"
)
#: each patient's messaging contact: the primary channel, else the newest one
CREATE_PATIENT_CONTACTS_VIEW = """CREATE VIEW IF NOT EXISTS patient_contacts AS
SELECT pc.patient_id, pc.channel, pc.external_id
FROM patient_channels pc
WHERE pc.id = (
    SELECT c2.id FROM patient_channels c2
    WHERE c2.patient_id = pc.patient_id
    ORDER BY c2.is_primary DESC, c2.id DESC
    LIMIT 1
)"""
CREATE_SCHEMA_MIGRATIONS = """CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)"""


class Contact(NamedTuple):
    channel: str
    external_id: str


class ChannelTaken(ValueError):
    """That channel identity already belongs to another patient."""


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


@contextmanager
def atomic(conn: sqlite3.Connection, name: str) -> Iterator[None]:
    """All-or-nothing, inside or outside a caller's transaction (a savepoint)."""
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    conn.execute(f"RELEASE {name}")


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_PATIENTS)
    conn.execute(CREATE_PATIENT_CHANNELS)
    conn.execute(CREATE_PATIENT_CHANNELS_INDEX)
    conn.execute(CREATE_PATIENT_CONTACTS_VIEW)
    conn.execute(CREATE_SCHEMA_MIGRATIONS)


# ── patients ──
def create(
    full_name: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    lang: str | None = None,
    notes: str | None = None,
) -> int:
    """A new patient with no messaging channel. Returns the internal id."""
    now = clock.iso_now()
    cur = _conn().execute(
        """INSERT INTO patients (full_name, phone, email, lang, notes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        ((full_name or "").strip(), _blank(phone), _blank(email), lang, notes, now, now),
    )
    return int(cur.lastrowid or 0)


def get(patient_id: int) -> dict[str, Any] | None:
    row = _conn().execute("SELECT * FROM patients WHERE id=?", (int(patient_id),)).fetchone()
    return dict(row) if row else None


def update_contact(patient_id: int, *, phone: str | None = None, email: str | None = None) -> None:
    """Record a phone number or email address, keeping any value not given."""
    _conn().execute(
        """UPDATE patients SET phone = COALESCE(?, phone), email = COALESCE(?, email),
                               updated_at = ?
           WHERE id = ?""",
        (_blank(phone), _blank(email), clock.iso_now(), int(patient_id)),
    )


def belongs_to_therapist(patient_id: int, therapist_id: str) -> bool:
    """Has this therapist ever booked this patient? (A therapist sees only their own patients.)"""
    row = (
        _conn()
        .execute(
            "SELECT 1 FROM appointments WHERE patient_id=? AND therapist_id=? LIMIT 1",
            (int(patient_id), therapist_id),
        )
        .fetchone()
    )
    return row is not None


# ── channels ──
def find_by_channel(channel: str, external_id: str | int) -> int | None:
    row = (
        _conn()
        .execute(
            "SELECT patient_id FROM patient_channels WHERE channel=? AND external_id=?",
            (channel, _external(external_id)),
        )
        .fetchone()
    )
    return int(row["patient_id"]) if row else None


def for_channel(channel: str, external_id: str | int, full_name: str = "") -> int:
    """The patient behind a channel identity — created on first contact.

    A name is only filled in when the patient has none: a name the clinic already knows is not
    replaced by whatever the person calls themselves in the app.
    """
    conn = _conn()
    ext = _external(external_id)
    existing = find_by_channel(channel, ext)
    if existing is None:
        now = clock.iso_now()
        try:
            with atomic(conn, "first_contact"):
                cur = conn.execute(
                    """INSERT INTO patients (full_name, created_at, updated_at)
                       VALUES (?, ?, ?)""",
                    ((full_name or "").strip(), now, now),
                )
                created = int(cur.lastrowid or 0)
                conn.execute(
                    """INSERT INTO patient_channels
                       (patient_id, channel, external_id, is_primary, verified_at, created_at)
                       VALUES (?, ?, ?, 1, ?, ?)""",
                    (created, channel, ext, now, now),
                )
        except sqlite3.IntegrityError:
            owner = find_by_channel(channel, ext)  # a concurrent first contact won
            if owner is None:
                raise
            return owner
        return created
    if (full_name or "").strip():
        conn.execute(
            """UPDATE patients SET full_name = ?, updated_at = ?
               WHERE id = ? AND full_name = ''""",
            (full_name.strip(), clock.iso_now(), existing),
        )
    return existing


def link_channel(
    patient_id: int, channel: str, external_id: str | int, *, primary: bool = True
) -> None:
    """Attach a channel identity to a patient. Raises ChannelTaken if someone else has it."""
    ext = _external(external_id)
    owner = find_by_channel(channel, ext)
    if owner is not None and owner != int(patient_id):
        raise ChannelTaken(f"{channel} identity already belongs to another patient")
    conn = _conn()
    now = clock.iso_now()
    with atomic(conn, "link_channel"):
        if primary:
            conn.execute(
                "UPDATE patient_channels SET is_primary=0 WHERE patient_id=?", (int(patient_id),)
            )
        conn.execute(
            """INSERT INTO patient_channels
               (patient_id, channel, external_id, is_primary, verified_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(channel, external_id) DO UPDATE SET is_primary = excluded.is_primary""",
            (int(patient_id), channel, ext, 1 if primary else 0, now, now),
        )


def messaging_contact(patient_id: int) -> Contact | None:
    """Where to message this patient, or None when they have no messaging channel."""
    row = (
        _conn()
        .execute(
            "SELECT channel, external_id FROM patient_contacts WHERE patient_id=?",
            (int(patient_id),),
        )
        .fetchone()
    )
    return Contact(str(row["channel"]), str(row["external_id"])) if row else None


# ── the pre-7.2 ids (one release of compatibility) ──
def canonical_id(value: int) -> int:
    """An internal patient id for `value`: itself when it is one, else the patient whose
    pre-7.2 id it was. Unknown values come back unchanged (the caller answers 404)."""
    conn = _conn()
    if conn.execute("SELECT 1 FROM patients WHERE id=?", (int(value),)).fetchone():
        return int(value)
    row = conn.execute("SELECT id FROM patients WHERE legacy_id=?", (int(value),)).fetchone()
    return int(row["id"]) if row else int(value)


def migration_applied(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT 1 FROM schema_migrations WHERE name=?", (MIGRATION,)).fetchone()
    return row is not None


def migrate_legacy_ids(conn: sqlite3.Connection, db_file: Path | None) -> dict[str, int]:
    """Give every pre-7.2 patient id a `patients` row and rewrite every table to the new ids.

    Runs once (a `schema_migrations` row), in one transaction, after a backup of a non-empty
    database. Any failure rolls everything back and raises: the process must not start with
    old and new ids mixed.
    """
    if migration_applied(conn):
        return {}
    has_rows = conn.execute("SELECT EXISTS (SELECT 1 FROM appointments)").fetchone()[0]
    backup = _backup(conn, db_file) if has_rows and db_file is not None else None
    now = clock.iso_now()
    ids_union = " UNION ".join(
        f"SELECT patient_id AS pid FROM {table} WHERE patient_id IS NOT NULL"  # nosec B608
        for table in PATIENT_ID_TABLES
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        # table names come from the PATIENT_ID_TABLES constant, never from input
        conn.execute(
            f"""INSERT INTO patients (full_name, phone, email, legacy_id, created_at, updated_at)
               SELECT
                 COALESCE((SELECT a.patient_name FROM appointments a WHERE a.patient_id = ids.pid
                           ORDER BY a.date DESC, a.time DESC, a.id DESC LIMIT 1), ''),
                 (SELECT a.patient_phone FROM appointments a
                  WHERE a.patient_id = ids.pid AND COALESCE(a.patient_phone, '') <> ''
                  ORDER BY a.date DESC, a.id DESC LIMIT 1),
                 (SELECT a.patient_email FROM appointments a
                  WHERE a.patient_id = ids.pid AND COALESCE(a.patient_email, '') <> ''
                  ORDER BY a.date DESC, a.id DESC LIMIT 1),
                 ids.pid, ?, ?
               FROM ({ids_union}) ids
               WHERE NOT EXISTS (SELECT 1 FROM patients p WHERE p.legacy_id = ids.pid)
               ORDER BY ids.pid""",  # nosec B608
            (now, now),
        )
        # Positive pre-7.2 ids were Telegram user ids; negative ones were manual bookings.
        conn.execute(
            """INSERT OR IGNORE INTO patient_channels
               (patient_id, channel, external_id, is_primary, verified_at, created_at)
               SELECT id, 'telegram', CAST(legacy_id AS TEXT), 1, ?, ?
               FROM patients WHERE legacy_id > 0""",
            (now, now),
        )
        counts: dict[str, int] = {}
        for table in PATIENT_ID_TABLES:
            cur = conn.execute(f"""UPDATE {table}
                   SET patient_id = (SELECT p.id FROM patients p
                                     WHERE p.legacy_id = {table}.patient_id)
                   WHERE patient_id IS NOT NULL
                     AND EXISTS (SELECT 1 FROM patients p
                                 WHERE p.legacy_id = {table}.patient_id)""")  # nosec B608
            counts[table] = cur.rowcount
        counts["patients"] = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        conn.execute(
            "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)", (MIGRATION, now)
        )
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise RuntimeError(
            f"patient identity migration failed ({exc}); the database is unchanged"
            + (f" — backup at {backup}" if backup else "")
        ) from exc
    logger.info("patient identity migration applied: %s (backup: %s)", counts, backup or "none")
    return counts


def _backup(conn: sqlite3.Connection, db_file: Path) -> Path:
    stamp = clock.now_utc().strftime("%Y%m%dT%H%M%SZ")
    dest_path = db_file.with_name(f"{db_file.name}.pre-patient-identity-{stamp}")
    dest = sqlite3.connect(dest_path)
    try:
        conn.backup(dest)
    finally:
        dest.close()
    return dest_path


def _external(external_id: str | int) -> str:
    value = str(external_id).strip()
    if not value or len(value) > MAX_EXTERNAL_ID_LEN:
        raise ValueError(f"a channel identity must be 1–{MAX_EXTERNAL_ID_LEN} characters")
    return value


def _blank(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


# ── history (the profile page) ──
def _parse_json_cols(d: dict[str, Any]) -> dict[str, Any]:
    for key in ("ai_suggested_points", "used_points"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = []
    for key in ("ai_recommendations", "followup_conversation"):
        val = d.get(key)
        if isinstance(val, str):
            try:
                d[key] = json.loads(val)
            except Exception:
                d[key] = None
    return d


def get_full_history(patient_id: int, therapist_id: str | None = None) -> dict[str, Any] | None:
    """Return patient summary + all appointments with treatment and intake data.

    Returns None if the patient_id does not exist — or, when `therapist_id` is given, has no
    appointment with that therapist (tenant scoping, F6).
    """
    rows = (
        _conn()
        .execute(
            """SELECT a.id            AS appointment_id,
                  a.patient_id,
                  a.patient_name,
                  a.therapist_id,
                  a.date,
                  a.time,
                  a.status,
                  a.summary,
                  a.source,
                  a.patient_phone,
                  a.created_at   AS appointment_created_at,
                  t.tcm_pattern,
                  t.treatment_principles,
                  t.diagnosis_certainty,
                  t.ai_suggested_points,
                  t.ai_recommendations,
                  t.tongue_observation,
                  t.pulse_observation,
                  t.session_notes,
                  t.used_points,
                  t.recommendations_sent_at,
                  t.completed_at,
                  t.followup_rating,
                  t.followup_sent_at,
                  t.followup_conversation,
                  t.therapist_diagnosis,
                  t.therapist_notes,
                  t.manual_feedback_rating,
                  t.manual_feedback_notes,
                  i.history_json AS intake_history_json
           FROM appointments a
           LEFT JOIN treatment_notes t ON t.appointment_id = a.id
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id = ?
             AND a.status = 'active'
           """
            + (" AND a.therapist_id = ? " if therapist_id else "")
            + " ORDER BY a.date DESC, a.time DESC",
            (patient_id, therapist_id) if therapist_id else (patient_id,),
        )
        .fetchall()
    )

    if not rows:
        return None

    appointments = []
    for row in rows:
        d = _parse_json_cols(dict(row))
        intake_raw = d.pop("intake_history_json", None)
        d["intake_history"] = json.loads(intake_raw) if intake_raw else []
        appointments.append(d)

    first = appointments[0]
    contact = messaging_contact(patient_id)
    return {
        "patient_id": patient_id,
        "name": first["patient_name"],
        "source": first.get("source") or "telegram",
        "patient_phone": first.get("patient_phone"),
        "channel": contact.channel if contact else None,
        "appointments": appointments,
    }


def get_single_appointment(patient_id: int, appointment_id: int) -> dict[str, Any] | None:
    """Fetch one appointment's full record for a patient."""
    history = get_full_history(patient_id)
    if not history:
        return None
    return next(
        (a for a in history["appointments"] if a["appointment_id"] == appointment_id),
        None,
    )
