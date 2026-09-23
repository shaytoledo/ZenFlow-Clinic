"""Plan 9.5 (part 3) — Telegram-side flood control.

Per-user (per Telegram id) limits on the three bot surfaces the plan names: activation-code entry
(guessing a pending registration code), the patient→therapist relay (spamming the therapist), and
intake answers (pinning the Ollama box). Over budget, the bot replies "slow down" and does the work
of neither forwarding, registering, nor asking the next question.
"""

from __future__ import annotations

import pytest

from tests.bot.conftest import make_context, make_update

pytestmark = pytest.mark.integration

PATIENT = 900_500_001


def _slow(replies: list[str]) -> bool:
    return any(
        ("wait" in r.lower()) or ("slow" in r.lower()) or ("too many" in r.lower()) or ("⏳" in r)
        for r in replies
    )


# ── the service ──
async def test_too_fast_trips_at_the_budget(monkeypatch) -> None:
    from bot.services import flood

    monkeypatch.setattr(flood, "per_minute", lambda: 2)
    assert await flood.too_fast("relay", 111) is False
    assert await flood.too_fast("relay", 111) is False
    assert await flood.too_fast("relay", 111) is True, "the third in a 2/min window is over budget"
    # a different kind/user has its own budget
    assert await flood.too_fast("relay", 222) is False
    assert await flood.too_fast("intake", 111) is False


async def test_disabled_when_zero(monkeypatch) -> None:
    from bot.services import flood

    monkeypatch.setattr(flood, "per_minute", lambda: 0)
    for _ in range(10):
        assert await flood.too_fast("relay", 111) is False


# ── the relay ──
async def test_relay_throttles_a_flood(therapist_bot, make_therapist, monkeypatch) -> None:
    from bot import config as botcfg
    from bot.patient_bot import therapist as pt
    from bot.services import flood
    from bot.states import THERAPIST_RELAY

    make_therapist(therapist_id="t1", active=True, telegram_id=111)
    botcfg.reload_therapists()
    monkeypatch.setattr(flood, "per_minute", lambda: 2)
    ctx = make_context(user_data={"selected_therapist": "t1"})

    for _ in range(2):
        assert (
            await pt.relay_to_therapist(make_update("hi", user_id=PATIENT), ctx) == THERAPIST_RELAY
        )
    assert len(therapist_bot.sent) == 2, "both in-budget messages are forwarded"

    blocked = make_update("spam", user_id=PATIENT)
    assert await pt.relay_to_therapist(blocked, ctx) == THERAPIST_RELAY
    assert len(therapist_bot.sent) == 2, "the flooding message is NOT forwarded"
    assert _slow(blocked.message.reply_texts())


# ── intake ──
async def test_intake_throttles_a_flood_without_consuming_a_question(monkeypatch) -> None:
    from bot.patient_bot import schedule as sch
    from bot.services import flood
    from bot.states import INTAKE

    monkeypatch.setattr(flood, "per_minute", lambda: 1)
    # exhaust this user's intake budget directly, so the handler never reaches the AI
    assert await flood.too_fast("intake", PATIENT) is False
    assert await flood.too_fast("intake", PATIENT) is True

    ctx = make_context(user_data={"selected_therapist": "t1"})
    blocked = make_update("spam", user_id=PATIENT)
    assert await sch.handle_intake_answer(blocked, ctx) == INTAKE
    assert ctx.user_data.get("intake_count", 0) == 0, "a throttled message must not use a question"
    assert _slow(blocked.message.reply_texts())


# ── activation code ──
async def test_activation_code_attempts_are_throttled(monkeypatch) -> None:
    from bot.services import flood
    from bot.therapist_bot import handlers as th

    monkeypatch.setattr(flood, "per_minute", lambda: 2)
    code = "ABCD1234"  # matches ^[A-Z0-9]{8}$ but is not a real pending code

    for _ in range(2):
        u = make_update(code, user_id=888)
        await th.handle_therapist_message(u, make_context())

    blocked = make_update(code, user_id=888)
    await th.handle_therapist_message(blocked, make_context())
    assert _slow(blocked.message.reply_texts()), "a code-guessing flood is throttled"
