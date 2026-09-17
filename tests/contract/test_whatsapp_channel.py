"""WhatsApp against the channel conformance suite (plan 7.4), on a mocked Cloud API.

No credentials, no network: an httpx transport answers like graph.facebook.com, so the real
payload building, error mapping and webhook parsing run. What WhatsApp does differently from
Telegram — the 24-hour window, templates, button limits, no editing — is pinned below the suite.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from freezegun import freeze_time

from bot.interfaces.channel import ChannelAdapter, ChannelError, Template
from bot.interfaces.whatsapp_channel import WhatsAppChannel, remember_inbound
from tests.contract.channel_conformance import (
    ChannelConformance,
    ChannelHarness,
    Delivered,
    Edited,
    Failure,
)

pytestmark = pytest.mark.contract

PHONE_ID = "106540352242922"
TOKEN = "EAAtest-whatsapp-access-token"
APP_SECRET = "whatsapp-app-secret-0123456789"
USER = "972500000001"
NOW = "2026-03-01T12:00:00Z"


class FakeCloudApi:
    """The Cloud API, offline: records each call and answers like Meta does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        self.down = False
        self._failures: list[tuple[int, dict[str, Any]]] = []
        self._next = 100
        #: media id → (filename, bytes), as an upload leaves them on the provider
        self.uploads: dict[str, tuple[str | None, bytes]] = {}

    def fail_next(self, status: int, code: int | None, message: str) -> None:
        body: dict[str, Any] = {"error": {"message": message, "type": "OAuthException"}}
        if code is not None:
            body["error"]["code"] = code
        self._failures.append((status, body))

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [payload for path, payload, _f in self.calls if path.endswith(kind)]

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if self.down:
                raise httpx.ConnectError("connection refused", request=request)
            assert request.url.host == "graph.facebook.com", request.url
            assert request.headers.get("authorization") == f"Bearer {TOKEN}"
            payload, files = _decode(request)
            self.calls.append((request.url.path, payload, files))
            if self._failures:
                status, body = self._failures.pop(0)
                return httpx.Response(status, json=body)
            if request.url.path.endswith("/media"):
                media_id = f"media-{len(self.uploads) + 1}"
                self.uploads[media_id] = files.get("file", (None, b""))
                return httpx.Response(200, json={"id": media_id})
            self._next += 1
            return httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "contacts": [{"input": payload.get("to"), "wa_id": payload.get("to")}],
                    "messages": [{"id": f"wamid.{self._next}"}],
                },
            )

        return httpx.MockTransport(handle)


def _decode(request: httpx.Request) -> tuple[dict[str, Any], dict[str, Any]]:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        return json.loads(request.content), {}
    from tests.telegram_fake import _decode as decode_multipart

    return decode_multipart(request)


