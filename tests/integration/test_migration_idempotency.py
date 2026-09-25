"""Phase 11.2 — schema-migration idempotency.

`init_db()` runs on every process start: it creates tables with `IF NOT EXISTS` and applies each
`ALTER TABLE ADD COLUMN` migration under a swallow-if-exists guard. The guarantee under test is that
re-running it on a populated database is a no-op — the schema is unchanged and no row is lost — so a
restart (or a second worker) can never corrupt or reset the data. These tests seed real clinical rows,
run `init_db()` several more times, and assert the schema snapshot and the data are byte-stable.
"""

from __future__ import annotations

import pytest

from bot.db import get_db, init_db

pytestmark = pytest.mark.integration

# Columns added by the ALTER-TABLE migration list — they must survive every re-init.
_MIGRATED_COLUMNS = {
    "treatment_notes": {
        "diagnosis_certainty",
        "completed_at",
        "therapist_diagnosis",
        "therapist_notes",
        "points_status",
        "pending_recommendations",
        "followup_conversation",
    },
    "appointments": {"source", "patient_phone", "patient_email"},
    "therapists": {"language", "ui_prefs"},
}


def _schema_snapshot() -> list[tuple[str, str]]:
    """Every table's name and its CREATE SQL, ordered — the shape of the database."""
    rows = get_db().execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    return [(r["name"], r["sql"]) for r in rows]


def _columns(table: str) -> set[str]:
    return {r["name"] for r in get_db().execute(f"PRAGMA table_info({table})")}


def test_reinit_preserves_schema_and_data(make_completed_session) -> None:
    session = make_completed_session()
    apt_id = session["id"]

    before_schema = _schema_snapshot()
    before_counts = {
        t: get_db().execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        for t in ("appointments", "therapists", "patients", "treatment_notes")
    }
    note_before = (
        get_db()
        .execute(
            "SELECT tcm_pattern, diagnosis_certainty FROM treatment_notes WHERE appointment_id=?",
            (apt_id,),
        )
        .fetchone()
    )

    # Re-run the full startup migration path several more times.
    for _ in range(3):
        init_db()

    assert _schema_snapshot() == before_schema, "the table set / DDL must not change on re-init"
    after_counts = {
        t: get_db().execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"] for t in before_counts
    }
    assert after_counts == before_counts, "no rows added or lost by re-init"
    note_after = (
        get_db()
        .execute(
            "SELECT tcm_pattern, diagnosis_certainty FROM treatment_notes WHERE appointment_id=?",
            (apt_id,),
        )
        .fetchone()
    )
    assert dict(note_after) == dict(note_before), "clinical row is untouched by re-init"


def test_all_migrated_columns_are_present_after_reinit() -> None:
    init_db()
    init_db()
    for table, expected in _MIGRATED_COLUMNS.items():
        missing = expected - _columns(table)
        assert not missing, f"{table} lost migrated columns after re-init: {missing}"


@pytest.mark.parametrize(
    "table",
    [
        "appointments",
        "therapists",
        "treatment_notes",
        "intake_sessions",
        "google_tokens",
        "notifications",
        "jobs",
        "leases",
        "bot_persistence",
        "patients",
        "patient_channels",
        "availability",
    ],
)
def test_core_tables_exist(table: str) -> None:
    row = (
        get_db()
        .execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        .fetchone()
    )
    assert row is not None, f"expected core table {table!r} to exist after init_db"
