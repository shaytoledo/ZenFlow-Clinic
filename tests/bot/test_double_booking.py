"""Phase 2.2b — BOT_AUDIT B4: one therapist, one slot, one patient.

Hours are listed when the menu is drawn, but the appointment is written minutes later, after up to
five intake answers. Two patients shown the same free hour both booked it, and the second write
quietly won a slot that was already gone.
"""

from __future__ import annotations

from datetime import date

import pytest

from bot.patient_bot.services.appointments import SlotTaken, save_appointment

DAY = date(2026, 3, 10)
TIME = "10:00"


def _book(therapist_id: str, patient_id: int, *, day: date = DAY, time_slot: str = TIME) -> int:
    return save_appointment(
        patient_id=patient_id,
        patient_name=f"Patient {patient_id}",
        day=day,
        time_slot=time_slot,
        intake_history=[],
        summary="",
        therapist_id=therapist_id,
    )


def test_second_booking_of_the_same_slot_is_refused(db, make_therapist) -> None:
    t = make_therapist(therapist_id="t1")
    _book(t["id"], 101)
    with pytest.raises(SlotTaken):
        _book(t["id"], 102)

    rows = db_rows(t["id"])
    assert len(rows) == 1, "the slot must hold exactly one active appointment"
    assert rows[0]["patient_id"] == 101, "first come, first served"


def test_the_same_hour_with_another_therapist_is_fine(db, make_therapist) -> None:
    a = make_therapist(name="Dr A", therapist_id="t1")
    b = make_therapist(name="Dr B", therapist_id="t2")
    _book(a["id"], 101)
    _book(b["id"], 102)  # must not raise


def test_a_cancelled_appointment_frees_the_slot(db, make_therapist) -> None:
    from bot.patient_bot.services.appointments import cancel_appointment

    t = make_therapist(therapist_id="t1")
    first = _book(t["id"], 101)
    cancel_appointment(first)
    _book(t["id"], 102)  # must not raise


def test_a_different_hour_is_fine(db, make_therapist) -> None:
    t = make_therapist(therapist_id="t1")
    _book(t["id"], 101)
    _book(t["id"], 102, time_slot="11:00")


def test_the_losing_patient_keeps_no_half_written_booking(db, make_therapist) -> None:
    """The intake rows must not survive a refused booking."""
    t = make_therapist(therapist_id="t1")
    _book(t["id"], 101)
    with pytest.raises(SlotTaken):
        save_appointment(
            patient_id=102,
            patient_name="Patient 102",
            day=DAY,
            time_slot=TIME,
            intake_history=[{"q": "how long?", "a": "two weeks"}],
            summary="",
            therapist_id=t["id"],
        )
    conn = __import__("bot.db", fromlist=["get_db"]).get_db()
    leftovers = conn.execute(
        "SELECT COUNT(*) AS n FROM intake_sessions WHERE patient_id=102"
    ).fetchone()
    assert leftovers["n"] == 0


def db_rows(therapist_id: str) -> list:
    from bot.db import get_db

    return (
        get_db()
        .execute(
            "SELECT * FROM appointments WHERE therapist_id=? AND date=? AND time=? AND status='active'",
            (therapist_id, DAY.isoformat(), TIME),
        )
        .fetchall()
    )


# ── the patient must hear about it, and the slot must not be given away ──
async def test_the_patient_is_told_when_the_slot_was_just_taken(
    db, fake_redis, make_therapist, monkeypatch
) -> None:
    from bot.patient_bot import schedule
    from bot.states import SELECTING
    from tests.bot.conftest import FakeQuery, make_context, make_update

    t = make_therapist(therapist_id="t1")
    _book(t["id"], 101)

    released: list[tuple] = []

    async def _fake_book_slot(*a, **k):
        released.append(a)
        return "gcal-1"

    monkeypatch.setattr(schedule, "book_slot", _fake_book_slot)

    query = FakeQuery("intake_no", user_id=102)
    update = make_update(None, user_id=102, query=query)
    context = make_context(
        {"selected_day": DAY.isoformat(), "selected_time": TIME, "selected_therapist": t["id"]}
    )
    state = await schedule.skip_intake(update, context)

    assert state == SELECTING
    said = " ".join(e["text"] for e in query.edits).lower()
    assert "taken" in said or "no longer" in said, said
    assert released == [], "a booking that failed must not remove the hour from availability"
    assert len(db_rows(t["id"])) == 1


async def test_a_successful_booking_still_releases_the_hour(
    db, fake_redis, make_therapist, monkeypatch
) -> None:
    from bot.patient_bot import schedule
    from tests.bot.conftest import FakeQuery, make_context, make_update

    t = make_therapist(therapist_id="t1")
    released: list[tuple] = []

    async def _fake_book_slot(*a, **k):
        released.append(a)
        return "gcal-1"

    monkeypatch.setattr(schedule, "book_slot", _fake_book_slot)

    query = FakeQuery("intake_no", user_id=103)
    update = make_update(None, user_id=103, query=query)
    context = make_context(
        {"selected_day": DAY.isoformat(), "selected_time": TIME, "selected_therapist": t["id"]}
    )
    await schedule.skip_intake(update, context)

    assert len(released) == 1, "the hour is removed from availability exactly once"
    rows = db_rows(t["id"])
    from web.repositories import patient_repo

    assert len(rows) == 1 and rows[0]["patient_id"] == patient_repo.find_by_channel("telegram", 103)
    assert rows[0]["gcal_apt_event_id"] == "gcal-1", "the calendar event id is stored"


# ── the dashboard must not be able to double book either ──
async def test_the_dashboard_refuses_a_slot_the_bot_already_holds(
    db, authenticated_client, make_therapist
) -> None:
    therapist_id = authenticated_client.headers["X-Test-Therapist-Id"]
    _book(therapist_id, 101, day=date(2026, 3, 11))

    resp = await authenticated_client.post(
        "/api/appointments",
        json={
            "patient_name": "Walk-in",
            "date": "2026-03-11",
            "time": TIME,
            "patient_phone": "",
            "patient_email": "",
            "notes": "",
        },
    )
    assert resp.status_code == 409, resp.text
    assert "booked" in resp.json()["detail"].lower()
