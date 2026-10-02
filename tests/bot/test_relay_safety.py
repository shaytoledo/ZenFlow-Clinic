"""Phase 2.2a — relay safety: BOT_AUDIT B1, B2, B7, B12.

A therapist reply must never reach a patient other than the intended one, a message must not fail
to deliver because it contains Markdown characters, media must not vanish silently, and a patient
must never be connected to a therapist they did not choose.
"""

from __future__ import annotations

import pytest

from bot.patient_bot.services import relay as prelay
from bot.states import SELECTING, THERAPIST_RELAY
from tests.bot.conftest import FakeMessage, make_context, make_update

PATIENT_A = 900_000_101
PATIENT_B = 900_000_102


@pytest.fixture
def two_therapists(make_therapist):
    a = make_therapist(name="Dr A", telegram_id=700_001, therapist_id="t1")
    b = make_therapist(name="Dr B", telegram_id=700_002, therapist_id="t2")

    return a, b


# ── B1: end of chat must release the therapist's "current patient" slot ──
def test_end_relay_clears_current_only_for_that_patient(db, fake_redis) -> None:
    prelay.save_relay_mapping(11, PATIENT_A, "t1", "A")
    assert fake_redis.sync.get("zenflow:relay:current:t1") == str(PATIENT_A)
    prelay.end_relay(PATIENT_A)
    assert fake_redis.sync.get(f"zenflow:relay:active:{PATIENT_A}") is None
    assert fake_redis.sync.get("zenflow:relay:current:t1") is None


def test_end_relay_does_not_clobber_a_newer_session(db, fake_redis) -> None:
    prelay.save_relay_mapping(11, PATIENT_A, "t1", "A")
    prelay.save_relay_mapping(12, PATIENT_B, "t1", "B")  # B is now the current chat
    prelay.end_relay(PATIENT_A)  # A leaves — B must stay current
    assert fake_redis.sync.get("zenflow:relay:current:t1") == str(PATIENT_B)