class WhatsAppHarness(ChannelHarness):
    recipient = USER
    secrets = (TOKEN, APP_SECRET)
    template = Template(name="appointment_reminder", language="en", params=("12 March", "10:00"))
    marks_markdown = False  # WhatsApp formats inline (*bold*), with no parse mode to set

    def __init__(self, api: FakeCloudApi, adapter: WhatsAppChannel) -> None:
        self.api = api
        self.adapter = adapter

    # ── what the provider saw ──
    def delivered(self) -> list[Delivered]:
        out: list[Delivered] = []
        for path, payload, _files in self.api.calls:
            if not path.endswith("/messages") or payload.get("status") == "read":
                continue
            kind = payload.get("type")
            if kind == "text":
                context = payload.get("context") or {}
                out.append(
                    Delivered(
                        payload["to"],
                        payload["text"]["body"],
                        reply_to=context.get("message_id"),
                    )
                )
            elif kind == "interactive":
                interactive = payload["interactive"]
                out.append(
                    Delivered(
                        payload["to"], interactive["body"]["text"], buttons=_buttons(interactive)
                    )
                )
            elif kind == "template":
                out.append(Delivered(payload["to"], payload["template"]["name"]))
            elif kind in ("image", "audio", "video", "document"):
                body = payload[kind]
                # bytes reach WhatsApp through the media endpoint, and the message names the id
                upload = self.api.uploads.get(str(body.get("id") or ""))
                out.append(
                    Delivered(
                        payload["to"],
                        body.get("caption"),
                        media_kind=kind,
                        media_url=body.get("link"),
                        media_data=upload[1] if upload else None,
                        filename=body.get("filename") or (upload[0] if upload else None),
                    )
                )
        return out

    def edited(self) -> list[Edited]:
        return []  # WhatsApp cannot replace a sent message

    def typing(self) -> list[str]:
        return [
            call[1].get("_recipient", USER)
            for call in self.api.calls
            if call[1].get("typing_indicator")
        ]

    def fail_next(self, failure: Failure) -> None:
        if failure == "blocked":
            self.api.fail_next(400, 131026, "Message undeliverable")
        elif failure == "rate_limited":
            self.api.fail_next(429, 130429, "Rate limit hit")
        else:
            self.api.down = True

    # ── provider payloads ──
    @staticmethod
    def _webhook(message: dict[str, Any]) -> dict[str, Any]:
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": PHONE_ID},
                                "contacts": [{"wa_id": USER, "profile": {"name": "Dana Levi"}}],
                                "messages": [message],
                            },
                        }
                    ],
                }
            ],
        }

    def inbound_text(self, user: str, text: str, message_id: str) -> Any:
        return self._webhook(
            {
                "from": user,
                "id": message_id,
                "timestamp": "1772366400",
                "type": "text",
                "text": {"body": text},
            }
        )

    def inbound_reply(self, user: str, text: str, reply_to: str) -> Any:
        payload = self.inbound_text(user, text, "wamid.reply")
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["context"] = {
            "from": PHONE_ID,
            "id": reply_to,
        }
        return payload

    def inbound_button(self, user: str, data: str) -> Any:
        return self._webhook(
            {
                "from": user,
                "id": "wamid.tap",
                "timestamp": "1772366400",
                "type": "interactive",
                "interactive": {
                    "type": "button_reply",
                    "button_reply": {"id": data, "title": "Yes"},
                },
            }
        )

    def inbound_media(self, user: str, kind: str, caption: str | None) -> Any:
        body: dict[str, Any] = {"id": f"media-{kind}", "mime_type": f"{kind}/ogg"}
        if caption is not None:
            body["caption"] = caption
        return self._webhook(
            {
                "from": user,
                "id": "wamid.media",
                "timestamp": "1772366400",
                "type": kind,
                kind: body,
            }
        )

    def ignored_payloads(self) -> list[Any]:
        no_sender = self.inbound_text(USER, "hi", "wamid.1")
        del no_sender["entry"][0]["changes"][0]["value"]["messages"][0]["from"]
        empty = self._webhook(
            {"from": USER, "id": "wamid.2", "timestamp": "1772366400", "type": "system"}
        )
        statuses = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {"statuses": [{"id": "wamid.1", "status": "delivered"}]},
                        }
                    ],
                }
            ],
        }
        return [no_sender, empty, statuses, {"object": "whatsapp_business_account"}, {"entry": []}]

    # ── webhook authenticity ──
    def signed(self, body: bytes) -> dict[str, str]:
        digest = hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
        return {"X-Hub-Signature-256": f"sha256={digest}"}

    def forged(self, body: bytes) -> list[dict[str, str]]:
        good = self.signed(body)["X-Hub-Signature-256"]
        return [
            {"X-Hub-Signature-256": good[:-1] + ("a" if good[-1] != "a" else "b")},
            {"X-Hub-Signature-256": good.removeprefix("sha256=")},  # no prefix
            {"X-Hub-Signature-256": "sha256=" + hashlib.sha256(body).hexdigest()},  # unkeyed
            {"X-Hub-Signature-256": ""},
            {"X-Other-Header": good},
        ]

    def unconfigured(self) -> ChannelAdapter:
        return WhatsAppChannel(PHONE_ID, TOKEN, app_secret="", transport=self.api.transport())


def _buttons(interactive: dict[str, Any]) -> list[list[tuple[str, str]]]:
    action = interactive["action"]
    if interactive["type"] == "button":
        return [[(b["reply"]["title"], b["reply"]["id"]) for b in action["buttons"]]]
    return [[(row["title"], row["id"]) for row in action["sections"][0]["rows"]]]


@pytest.fixture
def cloud() -> FakeCloudApi:
    return FakeCloudApi()


