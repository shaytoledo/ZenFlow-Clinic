"""Phase 1.1 — one-time timestamp normalisation with dry-run, backup and idempotence."""

from __future__ import annotations

from pathlib import Path

import bot.db as dbmod
from zenflow import migrate_timestamps as mig


def _seed_legacy(make_completed_session) -> int:
    apt = make_completed_session()
    dbmod.get_db().execute(
        """UPDATE treatment_notes SET
             completed_at=?, followup_sent_at=?, pending_rec_send_at=?, created_at=?, updated_at=?
           WHERE appointment_id=?""",
        (
            "2026-03-01T14:00:00.123456",  # naive Python local (Asia/Jerusalem = UTC+2 in March)
            "2026-03-01 13:00:00",  # SQLite datetime('now') — UTC
            "2026-03-02T15:00:00+03:00",  # aware
            "2026-03-01 12:00:00",
            "2026-03-01T12:00:00Z",  # already canonical
            apt["id"],
        ),
    )
    dbmod.get_db().execute(
        "UPDATE appointments SET created_at=? WHERE id=?", ("2026-02-28 09:30:00", apt["id"])
    )
    return int(apt["id"])


def _row(apt_id: int) -> dict:
    return dict(
        dbmod.get_db()
        .execute("SELECT * FROM treatment_notes WHERE appointment_id=?", (apt_id,))
        .fetchone()
    )


def test_dry_run_reports_and_changes_nothing(db: Path, make_completed_session) -> None:
    apt_id = _seed_legacy(make_completed_session)
    before = _row(apt_id)
    report = mig.migrate(local_tz="Asia/Jerusalem", dry_run=True)
    assert report.converted >= 5
    assert report.by_shape == {"naive-local": 1, "sqlite-utc": 3, "aware": 1}
    assert report.unparseable == []
    assert report.backup_path is None
    assert _row(apt_id) == before


def test_apply_normalises_every_shape_and_is_idempotent(db: Path, make_completed_session) -> None:
    apt_id = _seed_legacy(make_completed_session)
    report = mig.migrate(local_tz="Asia/Jerusalem", dry_run=False)
    assert report.backup_path and Path(report.backup_path).exists()
    row = _row(apt_id)
    assert row["completed_at"] == "2026-03-01T12:00:00Z"  # 14:00 Jerusalem → 12:00Z
    assert row["followup_sent_at"] == "2026-03-01T13:00:00Z"
    assert row["pending_rec_send_at"] == "2026-03-02T12:00:00Z"
    assert row["created_at"] == "2026-03-01T12:00:00Z"
    assert row["updated_at"] == "2026-03-01T12:00:00Z"
    apt_created = (
        dbmod.get_db()
        .execute("SELECT created_at FROM appointments WHERE id=?", (apt_id,))
        .fetchone()[0]
    )
    assert apt_created == "2026-02-28T09:30:00Z"

    again = mig.migrate(local_tz="Asia/Jerusalem", dry_run=False)
    assert again.converted == 0
    assert again.backup_path is None  # nothing to write → no backup taken


def test_unparseable_values_are_reported_and_left_alone(db: Path, make_completed_session) -> None:
    apt_id = _seed_legacy(make_completed_session)
    dbmod.get_db().execute(
        "UPDATE treatment_notes SET recommendations_sent_at='next tuesday' WHERE appointment_id=?",
        (apt_id,),
    )
    report = mig.migrate(local_tz="Asia/Jerusalem", dry_run=False)
    assert [(t, c) for t, c, _, _ in report.unparseable] == [
        ("treatment_notes", "recommendations_sent_at")
    ]
    assert _row(apt_id)["recommendations_sent_at"] == "next tuesday"
    assert mig.main(["--dry-run", "--local-tz", "Asia/Jerusalem"]) == 1  # exit 1 on unparseable


def test_cli_dry_run_exit_code_is_zero_when_clean(db: Path, make_completed_session) -> None:
    make_completed_session()
    assert mig.main(["--dry-run"]) == 0
