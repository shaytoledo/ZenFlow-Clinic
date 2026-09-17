"""
bot/interfaces/whatsapp_channel.py
───────────────────────────────────
WhatsApp implementation of `ChannelAdapter` (plan 7.4), on Meta's Cloud API (ADR-30).

Off until `ZF_CHANNEL_WHATSAPP=1`, and the only module that talks to graph.facebook.com.

What WhatsApp makes different from Telegram, and how this adapter answers it:

* **A 24-hour window.** Free-form text may only be sent while the patient's last message is less
  than 24 h old. Outside it, only pre-approved templates arrive, so `send_text` refuses with
  `outside_session_window` and the caller sends a `Template` instead. The window is tracked from
  inbound messages (`remember_inbound`, Redis, best effort: an unknown window is treated as open
  so a real patient message is never withheld on a cache miss).
* **Buttons are limited.** Up to 3 become reply buttons, up to 10 a list. More options than that
  — or a label longer than WhatsApp's 20 characters — is sent as a numbered text message,
  because every check-in step accepts a typed answer (6.2). Refusing would leave the patient with
  a question they cannot answer.
* **Nothing can be edited.** `supports_edit` is False, so the conformance suite checks the
  refusal instead of an edit.
* **Typing needs a message to answer.** The indicator is tied to marking an inbound message as
  read; with none remembered it is a no-op (it is best effort anyway).

Sent and received payloads are documented in `docs/WHATSAPP.md`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx

from zenflow import clock

from .channel import (
    Buttons,
    ChannelAdapter,
    ChannelError,
    InboundMedia,
    InboundMessage,
    OutboundMedia,
    Scrubber,
    SentMessage,
    Template,
    header,
)

logger = logging.getLogger(__name__)

API_BASE = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v23.0"
SIGNATURE_HEADER = "X-Hub-Signature-256"  # noqa: S105  # nosec B105
SESSION_WINDOW_HOURS = 24
#: Cloud API limits
MAX_TEXT = 4096
MAX_CAPTION = 1024
MAX_BODY = 1024  # interactive message body
MAX_BUTTON_TITLE = 20
MAX_BUTTON_ID = 256
MAX_REPLY_BUTTONS = 3
MAX_LIST_ROWS = 10
#: more options than a list can hold: send them as numbered text (6.2 accepts typed answers)
MAX_BUTTONS = 24
_DIGITS = re.compile(r"\D+")
#: media kind → (payload key, Cloud API accepts a link)
_MEDIA_KEYS = {"image": "image", "audio": "audio", "video": "video", "document": "document"}
#: answers that retrying will not fix
_PERMANENT_STATUS = {400, 401, 403, 404}
#: "Message failed to send because more than 24 hours have passed…"
_OUTSIDE_WINDOW_CODES = {131047, 131026, 470}


class _Api:
    """The Cloud API calls this adapter makes. Tests replace the transport, not this class."""

    def __init__(
        self,
        phone_number_id: str,
        token: str,
        api_version: str,
        transport: httpx.AsyncBaseTransport | None,
        scrub: Any,
    ) -> None:
        self.phone_number_id = phone_number_id
        self._token = token
        self._version = api_version
        self._transport = transport
        self._scrub = scrub

    @property
    def messages_url(self) -> str:
        return f"{API_BASE}/{self._version}/{self.phone_number_id}/messages"

    @property
    def media_url(self) -> str:
        return f"{API_BASE}/{self._version}/{self.phone_number_id}/media"

    async def post(
        self, url: str, payload: dict[str, Any] | None = None, files: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self._token or not self.phone_number_id:
            raise ChannelError("WhatsApp is not configured", permanent=True, code="not_configured")
        headers = {"Authorization": f"Bearer {self._token}"}
        transport = self._transport if self._transport is not None else _transport
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
                if files:
                    resp = await client.post(url, data=payload or {}, files=files, headers=headers)
                else:
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as e:
            # `from None`: an httpx error can carry the URL, and the URL carries the token.
            raise ChannelError(
                f"WhatsApp unreachable ({type(e).__name__})", code="network"
            ) from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400 or not isinstance(body, dict) or "error" in body:
            raise _api_error(resp.status_code, body, self._scrub)
        return body


_TIMEOUT = httpx.Timeout(15.0)
#: tests swap in an offline Cloud API here; None = the real network
_transport: httpx.AsyncBaseTransport | None = None


def _api_error(status: int, body: Any, scrub: Any) -> ChannelError:
    error = (body or {}).get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    message = scrub(str(error.get("message") or f"WhatsApp answered HTTP {status}"))
    code = error.get("code")
    if isinstance(code, int) and code in _OUTSIDE_WINDOW_CODES:
        return ChannelError(message, permanent=True, code="outside_session_window")
    if status == 429:
        return ChannelError(message, retry_after=60.0, code="429")
    return ChannelError(message, permanent=status in _PERMANENT_STATUS, code=str(code or status))


def _recipient(recipient_id: str | int) -> str:
    """WhatsApp wants an E.164 number without the plus."""
    digits = _DIGITS.sub("", str(recipient_id))
    if not digits:
        raise ChannelError(
            f"{recipient_id!r} is not a phone number", permanent=True, code="recipient"
        )
    return digits


def _instant(timestamp: Any) -> str:
    try:
        return clock.to_iso(datetime.fromtimestamp(int(timestamp), UTC))
    except (TypeError, ValueError):
        return clock.iso_now()


class WhatsAppChannel(ChannelAdapter):
    name = "whatsapp"
    max_text_len = MAX_TEXT
    max_caption_len = MAX_CAPTION
    max_button_data_len = MAX_BUTTON_ID
    max_buttons = MAX_BUTTONS
    supports_edit = False  # the Cloud API cannot replace a sent message
    session_window_hours = SESSION_WINDOW_HOURS

    def __init__(
        self,
        phone_number_id: str | None = None,
        token: str | None = None,
        *,
        app_secret: str | None = None,
        api_version: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        from zenflow.settings import get_settings

        s = get_settings()
        self._app_secret = s.whatsapp_app_secret if app_secret is None else app_secret
        phone_number_id = s.whatsapp_phone_number_id if phone_number_id is None else phone_number_id
        token = s.whatsapp_token if token is None else token
        scrub = Scrubber((token or "", self._app_secret or ""))
        self._api = _Api(
            phone_number_id or "",
            token or "",
            api_version or s.whatsapp_api_version or DEFAULT_API_VERSION,
            transport,
            scrub,
        )

    # ── outbound ──
    async def send_text(
        self,
        recipient_id: str | int,
        text: str,
        *,
        reply_to: str | int | None = None,
        markdown: bool = False,
    ) -> SentMessage:
        self.check_text(text)
        await self._require_open_window(recipient_id)
        payload = self._envelope(recipient_id, "text")
        payload["text"] = {"body": text, "preview_url": False}
        if reply_to is not None:
            payload["context"] = {"message_id": str(reply_to)}
        return await self._send(recipient_id, payload)

    async def send_buttons(
        self,
        recipient_id: str | int,
        text: str,
        buttons: Buttons,
        *,
        markdown: bool = False,
    ) -> SentMessage:
        self.check_text(text)
        self.check_buttons(buttons)
        flat = [b for row in buttons for b in row]
        too_long = any(len(label) > MAX_BUTTON_TITLE for label, _d in flat)
        if len(text) > MAX_BODY:
            raise ChannelError(
                f"a message with buttons is limited to {MAX_BODY} characters",
                permanent=True,
                code="too_long",
            )
        await self._require_open_window(recipient_id)
        if too_long or len(flat) > MAX_LIST_ROWS:
            # More options than WhatsApp shows, or a label it would cut off ("Much better"
            # fits, "A little worse than before" does not): ask in words. Every check-in step
            # accepts a typed answer (Phase 6.2), so the conversation continues either way.
            numbered = "\n".join(f"{i}. {label}" for i, (label, _d) in enumerate(flat, start=1))
            return await self.send_text(recipient_id, f"{text}\n\n{numbered}")
        if len(flat) <= MAX_REPLY_BUTTONS:
            payload = self._interactive(recipient_id, text, _reply_buttons(flat))
        else:
            payload = self._interactive(recipient_id, text, _list_rows(flat))
        return await self._send(recipient_id, payload)

    async def send_media(self, recipient_id: str | int, media: OutboundMedia) -> SentMessage:
        self.check_media(media)
        await self._require_open_window(recipient_id)
        key = _MEDIA_KEYS[media.kind]
        body: dict[str, Any] = {}
        if media.url is not None:
            body["link"] = media.url
        else:
            body["id"] = await self._upload(media)
        if media.caption and media.kind in ("image", "video", "document"):
            body["caption"] = media.caption
        if media.kind == "document" and media.filename:
            body["filename"] = media.filename
        payload = self._envelope(recipient_id, key)
        payload[key] = body
        return await self._send(recipient_id, payload)

    async def edit_message(
        self,
        recipient_id: str | int,
        message_id: str | int,
        text: str,
        *,
        buttons: Buttons | None = None,
        markdown: bool = False,
    ) -> SentMessage:
        raise ChannelError(
            "WhatsApp cannot replace a sent message", permanent=True, code="unsupported"
        )

    async def set_typing(self, recipient_id: str | int) -> None:
        """Mark the patient's last message read and show the indicator. Best effort."""
        try:
            message_id = await _last_inbound_message(_recipient(recipient_id))
            if not message_id:
                return
            await self._api.post(
                self._api.messages_url,
                {
                    "messaging_product": "whatsapp",
                    "status": "read",
                    "message_id": message_id,
                    "typing_indicator": {"type": "text"},
                },
            )
        except Exception as e:
            logger.debug("typing indicator not shown: %s", e)

    async def send_template(self, recipient_id: str | int, template: Template) -> SentMessage:
        """A pre-approved template — the only thing that reaches a closed session window."""
        if not template.name:
            raise ChannelError("a template needs a name", permanent=True, code="template")
        payload = self._envelope(recipient_id, "template")
        body: dict[str, Any] = {
            "name": template.name,
            "language": {"code": template.language or "en"},
        }
        if template.params:
            body["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": str(p)} for p in template.params],
                }
            ]
        payload["template"] = body
        return await self._send(recipient_id, payload)

    # ── inbound ──
    def parse_inbound(self, payload: Any) -> InboundMessage | None:
        try:
            return self._parse(payload)
        except Exception as e:
            logger.debug("unparseable WhatsApp payload ignored: %s", type(e).__name__)
            return None

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Meta signs every delivery with the app secret (sha256 over the raw body)."""
        secret = self._app_secret
        given = header(headers, SIGNATURE_HEADER) or ""
        if not secret or not given.startswith("sha256="):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(given[7:].strip(), expected)

    def verify_handshake(self, mode: str, token: str, challenge: str) -> str | None:
        """Meta's subscription handshake: echo the challenge when the verify token matches."""
        from zenflow.settings import get_settings

        expected = get_settings().whatsapp_verify_token
        if mode == "subscribe" and expected and hmac.compare_digest(token or "", expected):
            return challenge
        return None

    def statuses(self, payload: Any) -> list[dict[str, Any]]:
        """Delivery receipts in a webhook payload: (message id, status, recipient, at)."""
        out: list[dict[str, Any]] = []
        for value in _values(payload):
            for status in value.get("statuses") or []:
                if not isinstance(status, dict):
                    continue
                out.append(
                    {
                        "message_id": str(status.get("id") or ""),
                        "status": str(status.get("status") or ""),
                        "recipient": str(status.get("recipient_id") or ""),
                        "at": _instant(status.get("timestamp")),
                        "error": _status_error(status),
                    }
                )
        return out

    # ── helpers ──
    def _envelope(self, recipient_id: str | int, kind: str) -> dict[str, Any]:
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _recipient(recipient_id),
            "type": kind,
        }

    def _interactive(
        self, recipient_id: str | int, text: str, action: dict[str, Any]
    ) -> dict[str, Any]:
        payload = self._envelope(recipient_id, "interactive")
        payload["interactive"] = {
            "type": action.pop("_type"),
            "body": {"text": text},
            "action": action,
        }
        return payload

    async def _send(self, recipient_id: str | int, payload: dict[str, Any]) -> SentMessage:
        body = await self._api.post(self._api.messages_url, payload)
        messages = body.get("messages") or [{}]
        message_id = str(messages[0].get("id") or "") or None
        return SentMessage(
            channel=self.name,
            recipient_id=str(recipient_id).strip(),
            message_id=message_id,
            raw=body,
        )

    async def _upload(self, media: OutboundMedia) -> str:
        """Bytes have to be uploaded first; the Cloud API sends them by id."""
        files = {
            "file": (
                media.filename or "file",
                media.data or b"",
                media.mime_type or "application/octet-stream",
            )
        }
        body = await self._api.post(
            self._api.media_url, {"messaging_product": "whatsapp"}, files=files
        )
        media_id = str(body.get("id") or "")
        if not media_id:
            raise ChannelError("WhatsApp did not return a media id", code="media")
        return media_id

    async def _require_open_window(self, recipient_id: str | int) -> None:
        """Free-form messages only reach a patient who wrote within the last 24 h."""
        try:
            last = await _last_inbound_at(_recipient(recipient_id))
        except ChannelError:
            raise
        except Exception:  # an unknown window is treated as open: WhatsApp refuses it anyway
            return
        if last is None:
            return
        if clock.now_utc() - last > _window():
            raise ChannelError(
                "the 24-hour service window is closed — send a template",
                permanent=True,
                code="outside_session_window",
            )

    def _parse(self, payload: Any) -> InboundMessage | None:
        for value in _values(payload):
            messages = value.get("messages")
            if not isinstance(messages, list) or not messages:
                continue
            message = messages[0]
            if not isinstance(message, dict) or not message.get("from"):
                continue
            kind = str(message.get("type") or "")
            text, button_data, media = _content(message, kind)
            if text is None and button_data is None and not media:
                continue
            context = message.get("context")
            return InboundMessage(
                channel=self.name,
                external_user_id=str(message["from"]),
                text=text,
                received_at=_instant(message.get("timestamp")),
                message_id=str(message.get("id") or "") or None,
                reply_to=(
                    str(context.get("id"))
                    if isinstance(context, dict) and context.get("id")
                    else None
                ),
                button_data=button_data,
                media=media,
                display_name=_profile_name(value, str(message["from"])),
                raw=payload if isinstance(payload, dict) else {},
            )
        return None


