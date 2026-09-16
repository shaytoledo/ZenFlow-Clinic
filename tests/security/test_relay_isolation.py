"""Phase 2.4 — multi-therapist relay isolation (plan 2.4, SF-008).

Therapist B must never read, reply to, or be routed a message that belongs to therapist A:
not via reply-to, not via free typing, not via a stale `current:{therapist}` key, not via a
Telegram message id that happens to be reused, and not through the dashboard once A's chat has
ended.

Two structural defects were behind the old rules:
- `zenflow:relay:msg:{message_id}` was global, but Telegram numbers messages *per chat*. The
  therapist bot's message 50 to A and its message 50 to B shared one key, so the second mapping
  overwrote the first.
- Relay history was keyed by patient only. Once the live session ended, the dashboard guessed
  ownership from "has an appointment with this patient" (SF-008), and a patient who moved from A
  to B carried A's conversation into B's view.
"""

from __future__ import annotations

import json

import pytest

from bot.patient_bot.services import relay
from tests.bot.conftest import FakeBot, FakeMessage

pytestmark = pytest.mark.security

PATIENT_A = 910_000_001  # A's patient
PATIENT_B = 910_000_002  # B's patient
A_TG, B_TG = 710_001, 710_002
PW = "pw-Test-123"


@pytest.fixture
def patient_bot(monkeypatch: pytest.MonkeyPatch) -> FakeBot:
    """The patient application's client, as the therapist bot sees it."""
    import bot.therapist_bot.handlers as th

    bot_ = FakeBot()
    monkeypatch.setattr(th, "_patient_bot", bot_)
    return bot_


@pytest.fixture
def therapists(db, make_therapist):
    from bot import config as botcfg

    a = make_therapist(
        name="Dr A", email="ra@example.com", password=PW, telegram_id=A_TG, therapist_id="t1"
    )
    b = make_therapist(
        name="Dr B", email="rb@example.com", password=PW, telegram_id=B_TG, therapist_id="t2"
    )
    botcfg.reload_therapists()
    return a, b


# ── bot side ──
async def test_a_reused_message_id_routes_each_reply_to_its_own_patient(
    fake_redis, patient_bot, therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    # Telegram numbers messages per chat: both therapists' chats have a message 50.
    relay.save_relay_mapping(50, PATIENT_A, "t1", "A")
    relay.save_relay_mapping(50, PATIENT_B, "t2", "B")

    reply_a = FakeMessage("for A", user_id=A_TG, reply_to_message=FakeMessage("fwd", message_id=50))
    await _handle_relay(reply_a, "t1", "en")
    reply_b = FakeMessage("for B", user_id=B_TG, reply_to_message=FakeMessage("fwd", message_id=50))
    await _handle_relay(reply_b, "t2", "en")

    assert [(m["chat_id"], m["text"].endswith("for A")) for m in patient_bot.sent] == [
        (PATIENT_A, True),
        (PATIENT_B, False),
    ]
    assert patient_bot.sent[1]["text"].endswith("for B")


async def test_free_typing_only_ever_reaches_the_typists_own_patient(
    fake_redis, patient_bot, therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    relay.save_relay_mapping(60, PATIENT_A, "t1", "A")
    relay.save_relay_mapping(61, PATIENT_B, "t2", "B")

    await _handle_relay(FakeMessage("hello", user_id=B_TG), "t2", "en")

    assert [m["chat_id"] for m in patient_bot.sent] == [PATIENT_B]


async def test_a_stale_current_key_pointing_at_another_therapists_patient_is_ignored(
    fake_redis, patient_bot, therapists
) -> None:
    from bot.therapist_bot.handlers import _handle_relay

    relay.save_relay_mapping(70, PATIENT_A, "t1", "A")
    fake_redis.sync.set("zenflow:relay:current:t2", str(PATIENT_A))  # corrupted / left over

    msg = FakeMessage("are you there?", user_id=B_TG)
    await _handle_relay(msg, "t2", "en")

    assert patient_bot.sent == []
    assert "no active" in " ".join(msg.reply_texts()).lower()


async def test_history_is_kept_per_therapist(fake_redis, therapists) -> None:
    """A patient who moves from A to B does not carry A's conversation into B's view."""
    from web.services import telegram_service

    relay.append_history(PATIENT_A, "patient", "told A about my divorce", "t1")
    relay.append_history(PATIENT_A, "patient", "hello B", "t2")

    seen_by_b = await telegram_service.get_relay_messages("t2", PATIENT_A)
    assert [m["text"] for m in seen_by_b] == ["hello B"]
    seen_by_a = await telegram_service.get_relay_messages("t1", PATIENT_A)
    assert [m["text"] for m in seen_by_a] == ["told A about my divorce"]


# ── dashboard side (SF-008) ──
async def test_an_ended_chat_is_not_readable_by_another_therapist_who_treats_the_patient(
    fake_redis, therapists, make_appointment, login_as
) -> None:
    a, b = therapists
    patient = {"patient_id": PATIENT_A, "name": "Shared Patient", "source": "telegram"}
    make_appointment(therapist=b, patient=patient)  # B also treats this patient

    relay.save_relay_mapping(80, PATIENT_A, "t1", "Shared Patient")
    relay.append_history(PATIENT_A, "patient", "private to A", "t1")
    relay.end_relay(PATIENT_A)  # the live session is gone; only history remains

    client_b = await login_as(b)
    resp = await client_b.get(f"/api/messages/history/{PATIENT_A}")
    assert "private to A" not in resp.text
    assert resp.status_code == 404

    client_a = await login_as(a)
    own = await client_a.get(f"/api/messages/history/{PATIENT_A}")
    assert own.status_code == 200, "A still sees their own conversation after it ended"
    assert [m["text"] for m in own.json()["messages"]] == ["private to A"]


async def test_another_therapist_cannot_delete_or_mark_an_ended_chat(
    fake_redis, therapists, make_appointment, login_as
) -> None:
    _, b = therapists
    patient = {"patient_id": PATIENT_A, "name": "Shared Patient", "source": "telegram"}
    make_appointment(therapist=b, patient=patient)
    relay.save_relay_mapping(81, PATIENT_A, "t1", "Shared Patient")
    relay.append_history(PATIENT_A, "patient", "private to A", "t1")
    relay.end_relay(PATIENT_A)

    client_b = await login_as(b)
    assert (await client_b.delete(f"/api/messages/history/{PATIENT_A}")).status_code == 404
    assert (await client_b.post(f"/api/messages/unread/{PATIENT_A}")).status_code == 404
    assert fake_redis.sync.get(f"zenflow:relay:history:t1:{PATIENT_A}") is not None


async def test_web_replies_are_plain_text(fake_redis, fake_telegram, therapists, login_as) -> None:
    """The dashboard's send path had the same Markdown failure as the bot (B2)."""
    a, _ = therapists
    relay.save_relay_mapping(90, PATIENT_A, "t1", "A")

    client_a = await login_as(a)
    resp = await client_a.post(
        "/api/messages/send", json={"patient_id": PATIENT_A, "text": "email me at a_b@c.com"}
    )
    assert resp.status_code == 200, resp.text
    to_patient = [c for c in fake_telegram.calls if c["chat_id"] == PATIENT_A]
    assert to_patient and "a_b@c.com" in to_patient[0]["text"]
    assert not to_patient[0]["parse_mode"], "user text must not be parsed as Markdown"

    stored = json.loads(fake_redis.sync.get(f"zenflow:relay:history:t1:{PATIENT_A}"))
    assert stored[-1]["text"] == "email me at a_b@c.com"
