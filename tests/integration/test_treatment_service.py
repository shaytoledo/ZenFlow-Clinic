"""Phase 11.1/11.2 — the treatment-notes service (session listing + completion).

`treatment_service` drives the /sessions history and the "Complete session" action. The listing
queries (completed-only, and the LEFT-JOIN "everything" list that keeps note-less manual appointments
visible), their sort orders and tenant scoping, and the completion path were largely uncovered (~46%).
These tests exercise them against the real database.
"""

from __future__ import annotations

import pytest

from web.services import treatment_service as svc

pytestmark = pytest.mark.integration


def test_complete_session_stamps_completed_at_and_audits(make_appointment, make_treatment_notes):
    apt = make_appointment()
    make_treatment_notes(apt)  # notes exist but the session is not yet completed
    assert not (svc.get_notes(apt["id"]) or {}).get("completed_at")

    svc.complete_session(apt["id"], apt["patient_id"])

    notes = svc.get_notes(apt["id"])
    assert notes and notes["completed_at"], "completion stamps a timestamp"
    from web.services import audit

    actions = [r["action"] for r in audit.for_entity("treatment_notes", apt["id"])]
    assert "session.completed" in actions, "completion is recorded in the audit trail"


def test_save_notes_records_nothing_when_nothing_changed(make_appointment, make_treatment_notes):
    apt = make_appointment()
    make_treatment_notes(apt)
    from web.services import audit

    before = len(audit.for_entity("treatment_notes", apt["id"]))
    svc.save_notes(apt["id"], apt["patient_id"], {})  # empty change set
    after = len(audit.for_entity("treatment_notes", apt["id"]))
    assert after == before, "a no-op save writes no audit row"


def test_list_completed_returns_only_completed_newest_first(make_appointment, make_treatment_notes):
    apt_old = make_appointment(apt_date="2026-03-01")
    make_treatment_notes(apt_old, completed_at="2026-03-01T10:00:00Z")
    apt_new = make_appointment(apt_date="2026-03-05")
    make_treatment_notes(apt_new, completed_at="2026-03-05T10:00:00Z")
    make_appointment(apt_date="2026-03-06")  # active, no notes — must not appear

    done = svc.list_completed_sessions()
    ids = [r["appointment_id"] for r in done]
    assert apt_old["id"] in ids and apt_new["id"] in ids
    # newest completed first
    assert ids.index(apt_new["id"]) < ids.index(apt_old["id"])
    assert all(r["completed_at"] for r in done), "only completed sessions are listed"


def test_list_completed_is_tenant_scoped(make_therapist, make_appointment, make_treatment_notes):
    a = make_therapist(therapist_id="t_a")
    b = make_therapist(therapist_id="t_b")
    apt_a = make_appointment(therapist=a)
    make_treatment_notes(apt_a, completed_at="2026-03-01T10:00:00Z")
    apt_b = make_appointment(therapist=b)
    make_treatment_notes(apt_b, completed_at="2026-03-02T10:00:00Z")

    ids = [r["appointment_id"] for r in svc.list_completed_sessions("t_a")]
    assert ids == [apt_a["id"]], "b's completed session does not appear in a's list"


def test_list_all_sessions_includes_noteless_appointments(make_appointment, make_treatment_notes):
    with_notes = make_appointment()
    make_treatment_notes(with_notes)
    without_notes = make_appointment(apt_date="2026-03-09")  # never opened in the treatment screen

    ids = [r["appointment_id"] for r in svc.list_all_sessions()]
    assert with_notes["id"] in ids and without_notes["id"] in ids, "LEFT JOIN keeps note-less rows"


@pytest.mark.parametrize(
    "sort_by,expected_first_name",
    [("name", "Alice"), ("date", "Zoe"), ("bogus", "Zoe")],  # bogus → default (date DESC)
)
def test_list_all_sessions_sort_orders(
    make_patient, make_appointment, sort_by, expected_first_name
):
    make_appointment(patient=make_patient("Alice"), apt_date="2026-03-01")
    make_appointment(patient=make_patient("Zoe"), apt_date="2026-03-05")  # later date

    rows = svc.list_all_sessions(sort_by=sort_by)
    assert rows[0]["patient_name"] == expected_first_name


def test_list_all_sessions_last_access_sort_returns_all_rows(make_appointment):
    # last_access orders by COALESCE(updated_at, created_at); assert the branch runs and returns all
    # rows (exact order depends on sub-second timestamps, so it is not asserted here).
    make_appointment(apt_date="2026-03-01")
    make_appointment(apt_date="2026-03-05")
    rows = svc.list_all_sessions(sort_by="last_access")
    assert len(rows) == 2


def test_list_all_sessions_is_tenant_scoped(make_therapist, make_appointment):
    a = make_therapist(therapist_id="t_a")
    b = make_therapist(therapist_id="t_b")
    apt_a = make_appointment(therapist=a)
    make_appointment(therapist=b)
    ids = [r["appointment_id"] for r in svc.list_all_sessions("t_a")]
    assert ids == [apt_a["id"]], "only the requesting therapist's appointments are listed"
