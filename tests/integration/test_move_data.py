"""Phase 12.2.9 — the data moves between databases intact, and the move can be reversed.

`zenflow.move_data` copies every table in foreign-key order inside one transaction, keeps ids,
resets Postgres sequences, and verifies (row count + SHA-256 per table) that source and target are
identical. The source is never modified, so rolling back is pointing the app at it again; data
written after the cut-over goes back with the same tool, pointed the other way.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import bot.db as dbmod
from zenflow import move_data
from zenflow.restore_drill import fingerprint

pytestmark = pytest.mark.integration
KEY = "backup-key-" + "k" * 40


@pytest.fixture
def clinic(make_completed_session, make_patient) -> None:
    from web.services import audit
    from zenflow.queue import get_default_queue

    for _ in range(4):
        make_completed_session()
    make_patient("No visits yet")
    audit.record("appointment.created", "appointment", 1, after={"time": "10:00"})
    get_default_queue().enqueue("followup.send", {"appointment_id": 1}, idempotency_key="k1")


def test_the_app_database_moves_into_an_empty_one_identically(db, clinic, tmp_path) -> None:
    before = fingerprint(dbmod.get_db())
    report = move_data.move("app", str(tmp_path / "moved.db"))
    assert report["ok"], report["differences"]
    assert report["rows"]["appointments"] == 4 and report["rows"]["audit_log"] >= 1
    assert fingerprint(dbmod.get_db()) == before, "the source is never modified"


def test_a_target_that_holds_data_is_refused(db, clinic, tmp_path) -> None:
    target = str(tmp_path / "moved.db")
    assert move_data.move("app", target)["ok"]
    with pytest.raises(move_data.MoveRefused, match="already holds data"):
        move_data.move("app", target)


def test_a_missing_source_is_refused(db, tmp_path) -> None:
    assert (
        move_data.main(["--from", str(tmp_path / "nope.db"), "--to", str(tmp_path / "t.db")]) == 2
    )


# ── through S3 (the cut-over and the rollback on AWS run as one-off tasks) ──
@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    moto = pytest.importorskip("moto")
    from zenflow.settings import reset_settings
    from zenflow.storage import s3_client

    for name, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_DEFAULT_REGION": "il-central-1",
        "S3_REGION": "il-central-1",
        "BACKUP_ENCRYPTION_KEY": KEY,
    }.items():
        monkeypatch.setenv(name, value)
    reset_settings()
    s3_client.cache_clear()
    with moto.mock_aws():
        client = s3_client(region="il-central-1")
        client.create_bucket(
            Bucket="clinic-media", CreateBucketConfiguration={"LocationConstraint": "il-central-1"}
        )
        yield client
    s3_client.cache_clear()


def test_an_encrypted_round_trip_through_s3(db, clinic, aws, tmp_path) -> None:
    uri = "s3://clinic-media/migration/zenflow.db.enc"
    assert move_data.move("app", uri)["ok"]
    stored = aws.get_object(Bucket="clinic-media", Key="migration/zenflow.db.enc")["Body"].read()
    assert b"SQLite format" not in stored, "only the encrypted copy leaves the host"
    back = move_data.move(uri, str(tmp_path / "restored.db"))
    assert back["ok"] and back["rows"] == move_data.move("app", str(tmp_path / "again.db"))["rows"]


# ── Postgres: the real cut-over (SQLite → Postgres) and rollback (Postgres → SQLite) ──
@pytest.mark.skipif(not dbmod.is_postgres(), reason="needs the suite on Postgres (ZF_TEST_DB_URL)")
def test_postgres_to_sqlite_and_back_to_a_fresh_postgres(db, clinic, tmp_path) -> None:
    from zenflow.pg import Connection
    from zenflow.restore_drill import _scratch_url

    url = dbmod.db_url()
    sqlite_copy = str(tmp_path / "rollback.db")
    assert move_data.move("app", sqlite_copy)["ok"], "rollback direction: Postgres → SQLite"

    scratch = "zenflow_move_test"
    admin = Connection(url)
    try:
        admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{scratch}"')
        target = _scratch_url(url, scratch)
        report = move_data.move(sqlite_copy, target)
        assert report["ok"], report["differences"]
        moved = Connection(target)
        try:  # the sequences continue after the largest id: a new row does not collide
            top = moved.execute("SELECT MAX(id) FROM appointments").fetchone()[0]
            cur = moved.execute(
                "INSERT INTO appointments (patient_id, patient_name, therapist_id, date, time)"
                " VALUES (1, 'x', 't1', '2027-01-01', '09:00')"
            )
            new_id = moved.execute("SELECT MAX(id) FROM appointments").fetchone()[0]
            assert new_id == top + 1 and cur.rowcount == 1
        finally:
            moved.close()
    finally:
        admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        admin.close()


def test_the_cli_reports_and_exits(db, clinic, tmp_path, capsys) -> None:
    import json

    assert move_data.main(["--from", "app", "--to", str(tmp_path / "cli.db")]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and Path(tmp_path / "cli.db").is_file()