def _window() -> Any:
    from datetime import timedelta

    return timedelta(hours=SESSION_WINDOW_HOURS)


def _values(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    out = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        for change in entry.get("changes") or []:
            value = change.get("value") if isinstance(change, dict) else None
            if isinstance(value, dict):
                out.append(value)
    return out


def _content(
    message: dict[str, Any], kind: str
) -> tuple[str | None, str | None, tuple[InboundMedia, ...]]:
    if kind == "text":
        body = (message.get("text") or {}).get("body")
        return (body if isinstance(body, str) else None), None, ()
    if kind == "interactive":
        interactive = message.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        data = reply.get("id")
        return None, (str(data) if data else None), ()
    if kind == "button":  # a template's quick reply
        button = message.get("button") or {}
        return None, (str(button.get("payload")) if button.get("payload") else None), ()
    if kind in _MEDIA_KEYS:
        item = message.get(kind) or {}
        if not item.get("id"):
            return None, None, ()
        caption = item.get("caption")
        media = (
            InboundMedia(
                "audio" if kind == "audio" else kind, str(item["id"]), item.get("mime_type")
            ),
        )
        return (caption if isinstance(caption, str) else None), None, media
    return None, None, ()


def _profile_name(value: dict[str, Any], wa_id: str) -> str | None:
    for contact in value.get("contacts") or []:
        if isinstance(contact, dict) and str(contact.get("wa_id") or "") == wa_id:
            name = (contact.get("profile") or {}).get("name")
            return str(name) if name else None
    return None


def _status_error(status: dict[str, Any]) -> str | None:
    errors = status.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("title") or errors[0].get("message") or "") or None
    return None