@pytest.fixture
async def open_window(fake_redis) -> None:
    """The patient wrote a moment ago, so free-form messages are allowed."""
    await remember_inbound(USER, "wamid.inbound", None)


class TestWhatsAppConformance(ChannelConformance):
    # pytest injects the fixtures this override asks for
    @pytest.fixture
    def harness(  # type: ignore[override]
        self, cloud: FakeCloudApi, open_window: None
    ) -> ChannelHarness:
        return WhatsAppHarness(
            cloud,
            WhatsAppChannel(PHONE_ID, TOKEN, app_secret=APP_SECRET, transport=cloud.transport()),
        )


# ── what WhatsApp does differently ───────────────────────────────────────────────────────────
@pytest.fixture
def channel(cloud: FakeCloudApi) -> WhatsAppChannel:
    return WhatsAppChannel(PHONE_ID, TOKEN, app_secret=APP_SECRET, transport=cloud.transport())


async def test_the_wire_format(cloud: FakeCloudApi, channel: WhatsAppChannel, open_window) -> None:
    await channel.send_text(f"+{USER}", "Hello there")
    assert cloud.of("/messages") == [
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": USER,
            "type": "text",
            "text": {"body": "Hello there", "preview_url": False},
        }
    ]


async def test_three_options_are_reply_buttons(
    cloud: FakeCloudApi, channel: WhatsAppChannel, open_window
) -> None:
    await channel.send_buttons(USER, "Did it help?", [[("Yes", "y"), ("No", "n")], [("Skip", "s")]])
    interactive = cloud.of("/messages")[0]["interactive"]
    assert interactive["type"] == "button"
    assert [b["reply"]["title"] for b in interactive["action"]["buttons"]] == ["Yes", "No", "Skip"]


async def test_four_to_ten_options_become_a_list(
    cloud: FakeCloudApi, channel: WhatsAppChannel, open_window
) -> None:
    rows = [[(f"{n}", f"fu:1:1:{n}")] for n in range(6)]
    await channel.send_buttons(USER, "Pain 0–5?", rows)
    interactive = cloud.of("/messages")[0]["interactive"]
    assert interactive["type"] == "list"
    assert len(interactive["action"]["sections"][0]["rows"]) == 6


async def test_more_options_than_a_list_become_numbered_text(
    cloud: FakeCloudApi, channel: WhatsAppChannel, open_window
) -> None:
    """The check-in's 0–10 scale is 11 options; its typed answers accept a number (6.2)."""
    rows = [[(f"{n}", f"fu:1:1:{n}")] for n in range(11)]
    await channel.send_buttons(USER, "Pain 0–10?", rows)
    (payload,) = cloud.of("/messages")
    assert payload["type"] == "text"
    body = payload["text"]["body"]
    assert body.startswith("Pain 0–10?") and "1. 0" in body and "11. 10" in body


async def test_a_long_button_label_is_refused(channel: WhatsAppChannel, open_window) -> None:
    with pytest.raises(ChannelError) as err:
        await channel.send_buttons(USER, "?", [[("x" * 21, "d")]])
    assert err.value.permanent and err.value.code == "button_label"


async def test_media_by_upload_goes_through_the_media_endpoint(
    cloud: FakeCloudApi, channel: WhatsAppChannel, open_window
) -> None:
    from bot.interfaces.channel import OutboundMedia

    await channel.send_media(
        USER,
        OutboundMedia(
            kind="document", data=b"%PDF-1.4", filename="plan.pdf", mime_type="application/pdf"
        ),
    )
    paths = [path for path, _p, _f in cloud.calls]
    assert paths == [f"/v23.0/{PHONE_ID}/media", f"/v23.0/{PHONE_ID}/messages"]
    assert cloud.of("/messages")[0]["document"] == {"id": "media-1", "filename": "plan.pdf"}
    assert cloud.uploads["media-1"] == ("plan.pdf", b"%PDF-1.4")


# ── the 24-hour service window ──
async def test_free_form_text_needs_an_open_window(
    cloud: FakeCloudApi, channel: WhatsAppChannel, fake_redis
) -> None:
    with freeze_time(NOW) as frozen:
        await remember_inbound(USER, "wamid.in")
        await channel.send_text(USER, "inside the window")
        frozen.tick(timedelta(hours=24, minutes=1))
        with pytest.raises(ChannelError) as err:
            await channel.send_text(USER, "too late")
    assert err.value.permanent and err.value.code == "outside_session_window"
    assert len(cloud.of("/messages")) == 1


