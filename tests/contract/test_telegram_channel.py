"""Telegram against the channel conformance suite (plan 7.1), both ways it is reached:

* over HTTP (`TelegramChannel(token=…)`: the web process and queued jobs);
* through the bot application's own managed client (`TelegramChannel(bot=…)`: the relay, B14).

Both run against the same offline Bot API (`tests/telegram_fake.py`), so the real request
encoding and error handling are exercised.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from telegram import Bot

from bot.interfaces.channel import ChannelAdapter, ChannelError
from bot.interfaces.telegram_channel import TelegramChannel
from tests.contract.channel_conformance import (
    ChannelConformance,
    ChannelHarness,
    Delivered,
    Edited,
    Failure,
)
from tests.telegram_fake import ApiCall, FakeBotApi

pytestmark = [
    pytest.mark.contract,
    # the offline request class trips PTB's notice about its future upload timeout default
    pytest.mark.filterwarnings("ignore:.*write_timeout.*:telegram.warnings.PTBDeprecationWarning"),
]

TOKEN = "123456:conformance-token-AAAAAAAA"
SECRET = "webhook_secret-0123456789"
USER = "900000123"
MEDIA_METHODS = {"sendPhoto": "image", "sendDocument": "document", "sendAudio": "audio"}
MEDIA_FIELDS = {"image": "photo", "document": "document", "audio": "audio"}


def _buttons(params: dict[str, Any]) -> list[list[tuple[str, str]]] | None:
    markup = params.get("reply_markup")
    if not markup:
        return None
    return [[(b["text"], b["callback_data"]) for b in row] for row in markup["inline_keyboard"]]


def _reply_to(params: dict[str, Any]) -> str | None:
    ref = params.get("reply_parameters")
    return str(ref["message_id"]) if ref else None


class TelegramHarness(ChannelHarness):
    recipient = USER
    secrets = (TOKEN, SECRET, TOKEN.split(":", 1)[1])

    def __init__(self, api: FakeBotApi, adapter: TelegramChannel) -> None:
        self.api = api
        self.adapter = adapter

    # ── what the provider saw ──
    def delivered(self) -> list[Delivered]:
        out = []
        for call in self.api.api_calls:
            p = call.params
            if call.method == "sendMessage":
                out.append(
                    Delivered(
                        recipient=str(p["chat_id"]),
                        text=p["text"],
                        markdown=bool(p.get("parse_mode")),
                        buttons=_buttons(p),
                        reply_to=_reply_to(p),
                    )
                )
            elif call.method in MEDIA_METHODS:
                kind = MEDIA_METHODS[call.method]
                field = MEDIA_FIELDS[kind]
                upload = call.files.get(field)
                out.append(
                    Delivered(
                        recipient=str(p["chat_id"]),
                        text=p.get("caption"),
                        markdown=bool(p.get("parse_mode")),
                        media_kind=kind,
                        media_url=p.get(field) if upload is None else None,
                        media_data=upload[1] if upload else None,
                        filename=upload[0] if upload else None,
                    )
                )
        return out

    def edited(self) -> list[Edited]:
        return [
            Edited(
                str(c.params["chat_id"]),
                str(c.params["message_id"]),
                c.params["text"],
                _buttons(c.params),
            )
            for c in self.api.of("editMessageText")
        ]

    def typing(self) -> list[str]:
        return [str(c.params["chat_id"]) for c in self.api.of("sendChatAction")]

    def fail_next(self, failure: Failure) -> None:
        if failure == "blocked":
            self.api.fail_next("Forbidden: bot was blocked by the user", status=403)
        elif failure == "rate_limited":
            self.api.fail_next("Too Many Requests: retry after 7", status=429, retry_after=7)
        else:
            self.api.down = True

    # ── provider payloads ──
    @staticmethod
    def _message(user: str, message_id: str = "10", **extra: Any) -> dict[str, Any]:
        uid = int(user)
        return {
            "update_id": 5000,
            "message": {
                "message_id": int(message_id),
                "date": 1_772_366_400,  # 2026-03-01T12:00:00Z
                "chat": {"id": uid, "type": "private", "first_name": "Dana"},
                "from": {"id": uid, "is_bot": False, "first_name": "Dana", "last_name": "Levi"},
                **extra,
            },
        }

    def inbound_text(self, user: str, text: str, message_id: str) -> Any:
        return self._message(user, message_id, text=text)

    def inbound_reply(self, user: str, text: str, reply_to: str) -> Any:
        original = {
            "message_id": int(reply_to),
            "date": 1_772_366_000,
            "chat": {"id": int(user), "type": "private"},
        }
        return self._message(user, text=text, reply_to_message=original)

    def inbound_button(self, user: str, data: str) -> Any:
        uid = int(user)
        return {
            "update_id": 5001,
            "callback_query": {
                "id": "cbq-1",
                "from": {"id": uid, "is_bot": False, "first_name": "Dana"},
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": 33,
                    "date": 1_772_366_000,
                    "chat": {"id": uid, "type": "private"},
                },
            },
        }

    def inbound_media(self, user: str, kind: str, caption: str | None) -> Any:
        bodies: dict[str, dict[str, Any]] = {
            "image": {
                "photo": [
                    {"file_id": "small", "width": 90, "height": 90},
                    {"file_id": "big", "width": 800, "height": 800},
                ]
            },
            "audio": {"voice": {"file_id": "voice-1", "duration": 3, "mime_type": "audio/ogg"}},
            "document": {
                "document": {
                    "file_id": "doc-1",
                    "file_name": "scan.pdf",
                    "mime_type": "application/pdf",
                }
            },
        }
        body = dict(bodies[kind])
        if caption is not None:
            body["caption"] = caption
        return self._message(user, **body)

    def ignored_payloads(self) -> list[Any]:
        group = self._message(USER, text="hi")
        group["message"]["chat"] = {"id": -100123, "type": "supergroup", "title": "g"}
        edited = {"update_id": 7, "edited_message": self._message(USER, text="edit")["message"]}
        from_bot = self._message(USER, text="hi")
        from_bot["message"]["from"]["is_bot"] = True
        no_sender = self._message(USER, text="hi")
        del no_sender["message"]["from"]
        empty = self._message(USER)  # a service message: no text, no media
        return [
            group,
            edited,
            from_bot,
            no_sender,
            empty,
            {"update_id": 8, "my_chat_member": {}},
            {"update_id": 9, "message": "not a dict"},
            {"update_id": 10, "callback_query": {"id": "x", "from": {"id": 1}}},  # no data
        ]

    # ── webhook authenticity ──
    def signed(self, body: bytes) -> dict[str, str]:
        return {"X-Telegram-Bot-Api-Secret-Token": SECRET}

    def forged(self, body: bytes) -> list[dict[str, str]]:
        return [
            {"X-Telegram-Bot-Api-Secret-Token": ""},
            {"X-Telegram-Bot-Api-Secret-Token": SECRET + "x"},
            {"X-Telegram-Bot-Api-Secret-Token": SECRET[:-1]},
            {"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            {"X-Other-Header": SECRET},
        ]

    def unconfigured(self) -> ChannelAdapter:
        return TelegramChannel(token=TOKEN, webhook_secret="")


class TestTelegramHttpConformance(ChannelConformance):
    @pytest.fixture
    def harness(self) -> ChannelHarness:
        api = FakeBotApi()
        return TelegramHarness(
            api, TelegramChannel(token=TOKEN, webhook_secret=SECRET, transport=api.transport())
        )


class TestTelegramBotClientConformance(ChannelConformance):
    @pytest.fixture
    async def harness(self) -> AsyncIterator[ChannelHarness]:  # type: ignore[override]
        api = FakeBotApi()
        bot = Bot(TOKEN, request=api.request(), get_updates_request=api.request())
        await bot.initialize()
        api.api_calls.clear()  # initialize() calls getMe
        try:
            yield TelegramHarness(api, TelegramChannel(bot=bot, webhook_secret=SECRET))
        finally:
            await bot.shutdown()


# ── Telegram specifics ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def api() -> FakeBotApi:
    return FakeBotApi()


@pytest.fixture
def channel(api: FakeBotApi) -> TelegramChannel:
    return TelegramChannel(token=TOKEN, webhook_secret=SECRET, transport=api.transport())


def _only(api: FakeBotApi) -> ApiCall:
    (call,) = api.api_calls
    return call


async def test_the_wire_format(api: FakeBotApi, channel: TelegramChannel) -> None:
    await channel.send_text(USER, "*hi*", markdown=True, reply_to="12")
    assert _only(api).params == {
        "chat_id": int(USER),
        "text": "*hi*",
        "parse_mode": "Markdown",
        "reply_parameters": {"message_id": 12, "allow_sending_without_reply": True},
    }


async def test_plain_text_sends_no_parse_mode(api: FakeBotApi, channel: TelegramChannel) -> None:
    await channel.send_text(USER, "a_b")
    assert _only(api).params == {"chat_id": int(USER), "text": "a_b"}


async def test_buttons_are_an_inline_keyboard(api: FakeBotApi, channel: TelegramChannel) -> None:
    await channel.send_buttons(USER, "Pick", [[("A", "a"), ("B", "b")]])
    assert _only(api).params["reply_markup"] == {
        "inline_keyboard": [
            [{"text": "A", "callback_data": "a"}, {"text": "B", "callback_data": "b"}]
        ]
    }


def test_the_limits_are_telegrams() -> None:
    ch = TelegramChannel(token=TOKEN)
    assert (ch.max_text_len, ch.max_button_data_len, ch.max_buttons) == (4096, 64, 100)


async def test_text_length_counts_utf16_units(api: FakeBotApi, channel: TelegramChannel) -> None:
    """Telegram measures in UTF-16 code units: an emoji counts twice."""
    with pytest.raises(ChannelError):
        await channel.send_text(USER, "🙂" * 2049)  # 4098 units
    await channel.send_text(USER, "🙂" * 2048)
    assert len(api.api_calls) == 1


async def test_a_negative_chat_id_stays_numeric(api: FakeBotApi, channel: TelegramChannel) -> None:
    await channel.send_text("-100200", "x")
    assert _only(api).params["chat_id"] == -100200


async def test_a_username_recipient_is_passed_through(
    api: FakeBotApi, channel: TelegramChannel
) -> None:
    await channel.send_text("@clinic_channel", "x")
    assert _only(api).params["chat_id"] == "@clinic_channel"


async def test_a_bad_request_is_permanent_and_keeps_the_reason(
    api: FakeBotApi, channel: TelegramChannel
) -> None:
    api.fail_next("Bad Request: chat not found", status=400)
    with pytest.raises(ChannelError) as err:
        await channel.send_text(USER, "x")
    assert err.value.permanent and str(err.value) == "Bad Request: chat not found"
    assert err.value.code == "400"


async def test_a_server_error_is_retryable(api: FakeBotApi, channel: TelegramChannel) -> None:
    api.fail_next("Internal Server Error", status=502)
    with pytest.raises(ChannelError) as err:
        await channel.send_text(USER, "x")
    assert not err.value.permanent


async def test_bot_info(api: FakeBotApi, channel: TelegramChannel) -> None:
    assert (await channel.bot_info() or {}).get("username") == "zf_bot"
    api.fail_next("Unauthorized", status=401)
    assert await channel.bot_info() is None
    api.down = True
    assert await channel.bot_info() is None


async def test_the_managed_client_is_used_as_is(api: FakeBotApi) -> None:
    """The relay borrows the running application's client (B14): no second HTTP pool."""
    bot = Bot(TOKEN, request=api.request(), get_updates_request=api.request())
    await bot.initialize()
    ch = TelegramChannel(bot=bot)
    await ch.send_buttons(USER, "hi", [[("End", "therapist_end")]])
    call = api.of("sendMessage")[0]
    assert call.params["reply_markup"] == {
        "inline_keyboard": [[{"text": "End", "callback_data": "therapist_end"}]]
    }
    await bot.shutdown()


