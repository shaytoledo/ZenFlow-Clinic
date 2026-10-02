"""Phase 9.9 — the patient data-deletion procedure (`zenflow.patient_erasure`).

`anonymize` (an erasure request inside the clinical retention period) removes everything that
identifies or reaches the patient and keeps the de-identified clinical record; `purge` (retention
over, or a documented override) removes every row. Both leave other patients untouched, put the
audit trail's append-only guard back, and record the erasure in the trail without the identity.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import bot.db as dbmod
from zenflow import clock

pytestmark = pytest.mark.integration

NAME = "Dana Erasable"
PHONE = "+972-50-1234567"
EMAIL = "dana@example.com"


def _count(table: str, where: str, *params: Any) -> int:
    sql = f"SELECT COUNT(*) FROM {table} WHERE {where}"  # noqa: S608 - test constants
    return dbmod.get_db().execute(sql, params).fetchone()[0]


@pytest.fixture
def seeded(make_patient, make_appointment, make_treatment_notes, fake_redis):
    """A patient with a row in every class that holds patient data, plus a bystander patient."""
    from web.services import audit

    conn = dbmod.get_db()
    patient = make_patient(NAME, telegram_id=7_700_001)
    pid, tg = patient["patient_id"], patient["telegram_id"]
    conn.execute(
        "UPDATE patients SET phone=?, email=?, notes='allergic to latex' WHERE id=?",
        (PHONE, EMAIL, pid),
    )
    apt = make_appointment(patient=patient, summary="Migraine, 3 days", intake_history=[{"q": "a"}])
    conn.execute(
        "UPDATE appointments SET patient_phone=?, patient_email=?, gcal_apt_event_id='evt-1' "
        "WHERE id=?",
        (PHONE, EMAIL, apt["id"]),
    )
    make_treatment_notes(apt, session_notes="needled LR3")
    now = clock.iso_now()
    conn.execute(
        "INSERT INTO message_log (ts, channel, patient_id, therapist_id, appointment_id, kind, "
        "status, provider_message_id) VALUES (?, 'telegram', ?, ?, ?, 'followup', 'sent', '991')",
        (now, pid, apt["therapist_id"], apt["id"]),
    )
    conn.execute(
        "INSERT INTO notifications (therapist_id, kind, title, body, appointment_id, patient_id, "
        "patient_name) VALUES (?, 'send_failed', ?, 'x', ?, ?, ?)",
        (apt["therapist_id"], f"Could not reach {NAME}", apt["id"], pid, NAME),
    )
    conn.execute(
        "INSERT INTO followups (appointment_id, patient_id, therapist_id, status, pain_level, "
        "free_text, created_at, updated_at) VALUES (?, ?, ?, 'completed', 3, 'better', ?, ?)",
        (apt["id"], pid, apt["therapist_id"], now, now),
    )
    conn.execute(
        "INSERT INTO ai_calls (ts, appointment_id, stage, status, prompt_sha256, prompt_debug) "
        "VALUES (?, ?, 'diagnosis', 'ok', 'abc', ?)",
        (now, apt["id"], f"patient {NAME} says migraine"),
    )
    conn.execute(
        "INSERT INTO jobs (name, payload_json, run_at, status, created_at, updated_at) "
        "VALUES ('followup.send_step1', ?, ?, 'pending', ?, ?)",
        (json.dumps({"appointment_id": apt["id"]}), now, now, now),
    )
    for kind, name, key in (
        ("user_data", "", str(tg)),
        ("conversation", "patient", json.dumps([tg, tg])),
    ):
        conn.execute(
            "INSERT INTO bot_persistence (kind, name, key, value_json, updated_at) "
            "VALUES (?, ?, ?, '{}', ?)",
            (kind, name, key, now),
        )
    fake_redis.sync.set(f"message_store:zenflow:intake:{tg}", "[]")
    fake_redis.sync.set(f"zenflow:relay:active:{tg}", "{}")
    fake_redis.sync.set(f"zenflow:relay:history:{apt['therapist_id']}:{tg}", "[]")
    audit.record(
        "appointment.booked",
        "appointment",
        apt["id"],
        after={"patient_name": NAME, "patient_phone": PHONE, "summary": "Migraine, 3 days"},
    )

    other = make_patient("Bystander", telegram_id=7_700_002)
    other_apt = make_appointment(patient=other)
    make_treatment_notes(other_apt)
    conn.execute(
        "INSERT INTO bot_persistence (kind, name, key, value_json, updated_at) "
        "VALUES ('user_data', '', ?, '{}', ?)",
        (str(other["telegram_id"]), now),
    )
    fake_redis.sync.set(f"zenflow:relay:active:{other['telegram_id']}", "{}")
    return {"pid": pid, "tg": tg, "apt": apt, "other": other, "other_apt": other_apt}


def _audit_guard_holds() -> bool:
    conn = dbmod.get_db()
    try:
        conn.execute("UPDATE audit_log SET action='tampered'")
    except dbmod.DatabaseError:
        pass
    else:
        return False
    try:
        conn.execute("DELETE FROM audit_log")
    except dbmod.DatabaseError:
        return True
    return False


def _everything_identifying(pid: int, apt_id: int) -> str:
    """Every value left in the database that could carry the patient's identity, as one string."""
    conn = dbmod.get_db()
    rows: list[Any] = []
    rows += conn.execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchall()
    rows += conn.execute("SELECT * FROM patient_channels WHERE patient_id=?", (pid,)).fetchall()
    rows += conn.execute("SELECT * FROM appointments WHERE patient_id=?", (pid,)).fetchall()
    rows += conn.execute("SELECT * FROM notifications WHERE patient_id=?", (pid,)).fetchall()
    rows += conn.execute("SELECT * FROM ai_calls WHERE appointment_id=?", (apt_id,)).fetchall()
    rows += conn.execute("SELECT before_json, after_json FROM audit_log").fetchall()
    return json.dumps([list(r) for r in rows], ensure_ascii=False, default=str)


