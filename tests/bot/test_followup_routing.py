"""Phase 2.2b — BOT_AUDIT B3: a follow-up answer must reach the follow-up, whatever the state.

The 24h follow-up asks for a pain level, then an improvement rating, then notes. Until now only
`start()` consumed those answers, so a patient who happened to be mid-intake or in a therapist
chat had "7" fed to the intake LLM or forwarded to their therapist, and the follow-up never
advanced.
"""

from __future__ import annotations

import pytest
from telegram.ext import ApplicationHandlerStop

from tests.bot.conftest import make_context, make_update

PID = 900_000_301
PATIENT = {"patient_id": PID, "name": "Followup Patient", "source": "telegram"}


async def _open_conversation(appointment_id: int, therapist_id: str, step: int = 1) -> None:
    from bot.services.followup_scheduler import _set_conv_state

    await _set_conv_state(
        PID,
        {
            "appointment_id": appointment_id,
            "therapist_id": therapist_id,
            "step": step,
            "conversation": [],
        },
    )


async def test_followup_answer_is_consumed_in_any_state(db, fake_redis, make_completed_session):
    from bot.patient_bot.followup import handle_followup_reply

    apt = make_completed_session(patient=PATIENT)
    await _open_conversation(apt["id"], apt["therapist_id"])

    update = make_update("4", user_id=PID)
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_reply(update, make_context())

    assert update.message.reply_texts(), "the patient gets the next follow-up question"

    from bot.services.followup_scheduler import _get_conv_state

    state = await _get_conv_state(PID)
    assert state is not None and state["step"] == 2, "the follow-up advanced"
    assert state["pain_level"] == 4


async def test_followup_answer_never_reaches_the_therapist(
    db, fake_redis, therapist_bot, make_completed_session
):
    """The patient is sitting in THERAPIST_RELAY when the follow-up answer arrives."""
    from bot.patient_bot.followup import handle_followup_reply

    apt = make_completed_session(patient=PATIENT)
    await _open_conversation(apt["id"], apt["therapist_id"])

    context = make_context({"selected_therapist": apt["therapist_id"]})
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_reply(make_update("4", user_id=PID), context)

    assert therapist_bot.sent == [], "a follow-up answer is not a message to the therapist"


async def test_finished_followup_thanks_the_patient(db, fake_redis, make_completed_session):
    from bot.patient_bot.followup import handle_followup_reply

    apt = make_completed_session(patient=PATIENT)
    await _open_conversation(apt["id"], apt["therapist_id"], step=3)

    update = make_update("much better, thanks", user_id=PID)
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_reply(update, make_context())

    assert update.message.reply_texts()

    from bot.services.followup_scheduler import _get_conv_state

    assert await _get_conv_state(PID) is None, "the conversation is closed"


async def test_ordinary_message_passes_through_untouched(db, fake_redis):
    from bot.patient_bot.followup import handle_followup_reply

    update = make_update("I want to book an appointment", user_id=PID)
    await handle_followup_reply(update, make_context())  # must not raise

    assert update.message.reply_texts() == [], "nothing is answered; the normal flow continues"


def test_the_gate_runs_before_the_conversation_handler(db):
    """Wiring: the follow-up gate must sit in a lower handler group than the conversation."""
    from telegram.ext import ConversationHandler

    from bot.main import build_patient_app

    app = build_patient_app()
    conv_groups = [
        g
        for g, handlers in app.handlers.items()
        if any(isinstance(h, ConversationHandler) for h in handlers)
    ]
    gate_groups = [
        g
        for g, handlers in app.handlers.items()
        for h in handlers
        if getattr(getattr(h, "callback", None), "__name__", "") == "handle_followup_reply"
    ]
    assert gate_groups, "the follow-up gate is not registered"
    assert conv_groups, "the conversation handler is not registered"
    assert min(gate_groups) < min(conv_groups)
