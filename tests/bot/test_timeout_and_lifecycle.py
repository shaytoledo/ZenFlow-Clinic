"""Phase 2.2d — BOT_AUDIT B10 (stuck conversations) and B14 (leaked Bot clients).

B10: nothing ever expired. A patient left in `THERAPIST_RELAY` had every later message — "I want
to book Tuesday" — forwarded to their therapist days afterwards, and an abandoned intake waited
forever.

B14: each relay module built its own module-level `Bot(token=...)` at import time. Those clients
were never initialised or shut down: they leak an httpx pool on exit and bypass the application's
rate limiter.
"""

from __future__ import annotations

from telegram.ext import ConversationHandler

from bot.patient_bot.timeout import on_conversation_timeout
from bot.states import SELECTING
from tests.bot.conftest import FakeBot, make_context, make_update

PATIENT = 900_000_801


def _conversation():
    from bot.main import build_patient_app

    app = build_patient_app()
    conv = next(h for hs in app.handlers.values() for h in hs if isinstance(h, ConversationHandler))
    return app, conv


# ── B10 ──
def test_the_conversation_expires(db) -> None:
    from zenflow.settings import get_settings

    _, conv = _conversation()
    expected = get_settings().flags.conv_timeout_minutes * 60
    assert expected > 0
    assert conv.conversation_timeout == expected


def test_a_timeout_handler_exists_for_the_timeout_state(db) -> None:
    _, conv = _conversation()
    handlers = conv.states.get(ConversationHandler.TIMEOUT, [])
    assert handlers, "without a TIMEOUT handler the conversation dies silently"
    assert any(getattr(h.callback, "__name__", "") == "on_conversation_timeout" for h in handlers)


def test_the_app_can_actually_fire_timeouts(db) -> None:
    """`conversation_timeout` needs a JobQueue; without one PTB only warns and nothing expires."""
    app, _ = _conversation()
    assert app.job_queue is not None


async def test_the_timeout_tells_the_patient_and_clears_the_flow(db, fake_redis, make_therapist):
    from bot import config as botcfg

    make_therapist(name="Dr Only", therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()

    update = make_update("(no reply for half an hour)", user_id=PATIENT)
    context = make_context(
        {"selected_therapist": "t1", "selected_day": "2026-03-12", "intake_count": 2}
    )

    assert await on_conversation_timeout(update, context) == ConversationHandler.END
    assert "selected_day" not in context.user_data
    assert "intake_count" not in context.user_data
    assert context.user_data["selected_therapist"] == "t1"
    assert update.message.reply_texts(), "the patient is told, not dropped in silence"


async def test_the_timeout_works_without_a_message_to_reply_to(db, fake_redis, make_therapist):
    """The last update may have been a button press, or nothing at all."""
    from bot import config as botcfg

    make_therapist(therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()

    update = make_update(None, user_id=PATIENT)
    update.message = None
    context = make_context({"selected_therapist": "t1", "selected_day": "2026-03-12"})

    assert await on_conversation_timeout(update, context) == ConversationHandler.END
    assert context.bot.sent, "the notice is sent to the chat instead"


def test_the_timeout_can_be_switched_off(db, monkeypatch) -> None:
    """`ZF_CONV_TIMEOUT_MINUTES=0` means "never expire" — the clinic's call, not ours."""
    import zenflow.settings as settings_mod

    monkeypatch.setenv("ZF_CONV_TIMEOUT_MINUTES", "0")
    settings_mod.reset_settings()
    try:
        _, conv = _conversation()
        assert conv.conversation_timeout is None
    finally:
        settings_mod.reset_settings()


# ── B14 ──
def test_no_bot_client_is_built_at_import_time(db) -> None:
    """Importing a handler module must not open a Telegram client (or need a valid token)."""
    import inspect

    import bot.patient_bot.therapist as pt
    import bot.therapist_bot.handlers as th

    for module in (pt, th):
        source = inspect.getsource(module)
        assert "= Bot(token=" not in source, f"{module.__name__} still builds a Bot at import time"


def test_the_running_applications_are_wired_to_each_other(db, monkeypatch) -> None:
    import bot.patient_bot.therapist as pt
    import bot.therapist_bot.handlers as th
    from bot.main import wire_bots

    patient_bot, therapist_bot = FakeBot(), FakeBot()
    monkeypatch.setattr(pt, "_therapist_bot", None)
    monkeypatch.setattr(th, "_patient_bot", None)

    wire_bots(patient_bot, therapist_bot)
    wired_therapist: object = pt._therapist_bot
    wired_patient: object = th._patient_bot

    assert wired_therapist is therapist_bot, "the patient bot forwards through the therapist app"
    assert wired_patient is patient_bot, "the therapist bot replies through the patient app"


async def test_relay_says_so_when_the_bots_are_not_wired(
    db, fake_redis, make_therapist, monkeypatch
):
    import bot.patient_bot.therapist as pt
    from bot import config as botcfg

    t = make_therapist(therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    monkeypatch.setattr(pt, "_therapist_bot", None)

    update = make_update("hello?", user_id=PATIENT)
    state = await pt.start_relay(update, make_context({"selected_therapist": t["id"]}))

    assert state == SELECTING
    assert update.message.reply_texts(), "the patient is told, not ignored"