# ── the plan ──
def test_without_apply_nothing_changes(seeded) -> None:
    from zenflow.patient_erasure import erase_patient

    before = _everything_identifying(seeded["pid"], seeded["apt"]["id"])
    report = erase_patient(seeded["pid"], reason="DSAR 2026-10-01")
    assert report.applied is False
    assert report.counts["patient_channels"] == 1 and report.counts["queued_jobs"] == 1
    assert report.counts["redis_keys"] == 3 and report.counts["bot_state_rows"] == 2
    assert any("evt-1" in step for step in report.manual_steps), "Google events are listed"
    assert _everything_identifying(seeded["pid"], seeded["apt"]["id"]) == before


def test_a_reason_is_required(seeded) -> None:
    from zenflow.patient_erasure import ErasureRefused, erase_patient

    with pytest.raises(ErasureRefused):
        erase_patient(seeded["pid"], reason="  ", apply=True)


def test_an_unknown_patient_is_refused() -> None:
    from zenflow.patient_erasure import ErasureRefused, erase_patient

    with pytest.raises(ErasureRefused):
        erase_patient(9_999_999, reason="x", apply=True)


# ── anonymize ──
def test_anonymize_removes_every_identifier_and_keeps_the_clinical_record(seeded, fake_redis):
    from zenflow.patient_erasure import ERASED_NAME, erase_patient

    pid, apt_id, tg = seeded["pid"], seeded["apt"]["id"], seeded["tg"]
    erase_patient(pid, reason="DSAR 2026-10-01", apply=True)

    leftover = _everything_identifying(pid, apt_id)
    for secret in (NAME, PHONE, EMAIL, "allergic to latex", str(tg)):
        assert secret not in leftover, f"{secret!r} survived the erasure"
    patient = dbmod.get_db().execute("SELECT * FROM patients WHERE id=?", (pid,)).fetchone()
    assert patient["full_name"] == ERASED_NAME and patient["legacy_id"] is None
    assert _count("patient_channels", "patient_id=?", pid) == 0, "no channel can reach them"
    assert _count("message_log", "patient_id=?", pid) == 0
    assert _count("notifications", "patient_id=?", pid) == 0
    queued = _count("jobs", "payload_json=?", json.dumps({"appointment_id": apt_id}))
    assert queued == 0, "no follow-up is sent to an erased patient"
    assert _count("bot_persistence", "key LIKE ?", f"%{tg}%") == 0
    assert fake_redis.sync.keys(f"*{tg}*") == []

    # the clinical record stays, de-identified
    assert _count("appointments", "patient_id=?", pid) == 1
    assert _count("treatment_notes", "patient_id=?", pid) == 1
    assert _count("intake_sessions", "patient_id=?", pid) == 1
    assert _count("followups", "patient_id=?", pid) == 1
    assert _count("ai_calls", "appointment_id=?", apt_id) == 1
    row = (
        dbmod.get_db()
        .execute("SELECT after_json FROM audit_log WHERE action='appointment.booked'")
        .fetchone()
    )
    assert json.loads(row[0])["summary"] == "Migraine, 3 days", "clinical audit values are kept"