def _reply_buttons(flat: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "_type": "button",
        "buttons": [
            {"type": "reply", "reply": {"id": data, "title": label}} for label, data in flat
        ],
    }


def _list_rows(flat: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "_type": "list",
        "button": "Choose",
        "sections": [
            {
                "title": "Options",
                "rows": [{"id": data, "title": label} for label, data in flat],
            }
        ],
    }


# ── the session window (Redis, best effort) ──
def window_key(wa_id: str) -> str:
    return f"zenflow:whatsapp:inbound:{wa_id}"


async def remember_inbound(wa_id: str, message_id: str, at: str | None = None) -> None:
    """Record a patient's message: it opens their 24-hour window and answers typing."""
    from bot.redis_client import get_async_redis

    payload = json.dumps({"id": message_id, "at": at or clock.iso_now()})
    await get_async_redis().set(window_key(wa_id), payload, ex=SESSION_WINDOW_HOURS * 3600 + 3600)


async def _remembered(wa_id: str) -> dict[str, Any] | None:
    from bot.redis_client import get_async_redis

    raw = await get_async_redis().get(window_key(wa_id))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


async def _last_inbound_at(wa_id: str) -> datetime | None:
    remembered = await _remembered(wa_id)
    if not remembered or not remembered.get("at"):
        return None
    try:
        return clock.parse_iso(str(remembered["at"]))
    except ValueError:
        return None


async def _last_inbound_message(wa_id: str) -> str | None:
    remembered = await _remembered(wa_id)
    return str(remembered["id"]) if remembered and remembered.get("id") else None
