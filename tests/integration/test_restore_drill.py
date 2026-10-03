"""Phase 12.2.7 — backups restore, and the drill notices when one does not.

The drill backs up the live database with the production code path, restores it into a scratch
copy and compares every table (row count + a hash of the rows). On the single host an automatic
backup runs every ZF_BACKUP_HOURS and keeps ZF_BACKUP_KEEP.
"""

from __future__ import annotations

import json
import shutil
from datetime import timedelta
from pathlib import Path

import pytest

import bot.db as dbmod
from zenflow import db_backup, restore_drill

pytestmark = pytest.mark.integration
KEY = "backup-key-" + "k" * 40


@pytest.fixture
def clinic_data(make_completed_session) -> None:
    """Rows in the clinical tables, the audit trail and the jobs table — what a backup must keep."""
    from web.services import audit

    for _ in range(3):
        make_completed_session()
    audit.record("appointment.created", "appointment", 1, after={"time": "10:00"})


def _with_key(monkeypatch: pytest.MonkeyPatch, key: str | None) -> None:
    from zenflow.settings import reset_settings

    if key:
        monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", key)
    else:
        monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    reset_settings()


@pytest.mark.parametrize("key", [None, KEY], ids=["plain", "encrypted"])
def test_a_backup_restores_with_every_row(db, clinic_data, monkeypatch, key) -> None:
    if dbmod.is_postgres() and not shutil.which("pg_restore"):
        pytest.skip("pg_dump/pg_restore are not installed here")
    _with_key(monkeypatch, key)
    report = restore_drill.drill()
    assert report["ok"], report["differences"]
    assert report["encrypted"] is bool(key)
    assert report["rows"] >= 7 and report["tables"] >= 20
    assert report["alembic"] is not None
    leftovers = list(Path(dbmod.db_path()).parent.glob(f"{Path(dbmod.db_path()).name}.drill-*"))
    assert leftovers == [], "the drill cleans up its backup"


@pytest.mark.sqlite_only  # the corruption is injected into the SQLite restore step
def test_the_drill_fails_when_the_restore_lost_a_row(db, clinic_data, monkeypatch, capsys) -> None:
    real = restore_drill._restore_sqlite

    def lossy(data: bytes, workdir: Path):  # a backup that silently dropped one appointment
        import sqlite3

        fp, where = real(data, workdir)
        conn = sqlite3.connect(where)
        conn.execute("DELETE FROM appointments WHERE id = (SELECT MAX(id) FROM appointments)")
        conn.commit()
        fp = restore_drill.fingerprint(conn)
        conn.close()
        return fp, where

    monkeypatch.setattr(restore_drill, "_restore_sqlite", lossy)
    assert restore_drill.main([]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert any(d.startswith("appointments:") for d in report["differences"])


def test_the_fingerprint_sees_a_changed_value_not_just_a_count(db, clinic_data) -> None:
    conn = dbmod.get_db()
    before = restore_drill.fingerprint(conn)
    conn.execute(
        "UPDATE appointments SET time='23:59' WHERE id = (SELECT MIN(id) FROM appointments)"
    )
    after = restore_drill.fingerprint(conn)
    assert before["appointments"]["rows"] == after["appointments"]["rows"]
    assert restore_drill.differences(before, after)


def test_the_postgres_password_never_reaches_the_command_line(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")
    cmd, env = db_backup.pg_command(
        "pg_dump", "postgresql+psycopg://zf:s3cr%3At@db.internal:5432/zenflow?sslmode=require"
    )
    assert not any("s3cr" in part for part in cmd)
    assert cmd[-1] == "postgresql://zf@db.internal:5432/zenflow?sslmode=require"
    assert env["PGPASSWORD"] == "s3cr%3At"


# ── the automatic backup on the single host ──
@pytest.mark.sqlite_only  # on Postgres RDS backs itself up; the task skips (tested below)
def test_an_automatic_backup_is_taken_when_due_and_old_ones_are_pruned(
    db, monkeypatch, frozen_clock
) -> None:
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_BACKUP_HOURS", "24")
    monkeypatch.setenv("ZF_BACKUP_KEEP", "2")
    _with_key(monkeypatch, KEY)
    reset_settings()
    first = db_backup.backup_if_due()
    assert first and first.endswith(".enc"), "encrypted when the key is set"
    assert db_backup.backup_if_due() is None, "not again within the period"
    for _ in range(3):
        frozen_clock.tick(timedelta(hours=25))
        assert db_backup.backup_if_due()
    kept = db_backup.automatic_backups()
    assert len(kept) == 2 and Path(first) not in kept, "only the newest ZF_BACKUP_KEEP stay"
    for path in kept:
        assert (path.stat().st_mode & 0o077) == 0 or Path(path).drive, "owner-only"


def test_automatic_backups_can_be_turned_off_and_skip_postgres(db, monkeypatch) -> None:
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_BACKUP_HOURS", "0")
    reset_settings()
    assert db_backup.backup_if_due() is None and db_backup.automatic_backups() == []
    monkeypatch.setenv("ZF_BACKUP_HOURS", "24")
    reset_settings()
    monkeypatch.setattr(dbmod, "is_postgres", lambda: True)
    assert db_backup.backup_if_due() is None, "RDS snapshots + PITR, not a file on the task"


def test_the_backup_task_runs_once_per_hour_across_workers() -> None:
    from zenflow import periodic
    from zenflow.worker import DEFAULT_HANDLER_MODULES

    assert "zenflow.db_backup" in DEFAULT_HANDLER_MODULES
    assert periodic._TASKS["db.backup"].every_seconds == 3600
