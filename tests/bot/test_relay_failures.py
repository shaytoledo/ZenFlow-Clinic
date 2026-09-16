"""Phase 2.2b — BOT_AUDIT B9: a Redis failure must not be reported as a delivery failure.

`start_relay` sent the message to the therapist and then saved the routing mapping under the same
`try`. When Redis was down the patient was told "Could not reach the therapist" although the
therapist had already received the message.
"""

from __future__ import annotations

from bot.states import THERAPIST_RELAY
from tests.bot.conftest import make_context, make_update

PATIENT = 900_000_401


async def test_mapping_failure_is_not_reported_as_a_failed_send(
    db, fake_redis, therapist_bot, make_therapist, monkeypatch
) -> None:
    from bot.patient_bot import therapist as pt

    t = make_therapist(therapist_id="t1", telegram_id=700_001)
    from bot import config as botcfg

    botcfg.reload_therapists()

    def _boom(*a, **k):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(pt, "save_relay_mapping", _boom)

    update = make_update("my back hurts", user_id=PATIENT)
    state = await pt.start_relay(update, make_context({"selected_therapist": t["id"]}))

    assert len(therapist_bot.sent) == 1, "the therapist did receive it"
    said = " ".join(update.message.reply_texts()).lower()
    assert "could not reach" not in said, said
    assert state == THERAPIST_RELAY, "the patient stays in the chat they successfully opened"


async def test_a_real_send_failure_is_still_reported(
    db, fake_redis, make_therapist, monkeypatch
) -> None:
    from bot.patient_bot import therapist as pt
    from bot.states import SELECTING
    from tests.bot.conftest import FakeBot

    t = make_therapist(therapist_id="t1", telegram_id=700_001)
    from bot import config as botcfg

    botcfg.reload_therapists()
    monkeypatch.setattr(pt, "_therapist_bot", FakeBot(fail_with=RuntimeError("telegram is down")))

    update = make_update("my back hurts", user_id=PATIENT)
    state = await pt.start_relay(update, make_context({"selected_therapist": t["id"]}))

    assert state == SELECTING
    assert "could not reach" in " ".join(update.message.reply_texts()).lower()
