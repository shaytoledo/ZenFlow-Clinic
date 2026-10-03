"""Phase 12.2.2 — `zenflow.pg.Connection` behaves like the sqlite3 connection where the code relies on it.

Runs only when the suite is pointed at Postgres (`ZF_TEST_DB_URL=postgresql://…`, CI's
`postgres-suite` job); on SQLite these rules are sqlite3's own and the rest of the suite covers them.
"""

from __future__ import annotations

import os
import re
from typing import Any

import pytest

import bot.db as dbmod
from zenflow.clock import SQL_NOW

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not dbmod.is_postgres(), reason="the Postgres adapter needs ZF_TEST_DB_URL"),
]


def _other_connection() -> Any:
    """A second session: proves what was committed, not what this session can see."""
    from zenflow.pg import Connection

    return Connection(dbmod.db_url())


def _patients(conn: Any) -> list[str]:
    return [r["full_name"] for r in conn.execute("SELECT full_name FROM patients ORDER BY id")]


def test_a_savepoint_outside_a_transaction_opens_one_and_its_release_commits(db) -> None:
    from web.repositories import patient_repo

    conn = dbmod.get_db()
    with patient_repo.atomic(conn, "first_contact"):
        conn.execute(
            "INSERT INTO patients (full_name, created_at, updated_at) VALUES (?, ?, ?)",
            ("A", "x", "x"),
        )
        assert conn.in_transaction
    assert not conn.in_transaction
    other = _other_connection()
    try:
        assert _patients(other) == ["A"]
    finally:
        other.close()


def test_a_failed_savepoint_block_leaves_nothing_and_the_connection_usable(db) -> None:
    from web.repositories import patient_repo

    conn = dbmod.get_db()
    with pytest.raises(dbmod.IntegrityError), patient_repo.atomic(conn, "first_contact"):
        conn.execute(
            "INSERT INTO patients (full_name, created_at, updated_at) VALUES (?, ?, ?)",
            ("B", "x", "x"),
        )
        conn.execute(
            "INSERT INTO patients (id, full_name, created_at, updated_at) VALUES (1, ?, ?, ?)",
            ("dup", "x", "x"),
        )
    assert not conn.in_transaction
    assert _patients(conn) == []
    conn.execute(
        "INSERT INTO patients (full_name, created_at, updated_at) VALUES (?, ?, ?)", ("C", "x", "x")
    )
    assert _patients(conn) == ["C"]


def test_a_savepoint_inside_a_callers_transaction_does_not_commit_it(db) -> None:
    from web.repositories import patient_repo

    conn = dbmod.get_db()
    conn.execute("BEGIN")
    with patient_repo.atomic(conn, "link_channel"):
        conn.execute(
            "INSERT INTO patients (full_name, created_at, updated_at) VALUES (?, ?, ?)",
            ("D", "x", "x"),
        )
    assert (
        conn.in_transaction
    ), "RELEASE of an inner savepoint must leave the caller's transaction open"
    conn.execute("ROLLBACK")
    assert _patients(conn) == []


def test_an_insert_reports_its_new_id_like_sqlite_lastrowid(db) -> None:
    conn = dbmod.get_db()
    first = conn.execute(
        "INSERT INTO patients (full_name, created_at, updated_at) VALUES (?, ?, ?)", ("E", "x", "x")
    )
    second = conn.execute(
        "INSERT INTO patients (full_name, created_at, updated_at) VALUES (?, ?, ?)", ("F", "x", "x")
    )
    assert first.lastrowid and second.lastrowid == first.lastrowid + 1
    # a table keyed by text has no id to return — and must not be asked for one
    cur = conn.execute(
        "INSERT INTO leases (name, holder, expires_at) VALUES (?, ?, ?)", ("l", "h", "x")
    )
    assert cur.lastrowid is None and cur.rowcount == 1


def test_the_audit_trail_refuses_changes_with_a_database_error(db) -> None:
    from web.services import audit

    audit.record("appointment.created", "appointment", 1, after={"time": "10:00"})
    conn = dbmod.get_db()
    with pytest.raises(dbmod.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_log SET action='x'")
    with pytest.raises(dbmod.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_log")


def test_the_canonical_now_and_averages_come_back_as_sqlite_gave_them(db) -> None:
    conn = dbmod.get_db()
    now = conn.execute(f"SELECT {SQL_NOW}").fetchone()[0]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", now)
    avg = conn.execute("SELECT AVG(x) FROM (VALUES (1), (2)) AS v(x)").fetchone()[0]
    assert isinstance(avg, float) and avg == 1.5


def test_a_postgres_backup_is_an_owner_only_pg_dump_or_a_clear_refusal(db, monkeypatch) -> None:
    """12.2.7: pg_dump when it is installed; without it, a refusal that says so (exit 2)."""
    import shutil

    from zenflow.db_backup import BackupUnavailable, backup_database, main

    if shutil.which("pg_dump"):
        path = backup_database()
        try:
            assert path.endswith(".pgdump")
            with open(path, "rb") as dump:
                assert dump.read(5) == b"PGDMP", "pg_dump's custom format"
        finally:
            os.remove(path)
    monkeypatch.setattr(shutil, "which", lambda _tool: None)
    with pytest.raises(BackupUnavailable, match="pg_dump is not on PATH"):
        backup_database()
    assert main([]) == 2


def test_token_rotation_keeps_the_rows_it_rewrites(db, tmp_path) -> None:
    """On Postgres there is no file to copy: rotation keeps the old (encrypted) rows instead."""
    import json
    from pathlib import Path

    from zenflow import token_key

    old, new = "old-material-" + "o" * 40, "new-material-" + "n" * 40
    blob = token_key.fernet_for(old).encrypt(b"{}").decode()
    dbmod.get_db().execute(
        "INSERT INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at) "
        "VALUES ('t1', ?, '', '2026-01-01T00:00:00Z')",
        (blob,),
    )
    report = token_key.rotate(old_material=old, new_material=new, dry_run=False)
    assert report.rotated == 1 and report.backup_path
    kept = json.loads(Path(report.backup_path).read_text(encoding="utf-8"))
    assert kept[0]["therapist_id"] == "t1" and kept[0]["encrypted_token"] == blob
