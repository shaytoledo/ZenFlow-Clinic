"""Threat model A10 — the database and every copy of it are readable by their owner alone.

The SQLite file holds identifiable health data. On a shared host a 0644 database (the usual umask)
or a world-readable WAL file hands every local account the clinic's records. So:

- a new database file is created 0600 before SQLite opens it, and its -wal/-shm side files
  (which SQLite creates with the database file's own mode) follow;
- an existing database that is open to other users is tightened to 0600 on first open, with a
  warning naming the old mode;
- backups (plain and encrypted), decrypted restores and patient exports are written 0600 from the
  first byte.

POSIX only — on Windows a file inherits the user profile's ACL, which already excludes other users;
CI (Linux) runs these.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

import bot.db as dbmod

pytestmark = [
    pytest.mark.security,
    pytest.mark.skipif(os.name != "posix", reason="POSIX file modes (CI runs on Linux)"),
]

KEY = "backup-key-" + "p" * 40


def _mode(path: str | Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture
def loose_umask() -> Iterator[None]:
    """The common default umask: without the fix every new file would be 0644."""
    old = os.umask(0o022)
    yield
    os.umask(old)


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "fresh" / "zenflow.db"
    monkeypatch.setenv("ZENFLOW_DB_PATH", str(path))
    dbmod.close_db()
    yield path
    dbmod.close_db()


def test_a_new_database_and_its_wal_are_owner_only(fresh_db: Path, loose_umask) -> None:
    conn = dbmod.get_db()
    conn.execute("CREATE TABLE t (x TEXT)")
    conn.execute("INSERT INTO t VALUES ('a write creates the WAL')")
    assert _mode(fresh_db) == 0o600
    for side in ("-wal", "-shm"):
        side_file = fresh_db.with_name(fresh_db.name + side)
        assert side_file.exists(), f"WAL mode should have created {side}"
        assert _mode(side_file) == 0o600, side


def test_the_data_directory_is_created_owner_only(fresh_db: Path, loose_umask) -> None:
    dbmod.get_db()
    assert _mode(fresh_db.parent) & 0o077 == 0


def test_an_existing_world_readable_database_is_tightened_with_a_warning(
    fresh_db: Path, loose_umask, caplog
) -> None:
    fresh_db.parent.mkdir(parents=True)
    fresh_db.touch()
    os.chmod(fresh_db, 0o644)
    with caplog.at_level(logging.WARNING, logger="bot.db"):
        dbmod.get_db()
    assert _mode(fresh_db) == 0o600
    assert "0o644" in caplog.text


def test_restrict_to_owner_never_raises_on_a_missing_file(tmp_path: Path) -> None:
    assert dbmod.restrict_to_owner(tmp_path / "nope.db") is False


def test_backups_are_owner_only(loose_umask, monkeypatch: pytest.MonkeyPatch) -> None:
    import zenflow.settings as settings_mod
    from zenflow.db_backup import backup_database

    dbmod.get_db()
    assert _mode(backup_database()) == 0o600
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", KEY)
    settings_mod.reset_settings()
    try:
        assert _mode(backup_database(encrypt=True)) == 0o600
    finally:
        settings_mod.reset_settings()


def test_restores_and_exports_are_owner_only(
    loose_umask, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_patient
) -> None:
    import zenflow.settings as settings_mod
    from zenflow.db_backup import backup_database
    from zenflow.file_crypto import decrypt_file
    from zenflow.patient_export import main as export_main

    patient = make_patient("Owner Only")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", KEY)
    settings_mod.reset_settings()
    try:
        restored = decrypt_file(backup_database(encrypt=True), tmp_path / "restored.db")
        assert _mode(restored) == 0o600
        out = tmp_path / "export.json"
        assert export_main([str(patient["patient_id"]), "--out", str(out)]) == 0
        assert _mode(out) == 0o600
        assert export_main([str(patient["patient_id"]), "--out", str(out), "--encrypt"]) == 0
        assert _mode(str(out) + ".enc") == 0o600
    finally:
        settings_mod.reset_settings()


def test_overwriting_an_open_export_tightens_it_before_writing(
    loose_umask, tmp_path: Path, make_patient
) -> None:
    from zenflow.patient_export import main as export_main

    patient = make_patient("Owner Only")
    out = tmp_path / "export.json"
    out.write_text("old")
    os.chmod(out, 0o644)
    assert export_main([str(patient["patient_id"]), "--out", str(out)]) == 0
    assert _mode(out) == 0o600