def test_anonymize_leaves_other_patients_alone(seeded, fake_redis) -> None:
    from zenflow.patient_erasure import erase_patient

    other = seeded["other"]
    erase_patient(seeded["pid"], reason="DSAR", apply=True)
    row = (
        dbmod.get_db()
        .execute("SELECT full_name FROM patients WHERE id=?", (other["patient_id"],))
        .fetchone()
    )
    assert row["full_name"] == "Bystander"
    assert _count("patient_channels", "patient_id=?", other["patient_id"]) == 1
    assert _count("bot_persistence", "key=?", str(other["telegram_id"])) == 1
    assert fake_redis.sync.exists(f"zenflow:relay:active:{other['telegram_id']}")


def test_the_audit_guard_is_back_and_the_erasure_is_recorded_without_identity(seeded) -> None:
    from zenflow.patient_erasure import erase_patient

    erase_patient(seeded["pid"], reason="DSAR 2026-10-01", apply=True)
    assert _audit_guard_holds(), "audit_log must be append-only again after the erasure"
    row = (
        dbmod.get_db()
        .execute(
            "SELECT actor_type, actor_id, entity_id, after_json FROM audit_log "
            "WHERE action='patient.erased'"
        )
        .fetchone()
    )
    assert row is not None and row["entity_id"] == str(seeded["pid"])
    assert (row["actor_type"], row["actor_id"]) == ("system", "patient_erasure")
    after = json.loads(row["after_json"])
    assert after["mode"] == "anonymize" and after["reason"] == "DSAR 2026-10-01"
    assert NAME not in row["after_json"] and PHONE not in row["after_json"]


def test_a_failure_rolls_everything_back_guard_included(seeded, monkeypatch) -> None:
    from zenflow import patient_erasure as pe

    def _boom(value: str | None) -> str | None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(pe, "_scrub", _boom)
    before = _everything_identifying(seeded["pid"], seeded["apt"]["id"])
    with pytest.raises(RuntimeError):
        pe.erase_patient(seeded["pid"], reason="DSAR", apply=True)
    assert _everything_identifying(seeded["pid"], seeded["apt"]["id"]) == before
    assert _count("patient_channels", "patient_id=?", seeded["pid"]) == 1
    assert _audit_guard_holds()


def test_a_new_message_after_erasure_starts_an_unrelated_patient(seeded) -> None:
    from web.repositories import patient_repo
    from zenflow.patient_erasure import erase_patient

    erase_patient(seeded["pid"], reason="DSAR", apply=True)
    again = patient_repo.for_channel("telegram", seeded["tg"], "Dana")
    assert again != seeded["pid"]


