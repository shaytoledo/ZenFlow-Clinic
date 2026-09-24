"""Phase 9.9 — a patient data-export procedure (GDPR right of access).

`zenflow.patient_export.export_patient(patient_id)` gathers everything the clinic holds about one
patient — the patient row, their channel identities, appointments, intake, treatment notes, message
log, notifications, follow-ups and the AI calls made for their appointments — so the clinic can
answer a data-subject access request. It is read-only and never mixes in another patient's data.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_export_gathers_a_patients_records(make_patient, make_appointment, make_treatment_notes):
    from zenflow.patient_export import export_patient

    patient = make_patient(telegram_id=555_000_1)
    apt = make_appointment(patient=patient)
    make_treatment_notes(apt)

    data = export_patient(patient["patient_id"])

    assert data["patient"] and data["patient"]["id"] == patient["patient_id"]
    assert any(a["id"] == apt["id"] for a in data["appointments"])
    assert data["treatment_notes"], "the patient's clinical notes are included"
    assert "exported_at" in data and "patient_id" in data


def test_export_never_includes_another_patient(make_patient, make_appointment):
    from zenflow.patient_export import export_patient

    mine = make_patient(telegram_id=555_000_2)
    other = make_patient(telegram_id=555_000_3)
    my_apt = make_appointment(patient=mine)
    other_apt = make_appointment(patient=other)

    data = export_patient(mine["patient_id"])
    apt_ids = {a["id"] for a in data["appointments"]}
    assert my_apt["id"] in apt_ids
    assert other_apt["id"] not in apt_ids, "another patient's appointment must never leak"
    for row in data["appointments"]:
        assert row["patient_id"] == mine["patient_id"]


def test_export_of_an_unknown_patient_is_empty_not_an_error():
    from zenflow.patient_export import export_patient

    data = export_patient(9_999_999)
    assert data["patient"] is None
    assert data["appointments"] == [] and data["treatment_notes"] == []


def test_the_export_has_every_patient_linked_class(make_patient):
    """Every table that holds patient data appears as a section, so nothing is silently omitted."""
    from zenflow.patient_export import export_patient

    patient = make_patient(telegram_id=555_000_4)
    data = export_patient(patient["patient_id"])
    for section in (
        "patient",
        "channels",
        "appointments",
        "intake_sessions",
        "treatment_notes",
        "messages",
        "notifications",
        "followups",
        "ai_calls",
    ):
        assert section in data, f"the export is missing the {section!r} section"


def test_cli_writes_a_json_file(make_patient, tmp_path):
    import json

    from zenflow.patient_export import main

    patient = make_patient(telegram_id=555_000_9)
    out = tmp_path / "export.json"
    assert main([str(patient["patient_id"]), "--out", str(out)]) == 0
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["patient"]["id"] == patient["patient_id"]


def test_cli_reports_an_unknown_patient(capsys):
    from zenflow.patient_export import main

    assert main(["8_888_888".replace("_", "")]) == 1
