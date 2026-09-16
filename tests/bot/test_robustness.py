"""Phase 2.2c — BOT_AUDIT B6, B8, B11: never leave a patient staring at silence.

Three ways the bot used to go quiet on people: an exception reached nobody (B8), `/start` mid-flow
dropped a half-finished booking without a word (B6), and a button from an older keyboard produced
an endless spinner (B11).
"""

from __future__ import annotations

from typing import Any

import pytest

# Imported before the frozen-clock fixture can patch `datetime.date` (LangChain + pydantic.v1).
from bot.errors import on_error, stale_button
from bot.patient_bot.start import start
from bot.states import SELECTING
from tests.bot.conftest import FakeQuery, make_context, make_update

PATIENT = 900_000_701


@pytest.fixture
def one_therapist(db, make_therapist):
    from bot import config as botcfg

    t = make_therapist(name="Dr Only", therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    return t


# ── B8: an exception must reach the user, not only the log ──
async def test_error_handler_tells_the_user_something_went_wrong(db, fake_redis, caplog) -> None:
    update = make_update("book me in", user_id=PATIENT)
    context = make_context()
    context.error = RuntimeError("database is locked")

    await on_error(update, context)

    assert update.message.reply_texts(), "the patient is told, not left in silence"
    assert "database is locked" in caplog.text, "the cause is in the log with its traceback"


async def test_error_handler_stops_the_spinner_on_a_button(db, fake_redis) -> None:
    query = FakeQuery("schedule", user_id=PATIENT)
    update = make_update(None, user_id=PATIENT, query=query)
    context = make_context()
    context.error = RuntimeError("redis is down")

    await on_error(update, context)

    assert query.answered, "an unanswered callback query spins forever in the client"


async def test_error_handler_survives_an_update_it_cannot_reply_to(db, fake_redis) -> None:
    context = make_context()
    context.error = RuntimeError("boom")
    await on_error(None, context)  # must not raise


async def test_a_failing_reply_does_not_raise_out_of_the_error_handler(db, fake_redis) -> None:
    update = make_update("hello", user_id=PATIENT)

    async def _fail(*a: Any, **k: Any) -> None:
        raise RuntimeError("telegram is down too")

    update.message.reply_text = _fail
    context = make_context()
    context.error = RuntimeError("original")
    await on_error(update, context)  # must not raise


# ── B6: /start is a clean reset, and says so ──
async def test_start_drops_an_unfinished_booking_and_says_so(db, fake_redis, one_therapist) -> None:
    update = make_update("/start", user_id=PATIENT)
    context = make_context(
        {
            "selected_therapist": "t1",
            "selected_day": "2026-03-12",
            "selected_time": "10:00",
            "intake_count": 3,
        }
    )

    assert await start(update, context) == SELECTING
    assert "selected_day" not in context.user_data
    assert "selected_time" not in context.user_data
    assert "intake_count" not in context.user_data
    assert context.user_data["selected_therapist"] == "t1", "their therapist is not forgotten"
    joined = " ".join(update.message.reply_texts()).lower()
    assert "not" in joined and ("book" in joined or "finish" in joined), joined


async def test_start_with_nothing_in_flight_says_nothing_extra(
    db, fake_redis, one_therapist
) -> None:
    update = make_update("/start", user_id=PATIENT)
    context = make_context({"selected_therapist": "t1"})

    assert await start(update, context) == SELECTING
    assert len(update.message.reply_texts()) == 1, "just the menu, no phantom cancellation notice"


# ── B11: a button from an older keyboard gets an answer ──
async def test_a_stale_button_is_answered_and_the_menu_comes_back(
    db, fake_redis, one_therapist
) -> None:
    query = FakeQuery("hour_10:00", user_id=PATIENT)
    update = make_update(None, user_id=PATIENT, query=query)

    assert await stale_button(update, make_context()) == SELECTING
    assert query.answered, "no endless spinner"
    assert query.edits, "the patient gets a working menu instead of a dead keyboard"


def test_stale_callbacks_are_reachable_from_outside_a_conversation() -> None:
    """A restart empties the in-memory conversation state; every old button lands here."""
    from telegram.ext import CallbackQueryHandler, ConversationHandler

    from bot.main import build_patient_app

    app = build_patient_app()
    conv = next(h for hs in app.handlers.values() for h in hs if isinstance(h, ConversationHandler))
    entry_callbacks = [
        getattr(h.callback, "__name__", "")
        for h in conv.entry_points
        if isinstance(h, CallbackQueryHandler)
    ]
    fallback_callbacks = [
        getattr(h.callback, "__name__", "")
        for h in conv.fallbacks
        if isinstance(h, CallbackQueryHandler)
    ]
    assert "stale_button" in entry_callbacks, "a button pressed with no conversation state"
    assert "stale_button" in fallback_callbacks, "a button that no state handler claims"


def test_the_conversation_never_allows_reentry() -> None:
    """`allow_reentry=True` breaks INTAKE and THERAPIST_INPUT (CLAUDE.md)."""
    from telegram.ext import ConversationHandler

    from bot.main import build_patient_app

    app = build_patient_app()
    conv = next(h for hs in app.handlers.values() for h in hs if isinstance(h, ConversationHandler))
    assert conv.allow_reentry is False


# ── /cancel and /help ──
async def test_cancel_command_returns_to_the_menu(db, fake_redis, one_therapist) -> None:
    from bot.patient_bot.commands import cancel_command

    update = make_update("/cancel", user_id=PATIENT)
    context = make_context({"selected_therapist": "t1", "selected_day": "2026-03-12"})

    assert await cancel_command(update, context) == SELECTING
    assert "selected_day" not in context.user_data
    assert update.message.reply_texts()


async def test_help_command_explains_the_bot(db, fake_redis, one_therapist) -> None:
    from bot.patient_bot.commands import help_command

    update = make_update("/help", user_id=PATIENT)
    await help_command(update, make_context({"selected_therapist": "t1"}))

    said = " ".join(update.message.reply_texts()).lower()
    assert "/start" in said and "/cancel" in said


def test_both_commands_are_registered() -> None:
    from telegram.ext import CommandHandler, ConversationHandler

    from bot.main import build_patient_app

    app = build_patient_app()
    conv = next(h for hs in app.handlers.values() for h in hs if isinstance(h, ConversationHandler))
    commands = {c for h in conv.fallbacks if isinstance(h, CommandHandler) for c in h.commands}
    assert {"start", "cancel", "help"} <= commands


def test_the_error_handler_is_installed_on_both_bots() -> None:
    from bot.main import build_patient_app
    from bot.therapist_bot.main import build_therapist_app

    for app in (build_patient_app(), build_therapist_app()):
        assert app is not None
        names = [getattr(h, "__name__", "") for h in app.error_handlers]
        assert "on_error" in names
