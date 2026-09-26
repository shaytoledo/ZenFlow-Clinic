"""Phase 11.3 — the booking flow's therapist-selection routing and intake-answer completion.

`test_booking_flow` walks the happy path and the no-availability dead ends; this covers the branches
it skips in `schedule.py`: how a chosen therapist is routed (contact / welcome / schedule), the
no-therapists message, and — the biggest gap — `handle_intake_answer`, which asks the next question
until the fifth answer, then books with the intake transcript, starts the generation pipeline, and
handles a slot taken out from under the patient mid-intake.
"""

from __future__ import annotations

from datetime import date

import pytest

from bot.patient_bot import schedule as sch
from bot.states import (
    INTAKE,
    SCHEDULE_WEEK,
    SELECTING,
    THERAPIST_INPUT,
)
from tests.bot.conftest import FakeQuery, make_context, make_update

pytestmark = pytest.mark.integration

PATIENT = 900_600_001
DAY = date(2026, 3, 12)


def _reload() -> None:
    from bot import config as botcfg

    botcfg.reload_therapists()


@pytest.fixture
def bookable(monkeypatch: pytest.MonkeyPatch) -> None:
    """10:00 is offered as available and the calendar step succeeds; the appointment row
    (save_appointment, and its slot-conflict check) stays real."""
    from web.services import booking_service

    async def _book_slot(*a, **k):
        return "gcal-evt-x"

    async def _hours(day, therapist_id=None):
        return ["09:00", "10:00", "11:00"]

    monkeypatch.setattr(booking_service, "book_slot", _book_slot)
    monkeypatch.setattr(booking_service, "get_available_hours", _hours)


def _cb(data: str, user_data: dict):
    query = FakeQuery(data, user_id=PATIENT)
    return make_update(None, user_id=PATIENT, query=query), make_context(user_data), query


# ── therapist selection routing (select_therapist_and_continue) ──────────────────


async def test_select_therapist_contact_flow_prompts_for_a_message(fake_redis, make_therapist):
    make_therapist(name="Dr A", therapist_id="t1")
    _reload()
    update, ctx, query = _cb("sel_t_t1", {"therapist_flow": "contact"})
    assert await sch.select_therapist_and_continue(update, ctx) == THERAPIST_INPUT
    assert ctx.user_data["selected_therapist"] == "t1"
    assert query.answered and query.edits


async def test_select_therapist_welcome_flow_returns_to_menu(fake_redis, make_therapist):
    make_therapist(name="Dr A", therapist_id="t1")
    _reload()
    update, ctx, query = _cb("sel_t_t1", {"therapist_flow": "welcome"})
    assert await sch.select_therapist_and_continue(update, ctx) == SELECTING
    assert query.edits and "Dr A" in query.edits[-1]["text"]


async def test_select_therapist_schedule_flow_asks_the_week(fake_redis, make_therapist):
    make_therapist(name="Dr A", therapist_id="t1")
    _reload()
    update, ctx, _ = _cb("sel_t_t1", {"therapist_flow": "schedule"})
    assert await sch.select_therapist_and_continue(update, ctx) == SCHEDULE_WEEK


async def test_show_therapist_choice_with_no_active_therapists(fake_redis):
    _reload()  # empty DB
    update, ctx, query = _cb("schedule", {})
    assert await sch.show_therapist_choice(update, ctx) == SELECTING
    assert query.edits, "the patient is told to try again later"


# ── intake answers (handle_intake_answer) ────────────────────────────────────────


async def test_answer_before_the_fifth_asks_the_next_question(
    fake_redis, make_therapist, monkeypatch
):
    make_therapist(therapist_id="t1")
    _reload()
    from bot.services import flood

    monkeypatch.setattr(flood, "per_minute", lambda: 0)  # disable throttle for this test

    async def _next(_uid, _ans, lang="en"):
        return "And how long has that been going on?"

    monkeypatch.setattr(sch, "get_next_question", _next)

    ctx = make_context({"selected_therapist": "t1", "intake_count": 1})
    update = make_update("my lower back aches", user_id=PATIENT)
    assert await sch.handle_intake_answer(update, ctx) == INTAKE
    assert ctx.user_data["intake_count"] == 2, "the answer is counted"
    assert "how long" in " ".join(update.message.reply_texts()).lower()


async def test_the_fifth_answer_books_and_starts_generation(
    fake_redis, make_therapist, bookable, monkeypatch
):
    make_therapist(therapist_id="t1")
    _reload()
    from bot.services import flood

    monkeypatch.setattr(flood, "per_minute", lambda: 0)
    started: list = []
    monkeypatch.setattr(sch, "_start_generation", lambda apt_id, uid: started.append((apt_id, uid)))

    ctx = make_context(
        {
            "selected_therapist": "t1",
            "selected_day": DAY.isoformat(),
            "selected_time": "10:00",
            "intake_count": 4,  # this answer is the fifth
        }
    )
    update = make_update("that is everything", user_id=PATIENT)
    assert await sch.handle_intake_answer(update, ctx) == SELECTING

    from bot.db import get_db
    from web.repositories import patient_repo

    pid = patient_repo.find_by_channel("telegram", PATIENT)
    row = get_db().execute("SELECT * FROM appointments WHERE patient_id=?", (pid,)).fetchone()
    assert row and row["date"] == DAY.isoformat() and row["time"] == "10:00"
    assert row["therapist_id"] == "t1"
    assert started, "the summary→diagnosis→points pipeline was queued"
    assert ctx.user_data.get("selected_therapist") == "t1", "the therapist survives the reset"


async def test_the_fifth_answer_reports_a_slot_taken_mid_intake(
    fake_redis, make_therapist, bookable, make_appointment, monkeypatch
):
    make_therapist(therapist_id="t1")
    _reload()
    from bot.services import flood

    monkeypatch.setattr(flood, "per_minute", lambda: 0)
    # Someone else booked t1 at DAY 10:00 while this patient was answering questions.
    make_appointment(therapist={"id": "t1"}, apt_date=DAY.isoformat(), apt_time="10:00")

    ctx = make_context(
        {
            "selected_therapist": "t1",
            "selected_day": DAY.isoformat(),
            "selected_time": "10:00",
            "intake_count": 4,
        }
    )
    update = make_update("that is everything", user_id=PATIENT)
    assert await sch.handle_intake_answer(update, ctx) == SELECTING

    from bot.db import get_db
    from web.repositories import patient_repo

    pid = patient_repo.find_by_channel("telegram", PATIENT)
    booked = (
        get_db()
        .execute("SELECT COUNT(*) AS c FROM appointments WHERE patient_id=?", (pid,))
        .fetchone()["c"]
    )
    assert booked == 0, "no appointment is created for the patient whose slot was taken"
    assert update.message.reply_texts(), "the patient is told the slot is gone"
