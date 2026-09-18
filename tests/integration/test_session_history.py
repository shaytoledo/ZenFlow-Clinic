"""Plan 8.5 — the audit trail, where a therapist can actually see it.

8.1 made every clinical change answerable in SQL. This puts the answer on the session's own page:
who changed this record, when, and what kind of change it was — the therapist, the patient, the AI
pipeline or a connected system — without repeating the clinical text that is already on the page.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

pytestmark = pytest.mark.integration

DAY = "2026-03-12"
TIME = "10:00"
SLUG_TIME = "10-00"


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    from web.services import booking_service

    async def _hours(day: date, therapist_id: str | None = None) -> list[str]:
        return ["09:00", "10:00", "11:00"]

    async def _book(*a: Any, **k: Any) -> str:
        return "gcal-1"

    async def _restore(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(booking_service, "get_available_hours", _hours)
    monkeypatch.setattr(booking_service, "book_slot", _book)
    monkeypatch.setattr(booking_service, "restore_slot", _restore)


def _view(rows: list[dict[str, Any]], ai: list[dict[str, Any]], lang: str, tid: str = "t1") -> dict:
    """The card's content — a test that gets None here has nothing to assert about."""
    from web.services.history_view import history_view

    view = history_view(rows, ai, lang, therapist_id=tid)
    assert view is not None
    return view


# ── gathering ──
def test_a_sessions_trail_is_everything_that_happened_to_it(db) -> None:
    """One appointment's story lives under three entity types, all keyed by its id."""
    from web.services import audit

    audit.record("appointment.created", "appointment", 7, after={"local_time": TIME})
    audit.record("treatment_notes.updated", "treatment_notes", 7, after={"session_notes": "x"})
    audit.record("followup.answered", "followup", 7, after={"pain_level": 3})
    audit.record("appointment.created", "appointment", 8, after={"local_time": "11:00"})
    audit.record("therapist.updated", "therapist", "t1", after={"name": "Dr Lee"})

    trail = audit.for_appointment(7)
    assert [row["action"] for row in trail] == [
        "appointment.created",
        "treatment_notes.updated",
        "followup.answered",
    ], "oldest first, and only this session's"


def test_a_session_nothing_happened_to_has_no_trail(db) -> None:
    from web.services import audit

    assert audit.for_appointment(7) == []


# ── the view ──
def test_each_line_says_who_in_words_not_ids(db) -> None:
    from web.services import audit

    with audit.acting_as("therapist", "t1"):
        audit.record("appointment.created", "appointment", 7, after={"local_time": TIME})
    with audit.acting_as("patient", "42"):
        audit.record("followup.answered", "followup", 7, after={"pain_level": 3})
    with audit.acting_as("ai", "gemma3:latest"):
        audit.record("treatment_notes.updated", "treatment_notes", 7, after={"tcm_pattern": "x"})
    with audit.acting_as("api", "whatsapp-bridge"):
        audit.record("appointment.cancelled", "appointment", 7, before={"status": "scheduled"})

    view = _view(audit.for_appointment(7), [], "en")
    who = [item["who"] for item in view["items"]]
    assert who == ["You", "The patient", "ZenFlow AI", "whatsapp-bridge"]
    what = [item["what"] for item in view["items"]]
    assert what[0] == "Appointment booked" and what[3] == "Appointment cancelled"
    assert all(item["time"] for item in view["items"])


def test_another_therapist_is_named_not_mistaken_for_you(db) -> None:
    from web.services import audit

    with audit.acting_as("therapist", "t2"):
        audit.record("treatment_notes.updated", "treatment_notes", 7, after={"tongue": "pale"})

    view = _view(audit.for_appointment(7), [], "en")
    assert view["items"][0]["who"] == "Another therapist"


