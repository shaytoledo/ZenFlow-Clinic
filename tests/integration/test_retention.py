"""Phase 9.9 — the retention policy per data class and its sweep (`zenflow.retention`).

Operational rows about patients (message metadata, AI-call meters, read or resolved notifications,
finished jobs) are removed after `ZF_RETENTION_OPERATIONAL_DAYS`; open alerts and pending jobs stay.
The clinical record is never removed while `ZF_RETENTION_CLINICAL_YEARS` is 0 (keep forever, the
default until owner decision Q5); once set, a patient whose last appointment is older is purged.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import bot.db as dbmod
from zenflow import clock

pytestmark = pytest.mark.integration

OLD = "2020-01-01T00:00:00Z"


@pytest.fixture
def retention(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import zenflow.settings as settings_mod

    settings_mod.reset_settings()
    yield
    settings_mod.reset_settings()


def _set(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    import zenflow.settings as settings_mod

    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings_mod.reset_settings()


def _seed_operational() -> None:
    conn = dbmod.get_db()
    now = clock.iso_now()
    for ts in (OLD, now):
        conn.execute(
            "INSERT INTO message_log (ts, channel, patient_id, kind, status) "
            "VALUES (?, 'telegram', 1, 'followup', 'sent')",
            (ts,),
        )
        conn.execute(
            "INSERT INTO ai_calls (ts, stage, status) VALUES (?, 'diagnosis', 'ok')", (ts,)
        )
        conn.execute(
            "INSERT INTO jobs (name, run_at, status, created_at, updated_at) "
            "VALUES ('x', ?, 'done', ?, ?)",
            (ts, ts, ts),
        )
    conn.execute(
        "INSERT INTO jobs (name, run_at, status, created_at, updated_at) "
        "VALUES ('x', ?, 'pending', ?, ?)",
        (OLD, OLD, OLD),
    )
    for read_at, resolved_at, persistent in (
        (OLD, None, 0),  # read, ordinary  → removed
        (None, OLD, 1),  # resolved        → removed
        (None, None, 1),  # open alert     → kept
        (OLD, None, 1),  # read but persistent and unresolved → kept
    ):
        conn.execute(
            "INSERT INTO notifications (therapist_id, kind, title, persistent, read_at, "
            "resolved_at, created_at) VALUES ('t1', 'k', 'x', ?, ?, ?, ?)",
            (persistent, read_at, resolved_at, OLD),
        )


def _count(table: str) -> int:
    return dbmod.get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def test_the_policy_covers_every_patient_linked_table() -> None:
    from zenflow.patient_export import export_patient
    from zenflow.retention import POLICY

    covered = {t.strip() for c in POLICY for t in c.tables.split(",")}
    exported = {k for k, v in export_patient(0).items() if isinstance(v, list)}
    by_table = {
        "channels": "patient_channels",
        "messages": "message_log",
    }
    for section in exported:
        assert by_table.get(section, section) in covered, f"no retention rule for {section}"
    assert {"audit_log", "patients", "jobs"} <= covered


def test_preview_changes_nothing(retention) -> None:
    from zenflow.retention import sweep

    _seed_operational()
    before = {t: _count(t) for t in ("message_log", "ai_calls", "jobs", "notifications")}
    result = sweep()
    assert result["applied"] is False
    assert result["operational"] == {
        "message_log": 1,
        "ai_calls": 1,
        "notifications": 2,
        "jobs": 1,
    }
    assert {t: _count(t) for t in before} == before


def test_apply_removes_old_operational_rows_and_keeps_the_rest(retention) -> None:
    from zenflow.retention import sweep

    _seed_operational()
    sweep(apply=True)
    assert _count("message_log") == 1 and _count("ai_calls") == 1
    statuses = sorted(r[0] for r in dbmod.get_db().execute("SELECT status FROM jobs"))
    assert statuses == ["done", "pending"], "recent done + old pending stay"
    assert _count("notifications") == 2, "open and persistent-unresolved alerts stay"


def test_zero_days_keeps_operational_rows_forever(retention, monkeypatch) -> None:
    from zenflow.retention import sweep

    _set(monkeypatch, ZF_RETENTION_OPERATIONAL_DAYS="0")
    _seed_operational()
    assert sweep(apply=True)["operational"] == {}
    assert _count("message_log") == 2


def test_clinical_records_are_never_purged_by_default(
    retention, make_appointment, make_treatment_notes
) -> None:
    from zenflow.retention import sweep

    apt = make_appointment(apt_date="2001-01-01")
    make_treatment_notes(apt)
    assert sweep(apply=True)["clinical_purged"] == []
    assert _count("treatment_notes") == 1


def test_a_record_past_its_clinical_period_is_purged(
    retention, monkeypatch, make_appointment, make_treatment_notes
) -> None:
    from zenflow.retention import sweep

    _set(monkeypatch, ZF_RETENTION_CLINICAL_YEARS="10")
    old = make_appointment(apt_date="2001-01-01")
    make_treatment_notes(old)
    recent = make_appointment(apt_date=clock.today().isoformat())
    make_treatment_notes(recent)

    preview = sweep()
    assert preview["clinical_purged"] == [old["patient_id"]]
    assert _count("treatment_notes") == 2, "a preview purges nothing"

    sweep(apply=True)
    conn = dbmod.get_db()
    assert (
        conn.execute("SELECT COUNT(*) FROM patients WHERE id=?", (old["patient_id"],)).fetchone()[0]
        == 0
    )
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM treatment_notes WHERE patient_id=?", (recent["patient_id"],)
        ).fetchone()[0]
        == 1
    )


def test_a_patient_with_a_recent_visit_is_kept_even_with_old_ones(
    retention, monkeypatch, make_patient, make_appointment
) -> None:
    from zenflow.retention import expired_patients

    _set(monkeypatch, ZF_RETENTION_CLINICAL_YEARS="10")
    patient = make_patient("Regular")
    make_appointment(patient=patient, apt_date="2001-01-01")
    make_appointment(patient=patient, apt_date=clock.today().isoformat(), apt_time="11:00")
    assert patient["patient_id"] not in expired_patients()


def test_the_cli_prints_the_policy(capsys) -> None:
    from zenflow.retention import main

    assert main(["--policy"]) == 0
    out = capsys.readouterr().out
    assert "Clinical record" in out and "ZF_RETENTION_CLINICAL_YEARS" in out