def test_the_export_after_anonymize_holds_no_identity(seeded) -> None:
    from zenflow.patient_erasure import erase_patient
    from zenflow.patient_export import export_patient

    erase_patient(seeded["pid"], reason="DSAR", apply=True)
    text = json.dumps(export_patient(seeded["pid"]), ensure_ascii=False, default=str)
    for secret in (NAME, PHONE, EMAIL, str(seeded["tg"])):
        assert secret not in text


# ── purge ──
def test_purge_is_refused_while_retention_is_keep_forever(seeded) -> None:
    from zenflow.patient_erasure import ErasureRefused, erase_patient

    with pytest.raises(ErasureRefused, match="retention"):
        erase_patient(seeded["pid"], mode="purge", reason="asked", apply=True)
    assert _count("treatment_notes", "patient_id=?", seeded["pid"]) == 1


def test_purge_with_override_removes_every_row_and_its_trail(seeded, fake_redis) -> None:
    from zenflow.patient_erasure import erase_patient

    pid, apt_id = seeded["pid"], seeded["apt"]["id"]
    erase_patient(
        pid, mode="purge", reason="legal: counsel ok", apply=True, override_retention=True
    )

    for table in (
        "patients",
        "patient_channels",
        "appointments",
        "treatment_notes",
        "intake_sessions",
        "followups",
        "message_log",
        "notifications",
    ):
        column = "id" if table == "patients" else "patient_id"
        assert _count(table, f"{column}=?", pid) == 0, table
    assert _count("ai_calls", "appointment_id=?", apt_id) == 0
    assert _count("audit_log", "entity_type='appointment' AND entity_id=?", str(apt_id)) == 0
    assert _count("audit_log", "action='patient.purged' AND entity_id=?", str(pid)) == 1
    assert _audit_guard_holds()
    # the bystander keeps everything
    other = seeded["other"]
    assert _count("treatment_notes", "patient_id=?", other["patient_id"]) == 1
    assert _count("appointments", "patient_id=?", other["patient_id"]) == 1


def test_purge_is_allowed_once_the_clinical_period_is_over(
    seeded, monkeypatch: pytest.MonkeyPatch
) -> None:
    import zenflow.settings as settings_mod
    from zenflow.patient_erasure import erase_patient

    monkeypatch.setenv("ZF_RETENTION_CLINICAL_YEARS", "1")
    settings_mod.reset_settings()
    try:
        dbmod.get_db().execute(
            "UPDATE appointments SET date='2001-01-01' WHERE patient_id=?", (seeded["pid"],)
        )
        erase_patient(seeded["pid"], mode="purge", reason="retention", apply=True)
        assert _count("patients", "id=?", seeded["pid"]) == 0
    finally:
        settings_mod.reset_settings()


def test_the_cli_plans_by_default_and_refuses_cleanly(seeded, capsys) -> None:
    from zenflow.patient_erasure import main

    assert main([str(seeded["pid"]), "--reason", "DSAR"]) == 0
    assert '"applied": false' in capsys.readouterr().out
    assert _count("patient_channels", "patient_id=?", seeded["pid"]) == 1
    assert main([str(seeded["pid"]), "--mode", "purge", "--reason", "x", "--apply"]) == 2
    assert "refused" in capsys.readouterr().err


def test_erasure_handles_every_class_the_export_knows(seeded) -> None:
    """A new patient-data table added to the export must be handled by the erasure too."""
    from zenflow.patient_erasure import erase_patient
    from zenflow.patient_export import export_patient

    exported = {k for k, v in export_patient(seeded["pid"]).items() if isinstance(v, list)}
    as_table = {"channels": "patient_channels", "messages": "message_log"}
    handled = set(erase_patient(seeded["pid"], reason="DSAR").counts)
    for section in exported:
        assert as_table.get(section, section) in handled, f"erasure ignores {section}"