def test_the_trail_shows_what_changed_never_what_it_says(db) -> None:
    """The notes are on the page already; the trail says which fields moved, not their contents."""
    from web.services import audit

    secret = "patient reported a miscarriage in 2019"
    audit.record(
        "treatment_notes.updated",
        "treatment_notes",
        7,
        after={"session_notes": secret, "tongue": "pale", "pulse": "wiry", "points": "LI4"},
    )

    view = _view(audit.for_appointment(7), [], "en")
    rendered = str(view)
    assert secret not in rendered and "pale" not in rendered
    assert "session_notes" in view["items"][0]["fields"]


def test_an_unknown_action_still_reads_as_something(db) -> None:
    from web.services import audit

    audit.record("appointment.rescheduled", "appointment", 7, after={"local_time": "11:00"})
    view = _view(audit.for_appointment(7), [], "en")
    assert view["items"][0]["what"] == "Appointment rescheduled", "a verb, not a raw key"


def test_the_card_summarises_the_ai_work_of_that_session(db) -> None:
    from web.services import ai_calls, audit

    with ai_calls.for_appointment(7):
        for stage in ("pipeline.summary", "pipeline.diagnosis", "pipeline.points"):
            ai_calls.record(stage, provider="ollama", model="gemma3", duration_ms=1200)
        ai_calls.record("pipeline.points", duration_ms=900, status="timeout", error="timed out")
    audit.record("appointment.created", "appointment", 7)

    view = _view(audit.for_appointment(7), ai_calls.history(7), "en")
    assert view["ai"]["calls"] == 4 and view["ai"]["failures"] == 1
    assert view["ai"]["model"] == "gemma3"
    assert view["ai"]["seconds"] == pytest.approx(4.5, abs=0.1)


def test_hebrew_reads_in_hebrew(db) -> None:
    from web.services import audit

    with audit.acting_as("patient", "42"):
        audit.record("followup.answered", "followup", 7, after={"pain_level": 3})

    view = _view(audit.for_appointment(7), [], "he")
    assert view["items"][0]["who"] == "המטופל/ת"
    assert "hist_" not in str(view), "every label is translated, not a key"


# ── on the pages ──
async def test_the_treatment_page_shows_the_sessions_history(
    authenticated_client, make_patient
) -> None:
    import bot.db as dbmod

    booked = await authenticated_client.post(
        "/api/appointments", json={"patient_name": "Dana", "date": DAY, "time": TIME}
    )
    assert booked.status_code == 200, booked.text
    row = (
        dbmod.get_db()
        .execute(
            "SELECT patient_id FROM appointments WHERE id=?", (booked.json()["appointment_id"],)
        )
        .fetchone()
    )
    slug = f"{row['patient_id']}/{DAY}/{SLUG_TIME}"
    assert (
        await authenticated_client.post(
            f"/api/treatment-notes/{slug}", json={"session_notes": "tender at LI4"}
        )
    ).status_code == 200

    page = (await authenticated_client.get(f"/treatment/{slug}")).text
    assert 'id="session-history"' in page
    assert "Appointment booked" in page and "Notes updated" in page
    assert "You" in page
    assert "tender at LI4" not in page.split('id="session-history"')[1][:2000]


async def test_the_archive_shows_the_same_history(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    from web.services import audit

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=970_000_002)
    apt = make_appointment(therapist={"id": tid}, patient=patient, apt_date=DAY, apt_time=TIME)
    make_treatment_notes(apt)
    with audit.acting_as("therapist", tid):
        audit.record("session.completed", "treatment_notes", apt["id"], after={"tongue": "pale"})

    page = (
        await authenticated_client.get(f"/patients/{patient['patient_id']}/session/{apt['id']}")
    ).text
    assert 'id="session-history"' in page and "Session completed" in page


async def test_a_session_with_no_history_shows_no_card(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Noa", telegram_id=970_000_003)
    apt = make_appointment(therapist={"id": tid}, patient=patient, apt_date=DAY, apt_time=TIME)
    make_treatment_notes(apt)

    page = (
        await authenticated_client.get(f"/patients/{patient['patient_id']}/session/{apt['id']}")
    ).text
    assert "Appointment booked" not in page
