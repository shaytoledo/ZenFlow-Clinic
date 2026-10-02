"""
zenflow.schema
───────────────
The database schema as data (Phase 12.2.3, ADR-45) — one SQLAlchemy Core `MetaData`, portable
between SQLite (today) and Postgres (AWS, 12.2.2).

This mirrors, column for column and in the same order, the schema the application has always built
with raw `CREATE TABLE` / `ALTER TABLE` statements (bot/db.py and the repositories' create_schema).
`tests/integration/test_schema_baseline.py` proves the two are the same on SQLite, and Alembic's
baseline migration (`migrations/versions/0001_baseline.py`) is generated from it.

**From now on the schema changes only through an Alembic revision** (`python -m zenflow.migrate`),
and this file is kept in step with it (a test runs Alembic's autogenerate and requires no diff).

Dialect notes:
- A few old columns default to `datetime('now')` (SQLite's 'YYYY-MM-DD HH:MM:SS' UTC). `LegacyUtcNow`
  renders that per dialect. New columns get no database default: the app writes canonical UTC itself
  (ADR-19).
- Booleans stay `INTEGER` 0/1, as the code reads them (`bool(row["active"])`).
- The append-only guard on `audit_log` (ADR-31) is a trigger: `RAISE(ABORT)` on SQLite, a plpgsql
  function on Postgres.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import FunctionElement

metadata = sa.MetaData()


class LegacyUtcNow(FunctionElement[str]):
    """The `datetime('now')` default of the oldest columns, per dialect."""

    type = sa.Text()
    inherit_cache = True


@compiles(LegacyUtcNow, "sqlite")  # type: ignore[no-untyped-call, untyped-decorator]
def _utc_now_sqlite(element: Any, compiler: Any, **kw: Any) -> str:
    return "datetime('now')"


@compiles(LegacyUtcNow, "postgresql")  # type: ignore[no-untyped-call, untyped-decorator]
def _utc_now_postgres(element: Any, compiler: Any, **kw: Any) -> str:
    return "to_char(timezone('UTC', now()), 'YYYY-MM-DD HH24:MI:SS')"


def _text(name: str, *args: Any, **kw: Any) -> sa.Column[Any]:
    return sa.Column(name, sa.Text, *args, **kw)


def _int(name: str, *args: Any, **kw: Any) -> sa.Column[Any]:
    return sa.Column(name, sa.Integer, *args, **kw)


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Integer, primary_key=True)


def _zero() -> Any:
    return sa.text("0")


# ── people ───────────────────────────────────────────────────────────────────────────────────
therapists = sa.Table(
    "therapists",
    metadata,
    _text("id", primary_key=True),
    _text("name", nullable=False),
    _int("telegram_id", server_default=_zero()),
    _text("email"),
    _text("password_hash"),
    _text("google_id"),
    _text("calendar_name", server_default="ZenFlow Availability"),
    _int("active", server_default=_zero()),
    _text("created_at", server_default=LegacyUtcNow()),
    _text("language", server_default="en"),
    _text("ui_prefs"),
)

patients = sa.Table(
    "patients",
    metadata,
    _id(),
    _text("full_name", nullable=False, server_default=""),
    _text("phone"),
    _text("email"),
    _text("lang"),
    _text("notes"),
    _int("legacy_id", unique=True),
    _text("created_at", nullable=False),
    _text("updated_at", nullable=False),
    sqlite_autoincrement=True,
)

patient_channels = sa.Table(
    "patient_channels",
    metadata,
    _id(),
    _int("patient_id", sa.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False),
    _text("channel", nullable=False),
    _text("external_id", nullable=False),
    _int("is_primary", nullable=False, server_default=_zero()),
    _text("verified_at"),
    _text("created_at", nullable=False),
    sa.CheckConstraint("channel IN ('telegram','whatsapp')"),
    sa.CheckConstraint("length(external_id) BETWEEN 1 AND 64"),
    sa.CheckConstraint("is_primary IN (0, 1)"),
    sa.UniqueConstraint("channel", "external_id"),
    sa.Index("idx_patient_channels_patient", "patient_id"),
    sqlite_autoincrement=True,
)

# ── clinical record ──────────────────────────────────────────────────────────────────────────
appointments = sa.Table(
    "appointments",
    metadata,
    _id(),
    _int("patient_id", nullable=False),
    _text("patient_name", nullable=False),
    _text("therapist_id", nullable=False),
    _text("date", nullable=False),
    _text("time", nullable=False),
    _text("status", server_default="active"),
    _text("gcal_apt_event_id"),
    _text("summary"),
    _text("created_at", server_default=LegacyUtcNow()),
    _text("source", server_default="telegram"),
    _text("patient_phone"),
    _text("patient_email"),
    sa.Index(
        "ux_appointments_active_slot",
        "therapist_id",
        "date",
        "time",
        unique=True,
        sqlite_where=sa.text("status='active'"),
        postgresql_where=sa.text("status='active'"),
    ),
    sqlite_autoincrement=True,
)

intake_sessions = sa.Table(
    "intake_sessions",
    metadata,
    _id(),
    _int("appointment_id", nullable=False),
    _int("patient_id", nullable=False),
    _text("therapist_id", nullable=False),
    _text("history_json"),
    _text("created_at", server_default=LegacyUtcNow()),
    sqlite_autoincrement=True,
)

treatment_notes = sa.Table(
    "treatment_notes",
    metadata,
    _id(),
    _int("appointment_id", nullable=False, unique=True),
    _int("patient_id", nullable=False),
    _text("tcm_pattern"),
    _text("treatment_principles"),
    _int("diagnosis_certainty", server_default=_zero()),
    _text("ai_suggested_points"),
    _text("ai_recommendations"),
    _text("tongue_observation"),
    _text("pulse_observation"),
    _text("session_notes"),
    _text("used_points"),
    _text("recommendations_sent_at"),
    _text("created_at", server_default=LegacyUtcNow()),
    _text("updated_at", server_default=LegacyUtcNow()),
    _text("completed_at"),
    _int("followup_rating"),
    _text("followup_sent_at"),
    _text("therapist_diagnosis"),
    _text("therapist_notes"),
    _int("manual_feedback_rating"),
    _text("manual_feedback_notes"),
    _text("followup_conversation"),
    _text("pending_recommendations"),
    _text("pending_rec_send_at"),
    _text("points_status"),
    sqlite_autoincrement=True,
)

followups = sa.Table(
    "followups",
    metadata,
    _id(),
    _int(
        "appointment_id",
        sa.ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    ),
    _int("patient_id", nullable=False),
    _text("therapist_id", nullable=False, server_default=""),
    _text("channel", nullable=False, server_default="telegram"),
    _text("status", nullable=False, server_default="scheduled"),
    _int("auto", nullable=False, server_default=_zero()),
    _text("scheduled_for"),
    _text("sent_at"),
    _text("completed_at"),
    _int("step"),
    _int("pain_level"),
    _int("improvement_rating"),
    _text("side_effects"),
    _text("sleep_quality"),
    _text("adherence"),
    _text("free_text"),
    _text("ai_summary"),
    _int("needs_attention", nullable=False, server_default=_zero()),
    _text("conversation_json"),
    _text("source", nullable=False, server_default="patient"),
    _text("created_at", nullable=False),
    _text("updated_at", nullable=False),
    sa.CheckConstraint(
        "status IN ('scheduled','sent','in_progress','completed','expired','no_channel')"
    ),
    sa.CheckConstraint("pain_level BETWEEN 0 AND 10"),
    sa.CheckConstraint("improvement_rating BETWEEN 1 AND 5"),
    sa.CheckConstraint("sleep_quality IN ('worse','same','better')"),
    sa.CheckConstraint("adherence IN ('yes','partly','no')"),
    sa.CheckConstraint("source IN ('patient','therapist_manual')"),
    sa.Index("idx_followups_patient_status", "patient_id", "status"),
    sa.Index("idx_followups_status_sent", "status", "sent_at"),
    sqlite_autoincrement=True,
)

availability = sa.Table(
    "availability",
    metadata,
    _text("id", primary_key=True),
    _text("therapist_id", nullable=False),
    _text("start_dt", nullable=False),
    _text("end_dt", nullable=False),
)

# ── messages, notifications, audit, AI ───────────────────────────────────────────────────────
message_log = sa.Table(
    "message_log",
    metadata,
    _id(),
    _text("ts", nullable=False),
    _text("direction", nullable=False, server_default="out"),
    _text("channel", nullable=False),
    _int("patient_id"),
    _text("therapist_id", nullable=False, server_default=""),
    _int("appointment_id"),
    _text("kind", nullable=False),
    _text("status", nullable=False),
    _text("provider_message_id"),
    _text("error"),
    sa.CheckConstraint("direction IN ('out','in')"),
    sa.CheckConstraint("channel IN ('telegram','whatsapp','email')"),
    sa.CheckConstraint("kind IN ('recommendations','followup','confirmation','relay')"),
    sa.CheckConstraint("status IN ('sent','failed')"),
    sa.Index("idx_message_log_appointment", "appointment_id", "ts"),
    sqlite_autoincrement=True,
)

notifications = sa.Table(
    "notifications",
    metadata,
    _id(),
    _text("therapist_id", nullable=False),
    _text("kind", nullable=False),
    _text("severity", server_default="info"),
    _text("title", nullable=False),
    _text("body"),
    _int("appointment_id"),
    _int("patient_id"),
    _text("patient_name"),
    _int("persistent", server_default=_zero()),
    _text("read_at"),
    _text("resolved_at"),
    _text("created_at", server_default=LegacyUtcNow()),
    sa.Index("idx_notif_therapist_unread", "therapist_id", "read_at"),
    sqlite_autoincrement=True,
)

audit_log = sa.Table(
    "audit_log",
    metadata,
    _id(),
    _text("ts", nullable=False),
    _text("actor_type", nullable=False),
    _text("actor_id", nullable=False, server_default=""),
    _text("action", nullable=False),
    _text("entity_type", nullable=False),
    _text("entity_id", nullable=False),
    _text("before_json"),
    _text("after_json"),
    _text("ip"),
    _text("user_agent"),
    _text("request_id"),
    sa.CheckConstraint("actor_type IN ('therapist','patient','system','ai','api')"),
    sa.Index("idx_audit_entity", "entity_type", "entity_id", "id"),
    sa.Index("idx_audit_actor", "actor_type", "actor_id", "id"),
    sa.Index("idx_audit_ts", "ts"),
    sqlite_autoincrement=True,
)

ai_calls = sa.Table(
    "ai_calls",
    metadata,
    _id(),
    _text("ts", nullable=False),
    _int("appointment_id"),
    _text("stage", nullable=False),
    _text("provider", nullable=False, server_default=""),
    _text("model", nullable=False, server_default=""),
    _int("prompt_tokens"),
    _int("completion_tokens"),
    _int("duration_ms", nullable=False, server_default=_zero()),
    _text("status", nullable=False),
    _text("error"),
    _text("prompt_sha256", nullable=False, server_default=""),
    _text("response_sha256"),
    _text("prompt_debug"),
    _text("response_debug"),
    sa.CheckConstraint("status IN ('ok','error','timeout')"),
    sa.Index("idx_ai_calls_appointment", "appointment_id", "id"),
    sa.Index("idx_ai_calls_stage", "stage", "ts"),
    sa.Index("idx_ai_calls_ts", "ts"),
    sqlite_autoincrement=True,
)

# ── reference data ───────────────────────────────────────────────────────────────────────────
acupoints = sa.Table(
    "acupoints",
    metadata,
    _text("code", primary_key=True),
    _text("aliases", nullable=False, server_default="[]"),
    _text("name_pinyin", nullable=False, server_default=""),
    _text("name_cn", nullable=False, server_default=""),
    _text("name_en", nullable=False, server_default=""),
    _text("channel", nullable=False, server_default=""),
    _text("location", nullable=False, server_default=""),
    _text("actions", nullable=False, server_default=""),
    _text("needle_depth", nullable=False, server_default=""),
    _text("needle_angle", nullable=False, server_default=""),
    _text("contraindications", nullable=False, server_default="[]"),
    _text("translations", nullable=False, server_default="{}"),
    _text("source", nullable=False, server_default=""),
    _text("licence", nullable=False, server_default=""),
    _text("updated_at", nullable=False),
)

acupoint_images = sa.Table(
    "acupoint_images",
    metadata,
    _id(),
    _text("point_code", sa.ForeignKey("acupoints.code", ondelete="CASCADE"), nullable=False),
    _text("storage_key", nullable=False, unique=True),
    _text("thumb_key", nullable=False),
    _text("kind", nullable=False, server_default="diagram"),
    _int("width", nullable=False),
    _int("height", nullable=False),
    _text("sha256", nullable=False),
    _text("original_name", nullable=False, server_default=""),
    _text("credit", nullable=False),
    _text("licence", nullable=False),
    _text("licence_url", nullable=False, server_default=""),
    _text("source_url", nullable=False, server_default=""),
    _int("is_primary", nullable=False, server_default=_zero()),
    _text("created_at", nullable=False),
    _text("updated_at", nullable=False),
    sa.CheckConstraint("kind IN ('diagram', 'photo', '3d')"),
    sa.Index("idx_acupoint_images_code", "point_code"),
    sqlite_autoincrement=True,
)

# ── machinery: jobs, locks, bot state, sessions, API, Google ─────────────────────────────────
jobs = sa.Table(
    "jobs",
    metadata,
    _id(),
    _text("name", nullable=False),
    _text("payload_json", nullable=False, server_default="{}"),
    _text("run_at", nullable=False),
    _text("status", nullable=False, server_default="pending"),
    _int("attempts", nullable=False, server_default=_zero()),
    _int("max_attempts", nullable=False, server_default=sa.text("5")),
    _text("last_error"),
    _text("idempotency_key", unique=True),
    _text("locked_by"),
    _text("locked_at"),
    _text("created_at", nullable=False),
    _text("updated_at", nullable=False),
    _text("completed_at"),
    sa.Index("idx_jobs_claim", "status", "run_at"),
    sqlite_autoincrement=True,
)

leases = sa.Table(
    "leases",
    metadata,
    _text("name", primary_key=True),
    _text("holder", nullable=False),
    _text("expires_at", nullable=False),
)

bot_persistence = sa.Table(
    "bot_persistence",
    metadata,
    _text("kind", nullable=False),
    _text("name", nullable=False),
    _text("key", nullable=False),
    _text("value_json", nullable=False),
    _text("updated_at", nullable=False),
    sa.PrimaryKeyConstraint("kind", "name", "key"),
)

revoked_sessions = sa.Table(
    "revoked_sessions",
    metadata,
    _text("sid", primary_key=True),
    _text("revoked_at", nullable=False),
    _text("expires_at", nullable=False),
    sa.Index("idx_revoked_sessions_expiry", "expires_at"),
)

api_clients = sa.Table(
    "api_clients",
    metadata,
    _id(),
    _text("name", nullable=False, unique=True),
    _text("key_hash", nullable=False, unique=True),
    _text("created_at", nullable=False),
    _text("last_used_at"),
    _text("revoked_at"),
    sqlite_autoincrement=True,
)

api_idempotency = sa.Table(
    "api_idempotency",
    metadata,
    _text("client", nullable=False),
    _text("key", nullable=False),
    _text("request_hash", nullable=False),
    _int("status_code"),
    _text("response_json"),
    _text("created_at", nullable=False),
    sa.PrimaryKeyConstraint("client", "key"),
)

google_tokens = sa.Table(
    "google_tokens",
    metadata,
    _text("therapist_id", primary_key=True),
    _text("encrypted_token", nullable=False),
    _text("scopes"),
    _text("updated_at", server_default=LegacyUtcNow()),
)

schema_migrations = sa.Table(
    "schema_migrations",
    metadata,
    _text("name", primary_key=True),
    _text("applied_at", nullable=False),
)

# ── the view and the audit guard: plain SQL, per dialect ─────────────────────────────────────
PATIENT_CONTACTS_VIEW = """CREATE VIEW patient_contacts AS
SELECT pc.patient_id, pc.channel, pc.external_id
FROM patient_channels pc
WHERE pc.id = (
    SELECT c2.id FROM patient_channels c2
    WHERE c2.patient_id = pc.patient_id
    ORDER BY c2.is_primary DESC, c2.id DESC
    LIMIT 1
)"""

AUDIT_GUARD_SQLITE = (
    """CREATE TRIGGER audit_log_is_append_only_update
       BEFORE UPDATE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
    """CREATE TRIGGER audit_log_is_append_only_delete
       BEFORE DELETE ON audit_log
       BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END""",
)

AUDIT_GUARD_POSTGRES = (
    """CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'audit_log is append-only'; END
       $$ LANGUAGE plpgsql""",
    """CREATE TRIGGER audit_log_is_append_only_update BEFORE UPDATE ON audit_log
       FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()""",
    """CREATE TRIGGER audit_log_is_append_only_delete BEFORE DELETE ON audit_log
       FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()""",
)


def extra_ddl(dialect: str) -> tuple[str, ...]:
    """The statements tables cannot express: the contacts view and the audit guard."""
    guard = AUDIT_GUARD_POSTGRES if dialect == "postgresql" else AUDIT_GUARD_SQLITE
    return (PATIENT_CONTACTS_VIEW, *guard)


def create_all(engine: sa.engine.Engine) -> None:
    """Every table, index, the view and the audit guard, on an empty database, in one go."""
    with engine.begin() as conn:
        metadata.create_all(conn)
        for statement in extra_ddl(conn.dialect.name):
            conn.execute(sa.text(statement))
