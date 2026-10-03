"""Owner decision Q6 — photos, voice notes and files reach the other side, safely.

With ZF_RELAY_MEDIA=1 a file sent in a therapist chat is downloaded through the bot that received
it and sent again through the other bot, in memory only. Type and size are checked before
anything is downloaded, and the therapist's reply follows the same routing rule as text
(BOT_AUDIT B1). With the flag off, both sides are told to use text instead (B7).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.patient_bot.services import relay as prelay
from bot.services import media_relay
from bot.states import THERAPIST_RELAY
from tests.bot.conftest import FakeBot, FakeMessage, make_context, make_update

PATIENT = 900_000_301
THERAPIST_TG = 700_031
JPEG = b"\xff\xd8\xff" + b"x" * 2000
VOICE = b"OggS" + b"v" * 3000


@pytest.fixture
def relay_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_RELAY_MEDIA", "1")
    monkeypatch.setenv("ZF_RELAY_MEDIA_MAX_MB", "1")
    reset_settings()


@pytest.fixture
def therapist(make_therapist):
    return make_therapist(name="Dr Q", telegram_id=THERAPIST_TG, therapist_id="t1")


def _photo(file_id: str = "ph-big", size: int | None = len(JPEG)) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(file_id="ph-small", file_size=100),
        SimpleNamespace(file_id=file_id, file_size=size),
    ]


def _document(mime: str, size: int = 500, name: str = "scan.pdf") -> SimpleNamespace:
    return SimpleNamespace(file_id="doc-1", file_size=size, mime_type=mime, file_name=name)


# ── patient → therapist ──
async def test_a_photo_reaches_the_therapist_and_their_reply_can_route_back(
    db, fake_redis, relay_on, therapist, therapist_bot
) -> None:
    from bot.patient_bot.therapist import relay_unsupported_media

    patient_bot = FakeBot()
    patient_bot.serve("ph-big", JPEG)
    update = make_update(None, user_id=PATIENT, full_name="Pat Q", photo=_photo())
    update.message.caption = "the rash today"
    state = await relay_unsupported_media(
        update, make_context({"selected_therapist": "t1"}, bot=patient_bot)
    )

    assert state == THERAPIST_RELAY
    assert patient_bot.downloads == ["ph-big"], "the largest size, downloaded once"
    (sent,) = therapist_bot.sent
    assert sent["method"] == "photo" and sent["chat_id"] == THERAPIST_TG
    assert sent["bytes"] == JPEG and sent["caption"] == "📎 Pat Q:\nthe rash today"
    assert update.message.reply_texts() == ["✅ Sent."]
    route = prelay.get_patient_for_msg(therapist_bot._next_id, "t1")
    assert (
        route is not None and int(route["patient_id"]) == PATIENT
    ), "the therapist can reply to it"


@pytest.mark.parametrize(
    ("message_kw", "reason"),
    [
        ({"document": _document("application/x-msdownload", name="run.exe")}, "type of file"),
        ({"document": _document("application/pdf", size=5 * 1024 * 1024)}, "too large"),
    ],
    ids=["not-an-allowed-type", "declared-too-large"],
)
async def test_refused_before_anything_is_downloaded(
    db, fake_redis, relay_on, therapist, therapist_bot, message_kw, reason
) -> None:
    from bot.patient_bot.therapist import relay_unsupported_media

    patient_bot = FakeBot()
    update = make_update(None, user_id=PATIENT, **message_kw)
    await relay_unsupported_media(
        update, make_context({"selected_therapist": "t1"}, bot=patient_bot)
    )
    assert therapist_bot.sent == [] and getattr(patient_bot, "downloads", []) == []
    assert reason in update.message.reply_texts()[0]


async def test_a_file_larger_than_it_claimed_is_not_sent(
    db, fake_redis, relay_on, therapist, therapist_bot
) -> None:
    from bot.patient_bot.therapist import relay_unsupported_media

    patient_bot = FakeBot()
    patient_bot.serve("ph-big", b"x" * (2 * 1024 * 1024))  # the update said 2 KB
    update = make_update(None, user_id=PATIENT, photo=_photo())
    await relay_unsupported_media(
        update, make_context({"selected_therapist": "t1"}, bot=patient_bot)
    )
    assert therapist_bot.sent == []
    assert "too large" in update.message.reply_texts()[0]


async def test_with_the_flag_off_the_patient_is_asked_for_text(
    db, fake_redis, therapist, therapist_bot
) -> None:
    from bot.patient_bot.therapist import relay_unsupported_media

    patient_bot = FakeBot()
    update = make_update(None, user_id=PATIENT, photo=_photo())
    await relay_unsupported_media(
        update, make_context({"selected_therapist": "t1"}, bot=patient_bot)
    )
    assert therapist_bot.sent == [] and getattr(patient_bot, "downloads", []) == []
    assert "can't send photos" in update.message.reply_texts()[0]


# ── therapist → patient ──
async def test_a_voice_reply_reaches_the_right_patient(
    db, fake_redis, relay_on, therapist, patient_bot
) -> None:
    from bot.therapist_bot.handlers import handle_therapist_media

    prelay.save_relay_mapping(77, PATIENT, "t1", "Pat Q")
    therapist_side = FakeBot()
    therapist_side.serve("voice-1", VOICE)
    relayed = FakeMessage("(forwarded)", message_id=77)
    update = make_update(None, user_id=THERAPIST_TG, full_name="Dr Q", reply_to_message=relayed)
    update.message.voice = SimpleNamespace(
        file_id="voice-1", file_size=len(VOICE), mime_type="audio/ogg"
    )
    await handle_therapist_media(update, make_context(bot=therapist_side))

    (sent,) = patient_bot.sent
    assert sent["method"] == "audio" and sent["chat_id"] == PATIENT and sent["bytes"] == VOICE
    assert sent["caption"].startswith("👨‍⚕️ Dr Q")
    assert update.message.reply_texts() == ["✅ Delivered."]


async def test_a_media_reply_to_an_expired_mapping_goes_nowhere(
    db, fake_redis, relay_on, therapist, patient_bot
) -> None:
    """The same B1 rule as text: never guess the patient."""
    from bot.therapist_bot.handlers import handle_therapist_media

    prelay.save_relay_mapping(80, PATIENT, "t1", "Pat Q")  # a current chat exists…
    therapist_side = FakeBot()
    therapist_side.serve("voice-1", VOICE)
    stale = FakeMessage("(old)", message_id=79)  # …but this reply's mapping is gone
    update = make_update(None, user_id=THERAPIST_TG, reply_to_message=stale)
    update.message.voice = SimpleNamespace(
        file_id="voice-1", file_size=len(VOICE), mime_type="audio/ogg"
    )
    await handle_therapist_media(update, make_context(bot=therapist_side))
    assert patient_bot.sent == [] and therapist_side.downloads == []
    assert "expired" in update.message.reply_texts()[0]


async def test_with_the_flag_off_the_therapist_is_asked_for_text(
    db, fake_redis, therapist, patient_bot
) -> None:
    from bot.therapist_bot.handlers import handle_therapist_media

    update = make_update(None, user_id=THERAPIST_TG, photo=_photo())
    await handle_therapist_media(update, make_context(bot=FakeBot()))
    assert patient_bot.sent == []
    assert "can't be delivered" in update.message.reply_texts()[0]


# ── the parts ──
def test_file_names_lose_their_paths_and_control_characters() -> None:
    doc = _document("application/pdf", name="..\\..\\etc/pass\x00wd<1>.pdf")
    attachment = media_relay.attachment_of(
        SimpleNamespace(
            photo=None, voice=None, audio=None, video=None, video_note=None, document=doc
        )
    )
    assert attachment is not None and attachment.filename == "pass" + "wd1.pdf"


def test_nothing_is_written_to_disk_or_the_database() -> None:
    """The module that touches the bytes has no file or database access at all."""
    from pathlib import Path

    source = Path(media_relay.__file__).read_text(encoding="utf-8")
    for forbidden in ("open(", "write_bytes", "get_db", "Storage", "tempfile"):
        assert forbidden not in source, forbidden
