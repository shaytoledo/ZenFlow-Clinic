"""Phase 0.3 — SQLite harness tests.

The bot config opens the database at import time, so the test harness must be able to point
`bot.db` at a throw-away file BEFORE anything imports `bot.config`. These tests pin that contract.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import bot.db as dbmod


def _columns(conn: sqlite3.Connection) -> dict[str, list[str]]:
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: [c[1] for c in conn.execute(f"PRAGMA table_info({t})")] for t in sorted(tables)}


def test_db_path_is_injectable_and_never_the_real_data_file(db: Path) -> None:
    assert dbmod.db_path() == db
    assert db.exists()
    real = Path(dbmod.__file__).resolve().parent.parent / "data" / "zenflow.db"
    assert dbmod.db_path().resolve() != real.resolve()


def test_each_test_gets_a_fresh_database(db: Path) -> None:
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM therapists").fetchone()[0] == 0
    conn.execute("INSERT INTO therapists (id, name) VALUES ('tX', 'leak-check')")


def test_previous_test_data_did_not_leak(db: Path) -> None:
    conn = dbmod.get_db()
    assert conn.execute("SELECT COUNT(*) FROM therapists").fetchone()[0] == 0


def test_get_db_reconnects_when_the_path_changes(db: Path, tmp_path: Path, monkeypatch) -> None:
    first = dbmod.get_db()
    other = tmp_path / "other.db"
    monkeypatch.setenv("ZENFLOW_DB_PATH", str(other))
    second = dbmod.get_db()
    assert second is not first
    assert dbmod.db_path() == other
    dbmod.close_db()


def test_init_db_is_idempotent(db: Path) -> None:
    conn = dbmod.get_db()
    before = _columns(conn)
    dbmod.init_db()
    dbmod.init_db()
    after = _columns(conn)
    assert after == before
    assert set(after) >= {
        "therapists",
        "appointments",
        "intake_sessions",
        "availability",
        "treatment_notes",
        "google_tokens",
        "notifications",
    }


def test_schema_matches_what_the_repositories_select(db: Path) -> None:
    """Every read path in web/repositories must run against the freshly-initialised schema.

    A missing column shows up here as sqlite3.OperationalError, not in production.
    """
    from web.repositories import (
        appointment_repo,
        availability_repo,
        intake_repo,
        notification_repo,
        patient_repo,
        therapist_repo,
        treatment_repo,
    )

    calls = [
        appointment_repo.list_all,
        lambda: appointment_repo.list_by_patient(1),
        lambda: appointment_repo.list_active_by_patient(1),
        lambda: appointment_repo.get_by_patient_date_time(1, "2026-01-01", "10:00"),
        lambda: appointment_repo.get_id(1, "2026-01-01", "10:00"),
        lambda: appointment_repo.search_patients("x"),
        lambda: appointment_repo.search_patients(""),
        lambda: appointment_repo.list_in_date_range("t1", "2026-01-01", "2026-12-31"),
        lambda: appointment_repo.list_completed_in_window("2026-01-01", "2026-12-31"),
        lambda: availability_repo.list_for_therapist("t1"),
        lambda: intake_repo.get_for_appointment(1),
        lambda: notification_repo.list_for_therapist("t1"),
        lambda: notification_repo.unread_count("t1"),
        lambda: notification_repo.find_active_missing_contact("t1", 1),
        lambda: patient_repo.get_full_history(1),
        therapist_repo.list_all,
        lambda: therapist_repo.get_by_id("t1"),
        lambda: therapist_repo.get_by_email("a@b.c"),
        lambda: therapist_repo.get_by_google_id("g"),
        lambda: therapist_repo.get_by_telegram_id(1),
        therapist_repo.next_id,
        lambda: treatment_repo.get_by_appointment(1),
        lambda: treatment_repo.list_due_pending_recommendations("2026-01-01T00:00:00"),
        lambda: treatment_repo.list_completed_for_followup("2026-01-01", "2026-12-31"),
    ]
    for call in calls:
        call()  # raises sqlite3.OperationalError on schema drift