async def test_a_template_reaches_a_closed_window(
    cloud: FakeCloudApi, channel: WhatsAppChannel, fake_redis
) -> None:
    with freeze_time(NOW) as frozen:
        await remember_inbound(USER, "wamid.in")
        frozen.tick(timedelta(hours=48))
        sent = await channel.send_template(
            USER, Template(name="followup_checkin", language="he", params=("Dana",))
        )
    assert sent.message_id
    assert cloud.of("/messages")[0]["template"] == {
        "name": "followup_checkin",
        "language": {"code": "he"},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": "Dana"}]}],
    }


async def test_an_unknown_window_is_treated_as_open(
    cloud: FakeCloudApi, channel: WhatsAppChannel, fake_redis
) -> None:
    """A cache miss must not withhold a real reply — WhatsApp refuses it if truly closed."""
    await channel.send_text(USER, "hello")
    assert len(cloud.of("/messages")) == 1


async def test_the_providers_own_refusal_is_mapped(
    cloud: FakeCloudApi, channel: WhatsAppChannel, open_window
) -> None:
    cloud.fail_next(400, 131047, "Message failed to send because more than 24 hours have passed")
    with pytest.raises(ChannelError) as err:
        await channel.send_text(USER, "hi")
    assert err.value.permanent and err.value.code == "outside_session_window"


async def test_typing_marks_the_last_message_read(
    cloud: FakeCloudApi, channel: WhatsAppChannel, fake_redis
) -> None:
    await remember_inbound(USER, "wamid.inbound-7")
    await channel.set_typing(USER)
    (payload,) = cloud.of("/messages")
    assert payload == {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": "wamid.inbound-7",
        "typing_indicator": {"type": "text"},
    }


async def test_typing_without_a_message_does_nothing(
    cloud: FakeCloudApi, channel: WhatsAppChannel, fake_redis
) -> None:
    await channel.set_typing(USER)
    assert cloud.calls == []


# ── webhooks ──
def test_the_subscription_handshake(channel: WhatsAppChannel, monkeypatch) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "verify-me")
    S.reset_settings()
    try:
        assert channel.verify_handshake("subscribe", "verify-me", "1158201444") == "1158201444"
        assert channel.verify_handshake("subscribe", "wrong", "1158201444") is None
        assert channel.verify_handshake("unsubscribe", "verify-me", "1158201444") is None
    finally:
        S.reset_settings()
    assert channel.verify_handshake("subscribe", "", "1") is None, "no token configured"


def test_delivery_receipts_are_read(channel: WhatsAppChannel) -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.9",
                                    "status": "failed",
                                    "recipient_id": USER,
                                    "timestamp": "1772366400",
                                    "errors": [{"title": "Message undeliverable"}],
                                }
                            ]
                        },
                    }
                ]
            }
        ],
    }
    assert channel.statuses(payload) == [
        {
            "message_id": "wamid.9",
            "status": "failed",
            "recipient": USER,
            "at": "2026-03-01T12:00:00Z",
            "error": "Message undeliverable",
        }
    ]
    assert channel.statuses({"entry": []}) == []


def test_graph_facebook_is_named_in_one_module_only() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    hits = sorted(
        str(p.relative_to(root)).replace("\\", "/")
        for folder in ("bot", "web", "zenflow", "startup")
        for p in (root / folder).rglob("*.py")
        if "graph.facebook.com" in p.read_text(encoding="utf-8")
    )
    assert hits == ["bot/interfaces/whatsapp_channel.py"]


# ── the flag ──
def test_the_factory_keeps_whatsapp_behind_its_flag(db, monkeypatch) -> None:
    from bot.interfaces import get_channel
    from zenflow import settings as S

    monkeypatch.setenv("ZF_CHANNEL_WHATSAPP", "0")
    S.reset_settings()
    with pytest.raises(ValueError, match="ZF_CHANNEL_WHATSAPP"):
        get_channel("whatsapp")
    monkeypatch.setenv("ZF_CHANNEL_WHATSAPP", "1")
    S.reset_settings()
    try:
        assert get_channel("whatsapp").name == "whatsapp"
    finally:
        S.reset_settings()
