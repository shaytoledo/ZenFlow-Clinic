"""The booking state machine, end to end: therapist → week → day → hour → intake choice → saved.

Phase 2.2b. These pin the transitions the ConversationHandler depends on (CLAUDE.md: every handler
returns the next state constant) and the two dead ends a patient can hit — no free days, no free
hours — which used to be untested.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from bot.states import (
    INTAKE,
    INTAKE_CONFIRM,
    SCHEDULE_DAY,
    SCHEDULE_HOUR,
    SCHEDULE_WEEK,
    SELECTING,
    THERAPIST_SELECT,
)
from tests.bot.conftest import FakeQuery, make_context, make_update

PATIENT = 900_000_501
DAY = date(2026, 3, 12)


@pytest.fixture
def availability(monkeypatch):
    """Two free days, three free hours, and a calendar that accepts the booking."""
    from bot.patient_bot import schedule

    state: dict[str, Any] = {
        "days": [DAY, date(2026, 3, 13)],
        "hours": ["09:00", "10:00", "11:00"],
        "booked": [],
    }

    async def _days(week_offset: int = 0, therapist_id: str | None = None):
        return state["days"]

    async def _hours(day, therapist_id: str | None = None):
        return state["hours"]

    async def _book(*a, **k):
        state["booked"].append((a, k))
        return "gcal-evt-1"

    monkeypatch.setattr(schedule, "get_available_days", _days)
    monkeypatch.setattr(schedule, "get_available_hours", _hours)
    monkeypatch.setattr(schedule, "book_slot", _book)
    return state


def _step(data: str, user_data: dict):
    query = FakeQuery(data, user_id=PATIENT)
    return make_update(None, user_id=PATIENT, query=query), make_context(user_data), query


async def test_one_therapist_is_chosen_for_the_patient(
    db, fake_redis, make_therapist, availability
):
    from bot import config as botcfg
    from bot.patient_bot.schedule import show_therapist_choice

    make_therapist(name="Dr Only", therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()

    update, context, query = _step("schedule", {})
    state = await show_therapist_choice(update, context)

    assert state == SCHEDULE_WEEK, "a single therapist is auto-selected and the week is asked"
    assert context.user_data["selected_therapist"] == "t1"
    assert query.answered


async def test_two_therapists_are_offered(db, fake_redis, make_therapist, availability):
    from bot import config as botcfg
    from bot.patient_bot.schedule import show_therapist_choice

    make_therapist(name="Dr A", therapist_id="t1", telegram_id=700_001)
    make_therapist(name="Dr B", therapist_id="t2", telegram_id=700_002)
    botcfg.reload_therapists()

    update, context, query = _step("schedule", {})
    assert await show_therapist_choice(update, context) == THERAPIST_SELECT
    buttons = [b.text for row in query.edits[0]["reply_markup"].inline_keyboard for b in row]
    assert "Dr A" in buttons and "Dr B" in buttons


async def test_the_whole_booking_walk(db, fake_redis, make_therapist, availability):
    from bot import config as botcfg
    from bot.patient_bot import schedule

    t = make_therapist(name="Dr Only", therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    user_data: dict = {"selected_therapist": t["id"]}

    update, context, _ = _step("week_0", user_data)
    assert await schedule.show_days(update, context) == SCHEDULE_DAY
    assert user_data["selected_week"] == 0

    update, context, _ = _step(f"day_{DAY.isoformat()}", user_data)
    assert await schedule.show_hours(update, context) == SCHEDULE_HOUR
    assert user_data["selected_day"] == DAY.isoformat()

    update, context, _ = _step("hour_10:00", user_data)
    assert await schedule.confirm_appointment(update, context) == INTAKE_CONFIRM
    assert user_data["selected_time"] == "10:00"

    update, context, query = _step("intake_no", user_data)
    assert await schedule.skip_intake(update, context) == SELECTING

    from bot.db import get_db
    from web.repositories import patient_repo

    # Phase 7.2: the Telegram user became a patient; the appointment holds the patient's id
    pid = patient_repo.find_by_channel("telegram", PATIENT)
    row = get_db().execute("SELECT * FROM appointments WHERE patient_id=?", (pid,)).fetchone()
    assert row["date"] == DAY.isoformat() and row["time"] == "10:00"
    assert row["therapist_id"] == t["id"]
    assert row["gcal_apt_event_id"] == "gcal-evt-1", "the calendar event is linked to the booking"
    assert availability["booked"], "the hour was removed from availability"
    assert user_data["selected_therapist"] == t["id"], "the therapist survives the cleared state"
    assert "selected_day" not in user_data


async def test_yes_to_intake_asks_the_first_question(
    db, fake_redis, make_therapist, availability, monkeypatch
):
    from bot import config as botcfg
    from bot.patient_bot import schedule

    make_therapist(therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    monkeypatch.setattr(schedule, "initialize_intake", lambda *a, **k: None)

    user_data: dict[str, Any] = {
        "selected_therapist": "t1",
        "selected_day": DAY.isoformat(),
        "selected_time": "10:00",
    }
    update, context, query = _step("intake_yes", user_data)
    assert await schedule.start_intake(update, context) == INTAKE
    assert user_data["intake_count"] == 0
    assert context.bot.sent, "the first question is sent as its own message"


async def test_a_week_with_no_free_day_says_so(db, fake_redis, make_therapist, availability):
    from bot import config as botcfg
    from bot.patient_bot import schedule

    make_therapist(therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    availability["days"] = []

    update, context, query = _step("week_1", {"selected_therapist": "t1"})
    assert await schedule.show_days(update, context) == SCHEDULE_WEEK, "the patient can pick again"
    assert query.edits, "they are told the week is full"


async def test_a_day_with_no_free_hour_says_so(db, fake_redis, make_therapist, availability):
    from bot import config as botcfg
    from bot.patient_bot import schedule

    make_therapist(therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    availability["hours"] = []

    update, context, query = _step(f"day_{DAY.isoformat()}", {"selected_therapist": "t1"})
    assert await schedule.show_hours(update, context) == SCHEDULE_DAY, "back to the day list"
    assert query.edits
