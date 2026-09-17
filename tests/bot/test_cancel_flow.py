"""Cancelling an appointment: the slot comes back, the row stays (soft delete).

Phase 2.2b. Cancellation is the other half of the double-booking invariant — a cancelled row must
stop holding its hour — so it is pinned here next to `test_double_booking.py`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

# Imported at module load: freezegun replaces `datetime.date`, and importing the LangChain stack
# (pulled in by cancel.py) under a frozen clock raises a metaclass conflict.
from bot.patient_bot.cancel import confirm_cancel, show_appointments
from bot.patient_bot.services.appointments import save_appointment, telegram_patient
from bot.states import CANCEL_SELECT, SELECTING
from tests.bot.conftest import FakeQuery, make_context, make_update

PATIENT = 900_000_601
DAY = date(2026, 3, 12)


@pytest.fixture
def restored(monkeypatch):
    """Record the hours handed back to the availability calendar."""
    from bot.patient_bot import cancel
    from web.services import booking_service

    calls: list[tuple] = []

    async def _restore(day, time_slot, gcal_id=None, therapist_id=None):
        calls.append((day, time_slot, gcal_id, therapist_id))

    monkeypatch.setattr(booking_service, "restore_slot", _restore)
    monkeypatch.setattr(cancel, "clear_intake", lambda *a, **k: None)
    return calls


def _query(data: str, user_data: dict[str, Any]):
    q = FakeQuery(data, user_id=PATIENT)
    return make_update(None, user_id=PATIENT, query=q), make_context(user_data), q


async def test_upcoming_appointments_are_listed(db, fake_redis, make_therapist, frozen_clock):
    t = make_therapist(therapist_id="t1")
    save_appointment(
        patient_id=telegram_patient(PATIENT),
        patient_name="Test Patient",
        day=DAY,
        time_slot="10:00",
        intake_history=[],
        summary="",
        therapist_id=t["id"],
    )

    user_data: dict[str, Any] = {"selected_therapist": t["id"]}
    update, context, query = _query("cancel", user_data)
    assert await show_appointments(update, context) == CANCEL_SELECT
    assert len(user_data["apts_to_cancel"]) == 1
    assert query.edits


async def test_cancelling_frees_the_hour_and_keeps_the_record(
    db, fake_redis, make_therapist, restored, frozen_clock
):
    from bot.db import get_db

    t = make_therapist(therapist_id="t1")
    apt_id = save_appointment(
        patient_id=telegram_patient(PATIENT),
        patient_name="Test Patient",
        day=DAY,
        time_slot="10:00",
        intake_history=[],
        summary="",
        therapist_id=t["id"],
    )

    user_data: dict[str, Any] = {"selected_therapist": t["id"]}
    update, context, _ = _query("cancel", user_data)
    await show_appointments(update, context)

    update, context, query = _query("cancel_apt_0", user_data)
    assert await confirm_cancel(update, context) == SELECTING

    row = get_db().execute("SELECT * FROM appointments WHERE id=?", (apt_id,)).fetchone()
    assert row is not None, "cancelled appointments are kept for clinical history"
    assert row["status"] == "cancelled"
    assert restored == [(DAY, "10:00", None, t["id"])], "the hour goes back to availability"
    assert user_data["selected_therapist"] == t["id"], "the therapist choice survives"


async def test_nothing_to_cancel_says_so(db, fake_redis, make_therapist, frozen_clock):
    t = make_therapist(therapist_id="t1")
    update, context, query = _query("cancel", {"selected_therapist": t["id"]})
    assert await show_appointments(update, context) == CANCEL_SELECT
    assert query.edits, "the patient is told there is nothing booked"
    assert "apts_to_cancel" not in context.user_data