async def test_free_typing_after_the_patient_left_is_refused(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    prelay.save_relay_mapping(11, PATIENT_A, "t1", "A")
    prelay.end_relay(PATIENT_A)
    msg = FakeMessage("are you feeling better?", user_id=700_001)
    await _handle_relay(msg, "t1", "en")
    assert patient_bot.sent == [], "no message may go to a patient who ended the chat"
    assert "no active" in " ".join(msg.reply_texts()).lower()


# ── B1: a reply whose mapping expired must not fall back to "current patient" ──
async def test_reply_with_expired_mapping_is_not_routed_to_the_current_patient(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    prelay.save_relay_mapping(50, PATIENT_B, "t1", "B")  # B is current
    stale = FakeMessage("(old forwarded message)", message_id=49)
    msg = FakeMessage("take it twice a day", user_id=700_001, reply_to_message=stale)
    await _handle_relay(msg, "t1", "en")
    assert patient_bot.sent == [], "an expired mapping must not deliver to whoever is current"
    joined = " ".join(msg.reply_texts()).lower()
    assert "expired" in joined or "new message" in joined


# ── B1: free-typing is ambiguous with more than one active chat ──
async def test_free_typing_with_two_active_chats_asks_to_reply(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    prelay.save_relay_mapping(60, PATIENT_A, "t1", "A")
    prelay.save_relay_mapping(61, PATIENT_B, "t1", "B")
    msg = FakeMessage("see you next week", user_id=700_001)
    await _handle_relay(msg, "t1", "en")
    assert patient_bot.sent == []
    assert "reply" in " ".join(msg.reply_texts()).lower()


async def test_free_typing_with_one_active_chat_is_delivered(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    prelay.save_relay_mapping(70, PATIENT_A, "t1", "A")
    msg = FakeMessage("see you next week", user_id=700_001)
    await _handle_relay(msg, "t1", "en")
    assert [m["chat_id"] for m in patient_bot.sent] == [PATIENT_A]


async def test_reply_to_another_therapists_message_is_still_refused(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    prelay.save_relay_mapping(80, PATIENT_A, "t1", "A")
    stale = FakeMessage("(forwarded)", message_id=80)
    msg = FakeMessage("hello", user_id=700_002, reply_to_message=stale)
    await _handle_relay(msg, "t2", "en")
    assert patient_bot.sent == []


# ── B2: Markdown characters must not break delivery ──
MARKDOWN_TEXT = "my email is a_b@c.com and I take 5*3 mg — see [notes](x)"


async def test_patient_message_with_markdown_reaches_the_therapist(
    db, fake_redis, therapist_bot, two_therapists
) -> None:
    from bot.patient_bot.therapist import start_relay

    update = make_update(MARKDOWN_TEXT, user_id=PATIENT_A, full_name="Moshe_K Levi")
    context = make_context({"selected_therapist": "t1"})
    state = await start_relay(update, context)
    assert state == THERAPIST_RELAY
    assert len(therapist_bot.sent) == 1
    sent = therapist_bot.sent[0]
    assert MARKDOWN_TEXT in sent["text"]
    assert sent.get("parse_mode") is None, "user text must not be parsed as Markdown"


async def test_a_follow_up_message_in_an_open_chat_is_plain_text_too(
    db, fake_redis, therapist_bot, two_therapists
) -> None:
    """B2 on the relay loop itself: every later message goes through relay_to_therapist."""
    from bot.patient_bot.therapist import relay_to_therapist, start_relay

    context = make_context({"selected_therapist": "t1"})
    await start_relay(make_update("hello", user_id=PATIENT_A), context)
    state = await relay_to_therapist(make_update(MARKDOWN_TEXT, user_id=PATIENT_A), context)
    assert state == THERAPIST_RELAY
    sent = therapist_bot.sent[-1]
    assert MARKDOWN_TEXT in sent["text"]
    assert sent.get("parse_mode") is None, "a patient's words are never parsed as Markdown"


async def test_therapist_reply_with_markdown_reaches_the_patient(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    prelay.save_relay_mapping(90, PATIENT_A, "t1", "A")
    msg = FakeMessage(MARKDOWN_TEXT, user_id=700_001)
    await _handle_relay(msg, "t1", "en")
    assert len(patient_bot.sent) == 1
    assert MARKDOWN_TEXT in patient_bot.sent[0]["text"]
    assert patient_bot.sent[0].get("parse_mode") is None


# ── B7: media is refused politely, never dropped in silence ──
async def test_patient_media_in_relay_gets_a_clear_answer(
    db, fake_redis, therapist_bot, two_therapists
) -> None:
    from bot.patient_bot.therapist import relay_unsupported_media

    update = make_update(None, user_id=PATIENT_A, photo=["file-id"])
    context = make_context({"selected_therapist": "t1"})
    state = await relay_unsupported_media(update, context)
    assert state == THERAPIST_RELAY, "the patient stays in the chat"
    assert therapist_bot.sent == []
    assert update.message.reply_texts(), "the patient is told it was not sent"
    assert "text" in update.message.reply_texts()[0].lower()


async def test_therapist_media_gets_a_clear_answer(
    db, fake_redis, patient_bot, two_therapists
) -> None:
    from bot.therapist_bot.handlers import handle_therapist_media

    update = make_update(None, user_id=700_001, full_name="Dr A", photo=["file-id"])
    await handle_therapist_media(update, make_context())
    assert patient_bot.sent == []
    assert update.message.reply_texts()


# ── B12: never route a patient to a therapist they did not choose ──
async def test_deactivated_therapist_is_not_silently_replaced(
    db, fake_redis, therapist_bot, make_therapist
) -> None:
    from bot.patient_bot.therapist import start_relay

    make_therapist(name="Dr A", telegram_id=700_001, therapist_id="t1")
    make_therapist(name="Dr B", telegram_id=700_002, therapist_id="t2")
    import bot.db as dbmod

    dbmod.get_db().execute("UPDATE therapists SET active=0 WHERE id='t1'")

    update = make_update("hello?", user_id=PATIENT_A)
    context = make_context({"selected_therapist": "t1"})  # their therapist is no longer active
    state = await start_relay(update, context)
    assert therapist_bot.sent == [], "must not forward to a different therapist"
    assert state == SELECTING
    assert "therapist" in " ".join(update.message.reply_texts()).lower()


# ── B5: handlers must see registry changes, not a snapshot from import time ──
async def test_registry_reload_is_visible_to_handlers(
    db, fake_redis, therapist_bot, make_therapist
) -> None:
    from bot.patient_bot.therapist import _get_therapist

    make_therapist(name="Dr A", telegram_id=700_001, therapist_id="t1")
    context = make_context({"selected_therapist": "t1"})
    chosen = _get_therapist(context)
    assert chosen is not None and chosen["id"] == "t1", "a newly loaded therapist must be visible"

    import bot.db as dbmod

    dbmod.get_db().execute("UPDATE therapists SET active=0 WHERE id='t1'")
    assert _get_therapist(context) is None, "deactivation must apply without a bot restart"


def test_every_lookup_sees_the_database_as_it_is_now(db, make_therapist) -> None:
    """B5 + 12.2.4: no module keeps a copy of the registry. A therapist written to the database (by
    this process or another one) is routable at once; one deactivated stops being routable at once.
    """
    import sqlite3

    import bot.config as botcfg
    import bot.db as dbmod
    from bot import therapists
    from bot.patient_bot.therapist import _get_therapist
    from bot.therapist_bot import handlers

    assert not hasattr(botcfg, "THERAPISTS") and not hasattr(botcfg, "THERAPIST_MAP")
    make_therapist(name="Dr New", telegram_id=700_009, therapist_id="t9")
    chosen = _get_therapist(make_context({"selected_therapist": "t9"}))
    assert chosen is not None and chosen["id"] == "t9"
    assert handlers._therapist_lang(700_009) == "en"

    if not dbmod.is_postgres():  # "another process": a second connection to the same file
        other = sqlite3.connect(dbmod.db_path())
        other.execute("UPDATE therapists SET active=0 WHERE id='t9'")
        other.commit()
        other.close()
    else:
        dbmod.get_db().execute("UPDATE therapists SET active=0 WHERE id='t9'")
    assert therapists.get_active("t9") is None
    assert therapists.get_by_telegram(700_009) is None
    assert _get_therapist(make_context({"selected_therapist": "t9"})) is None


def test_no_choice_and_several_therapists_means_nobody(db, make_therapist) -> None:
    """B12: without the patient's own choice, only a clinic with exactly ONE active therapist has
    an unambiguous recipient — with two, the message must not go to whichever is first."""
    from bot.patient_bot.therapist import _get_therapist

    make_therapist(name="Dr A", telegram_id=700_001, therapist_id="t1")
    make_therapist(name="Dr B", telegram_id=700_002, therapist_id="t2")
    assert _get_therapist(make_context({})) is None


async def test_media_from_a_stranger_is_not_treated_as_a_therapist(
    db, fake_redis, patient_bot
) -> None:
    from bot.therapist_bot.handlers import handle_therapist_media

    update = make_update(None, user_id=123_456, full_name="Someone Else", photo=["file-id"])
    await handle_therapist_media(update, make_context())
    assert patient_bot.sent == []
    assert "not registered" in " ".join(update.message.reply_texts()).lower()