def test_a_channel_needs_a_token_or_a_client() -> None:
    with pytest.raises(ValueError):
        TelegramChannel()


def test_parse_inbound_details() -> None:
    ch = TelegramChannel(token=TOKEN)
    h = TelegramHarness(FakeBotApi(), ch)
    msg = ch.parse_inbound(h.inbound_text(USER, "hello", "10"))
    assert msg is not None
    assert msg.received_at == "2026-03-01T12:00:00Z"
    assert msg.display_name == "Dana Levi"
    image = ch.parse_inbound(h.inbound_media(USER, "image", None))
    assert image is not None and image.media[0].file_id == "big", "the largest size"
    doc = ch.parse_inbound(h.inbound_media(USER, "document", None))
    assert doc is not None and doc.media[0].mime_type == "application/pdf"
    tap = ch.parse_inbound(h.inbound_button(USER, "x"))
    assert tap is not None and tap.message_id == "33" and tap.raw["callback_query"]["id"] == "cbq-1"


def test_api_telegram_org_is_named_in_one_module_only() -> None:
    """Every Telegram call goes through the adapter (plan 7.1)."""
    root = Path(__file__).resolve().parents[2]
    hits = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for folder in ("bot", "web", "zenflow", "startup")
        for p in (root / folder).rglob("*.py")
        if "api.telegram.org" in p.read_text(encoding="utf-8")
    )
    assert hits == ["bot/interfaces/telegram_channel.py"]
