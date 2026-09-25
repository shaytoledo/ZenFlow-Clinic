"""Phase 11.3 — the patient→therapist contact/relay entry handlers.

Covers the parts of `bot/patient_bot/therapist.py` the relay tests skipped: choosing a therapist to
contact (`show_therapist_for_contact`), the message prompt (`ask_therapist_message`), ending a chat
(`end_chat`), and the security-critical rule that a patient's message is never silently rerouted to a
therapist they did not choose (`_get_therapist` / B12).
"""

from __future__ import annotations

from bot.patient_bot import therapist as pt
from bot.states import SELECTING, THERAPIST_INPUT, THERAPIST_RELAY, THERAPIST_SELECT
from tests.bot.conftest import FakeQuery, make_context, make_update

PATIENT = 900_000_888


def _reload() -> None:
    from bot import config as botcfg

    botcfg.reload_therapists()


async def test_contact_with_no_active_therapists_returns_to_menu() -> None:
    _reload()
    query = FakeQuery(data="contact_therapist", user_id=PATIENT)
    state = await pt.show_therapist_for_contact(make_update(query=query), make_context())
    assert state == SELECTING
    assert "No therapists" in query.edits[-1]["text"]


async def test_contact_with_one_therapist_prompts_for_the_message(make_therapist) -> None:
    t = make_therapist(name="Dr Solo", therapist_id="t_solo")
    _reload()
    query = FakeQuery(data="contact_therapist", user_id=PATIENT)
    ctx = make_context()
    state = await pt.show_therapist_for_contact(make_update(query=query), ctx)
    assert state == THERAPIST_INPUT
    assert ctx.user_data["selected_therapist"] == t["id"]
    assert "say to the therapist" in query.edits[-1]["text"].lower()


async def test_contact_with_an_existing_choice_skips_reselection(make_therapist) -> None:
    make_therapist(name="Dr A", therapist_id="t_a")
    make_therapist(name="Dr B", therapist_id="t_b")
    _reload()
    query = FakeQuery(data="contact_therapist", user_id=PATIENT)
    ctx = make_context({"selected_therapist": "t_a"})
    state = await pt.show_therapist_for_contact(make_update(query=query), ctx)
    assert state == THERAPIST_INPUT, "an already-chosen therapist is not re-selected"


async def test_contact_with_several_therapists_offers_a_choice(make_therapist) -> None:
    make_therapist(name="Dr A", therapist_id="t_a")
    make_therapist(name="Dr B", therapist_id="t_b")
    _reload()
    query = FakeQuery(data="contact_therapist", user_id=PATIENT)
    state = await pt.show_therapist_for_contact(make_update(query=query), make_context())
    assert state == THERAPIST_SELECT
    labels = [b.text for row in query.edits[-1]["reply_markup"].inline_keyboard for b in row]
    assert "Dr A" in labels and "Dr B" in labels


async def test_ask_therapist_message_prompts_and_advances(make_therapist) -> None:
    make_therapist(therapist_id="t_a")
    _reload()
    query = FakeQuery(data="ask_msg", user_id=PATIENT)
    state = await pt.ask_therapist_message(make_update(query=query), make_context())
    assert state == THERAPIST_INPUT
    assert query.answered and query.edits


async def test_end_chat_closes_the_relay_and_returns_to_menu(fake_redis, make_therapist) -> None:
    t = make_therapist(therapist_id="t_a")
    _reload()
    query = FakeQuery(data="therapist_end", user_id=PATIENT)
    ctx = make_context({"selected_therapist": t["id"]})
    state = await pt.end_chat(make_update(query=query), ctx)
    assert state == SELECTING
    assert "Chat ended" in query.edits[-1]["text"]


async def test_relay_send_failure_is_reported_and_keeps_the_chat_open(
    fake_redis, make_therapist, monkeypatch
) -> None:
    from bot.interfaces import TelegramChannel
    from tests.bot.conftest import FakeBot

    t = make_therapist(therapist_id="t_a", telegram_id=700_020)
    _reload()
    monkeypatch.setattr(
        pt, "_therapist_channel", TelegramChannel(bot=FakeBot(fail_with=RuntimeError("down")))
    )
    update = make_update("hello again", user_id=PATIENT)
    ctx = make_context({"selected_therapist": t["id"]})
    state = await pt.relay_to_therapist(update, ctx)
    assert state == THERAPIST_RELAY, "a transient send failure keeps the chat open to retry"
    assert "could not forward" in " ".join(update.message.reply_texts()).lower()


async def test_relay_never_reroutes_to_an_unchosen_therapist(
    fake_redis, therapist_bot, make_therapist
) -> None:
    # B12: the patient's chosen therapist is now inactive. Their message must NOT fall through to the
    # other, active therapist — they are told the therapist is unavailable and nothing is forwarded.
    make_therapist(name="Dr Active", therapist_id="t_active", active=True, telegram_id=700_010)
    make_therapist(name="Dr Off", therapist_id="t_off", active=False, telegram_id=700_011)
    _reload()

    update = make_update("please help", user_id=PATIENT)
    ctx = make_context({"selected_therapist": "t_off"})
    state = await pt.relay_to_therapist(update, ctx)

    assert state == THERAPIST_RELAY, "the chat stays open"
    assert "not available" in " ".join(update.message.reply_texts()).lower()
    assert therapist_bot.sent == [], "nothing is forwarded to any therapist"
