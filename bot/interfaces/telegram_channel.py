"""
bot/interfaces/telegram_channel.py
───────────────────────────────────
Telegram implementation of `ChannelAdapter` (plan 7.1) — the ONLY module that talks to the
Telegram Bot API.

Two ways in, one behaviour (both pass `tests/contract/channel_conformance.py`):

* `TelegramChannel(token=…)` — HTTPS to api.telegram.org (the web process and queued jobs);
* `TelegramChannel(bot=…)`   — a running python-telegram-bot application's own client (the
  relay): initialised, rate-limited and shut down by the application (BOT_AUDIT B14).

Replies *inside* a Telegram conversation (`update.message.reply_text`, `query.edit_message_text`)
belong to the Telegram driver in `bot/patient_bot` and `bot/therapist_bot`; everything the system
initiates goes through here.
"""

from __future__ import annotations

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
    SentMessage,
    header,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
# a header name, not a secret
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105  # nosec B105
_TIMEOUT = httpx.Timeout(10.0)
#: tests swap in an offline Bot API here (tests/conftest.py); None = the real network
_transport: httpx.AsyncBaseTransport | None = None

#: media kind → (Bot API method, file field)
_MEDIA = {
    "image": ("sendPhoto", "photo"),
    "audio": ("sendAudio", "audio"),
    "video": ("sendVideo", "video"),
    "document": ("sendDocument", "document"),
}
_PTB_METHODS = {
    "sendMessage": "send_message",
    "editMessageText": "edit_message_text",
    "sendChatAction": "send_chat_action",
    "sendPhoto": "send_photo",
    "sendAudio": "send_audio",
    "sendVideo": "send_video",
    "sendDocument": "send_document",
    "getMe": "get_me",
}
#: answers that retrying the same request will not fix (429 is rate limiting, 5xx transient)
_PERMANENT = {400, 401, 403, 404}
_NUMERIC_ID = re.compile(r"-?\d+")


class _Scrubber:
    """Removes this channel's own secrets from any text that leaves it."""

    def __init__(self, secrets: tuple[str, ...]) -> None:
        self._secrets = tuple(s for s in secrets if s)

    def __call__(self, text: str) -> str:
        from zenflow.logging import redact

        for secret in self._secrets:
            text = text.replace(secret, "***")
        return redact(text)


class _HttpApi:
    def __init__(
        self, token: str, transport: httpx.AsyncBaseTransport | None, scrub: _Scrubber
    ) -> None:
        self._token = token
        self._transport = transport
        self._scrub = scrub

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        if not self._token:
            raise ChannelError(
                "Telegram bot token is not configured", permanent=True, code="not_configured"
            )
        url = f"{API_BASE}/bot{self._token}/{method}"
        transport = self._transport or _transport
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
                if files:
                    form = {
                        k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()
                    }
                    resp = await client.post(url, data=form, files=files)
                else:
                    resp = await client.post(url, json=params)
        except httpx.HTTPError as e:
            # `from None`: an httpx error can carry the URL, and the URL carries the token.
            raise ChannelError(
                f"Telegram unreachable ({type(e).__name__})", code="network"
            ) from None
        try:
            body = resp.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not body.get("ok"):
            raise _api_error(resp.status_code, body, self._scrub)
        return body.get("result")


class _PtbApi:
    """The same calls through a python-telegram-bot `Bot` (or a stand-in with its methods)."""

    def __init__(self, bot: Any, scrub: _Scrubber) -> None:
        self.bot = bot
        self._scrub = scrub

    async def call(
        self,
        method: str,
        params: dict[str, Any],
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> Any:
        from telegram import InlineKeyboardMarkup, InputFile, ReplyParameters
        from telegram.error import TelegramError

        kwargs = dict(params)
        if "reply_markup" in kwargs:
            kwargs["reply_markup"] = InlineKeyboardMarkup.de_json(kwargs["reply_markup"], self.bot)
        if "reply_parameters" in kwargs:
            kwargs["reply_parameters"] = ReplyParameters.de_json(
                kwargs["reply_parameters"], self.bot
            )
        for name, (filename, data, _mime) in (files or {}).items():
            kwargs[name] = InputFile(data, filename=filename)
        try:
            result = await getattr(self.bot, _PTB_METHODS[method])(**kwargs)
        except TelegramError as e:
            raise _ptb_error(e, self._scrub) from None
        except Exception as e:  # a stand-in client, or anything PTB did not wrap
            raise ChannelError(self._scrub(f"{type(e).__name__}: {e}"), code="client") from None
        if isinstance(result, bool):
            return result
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return {"message_id": getattr(result, "message_id", None)}


def _api_error(status: int, body: Any, scrub: _Scrubber) -> ChannelError:
    body = body if isinstance(body, dict) else {}
    try:
        code = int(body.get("error_code") or status)
    except (TypeError, ValueError):
        code = status
    description = scrub(str(body.get("description") or f"Telegram answered HTTP {status}"))
    parameters = body.get("parameters")
    retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
    if retry_after:
        return ChannelError(description, retry_after=float(retry_after), code=str(code))
    return ChannelError(description, permanent=code in _PERMANENT, code=str(code))


def _ptb_error(e: Exception, scrub: _Scrubber) -> ChannelError:
    from telegram import error as tg

    message = scrub(str(e) or type(e).__name__)
    if isinstance(e, tg.RetryAfter):
        wait = e.retry_after
        seconds = wait.total_seconds() if hasattr(wait, "total_seconds") else float(wait)
        return ChannelError(message, retry_after=seconds, code="429")
    if isinstance(e, tg.Forbidden):
        return ChannelError(message, permanent=True, code="403")
    if isinstance(e, tg.InvalidToken):
        return ChannelError(message, permanent=True, code="401")
    if isinstance(e, tg.BadRequest | tg.ChatMigrated):  # BadRequest is a NetworkError in PTB
        return ChannelError(message, permanent=True, code="400")
    if isinstance(e, tg.NetworkError):
        return ChannelError(message, code="network")
    return ChannelError(message, code="telegram")


def _chat(recipient_id: str | int) -> int | str:
    value = str(recipient_id).strip()
    return int(value) if _NUMERIC_ID.fullmatch(value) else value


def _keyboard(buttons: Buttons) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in buttons
        ]
    }


def inline_keyboard(buttons: Buttons | None) -> dict[str, Any] | None:
    """`[[(label, callback_data), …], …]` → Telegram's `reply_markup`, or None without buttons."""
    return _keyboard(buttons) if buttons else None


def _id(value: Any) -> str | None:
    return None if value is None or isinstance(value, bool) else str(int(value))


def _instant(epoch: Any) -> str:
    if isinstance(epoch, int | float) and not isinstance(epoch, bool) and epoch > 0:
        return clock.to_iso(datetime.fromtimestamp(epoch, UTC))
    return clock.iso_now()


def _sender(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("is_bot") or value.get("id") is None:
        return None
    return value


def _display_name(sender: dict[str, Any]) -> str | None:
    parts = [sender.get("first_name"), sender.get("last_name")]
    name = " ".join(str(p) for p in parts if p)
    return name or sender.get("username") or None


def _media(msg: dict[str, Any]) -> tuple[InboundMedia, ...]:
    found: list[InboundMedia] = []
    photos = msg.get("photo")
    if isinstance(photos, list) and photos and isinstance(photos[-1], dict):
        found.append(InboundMedia("image", str(photos[-1]["file_id"])))  # sizes ascend
    for key, kind in (
        ("voice", "audio"),
        ("audio", "audio"),
        ("video", "video"),
        ("video_note", "video"),
        ("document", "document"),
    ):
        item = msg.get(key)
        if isinstance(item, dict) and item.get("file_id"):
            found.append(InboundMedia(kind, str(item["file_id"]), item.get("mime_type")))
    return tuple(found)


class TelegramChannel(ChannelAdapter):
    name = "telegram"
    max_text_len = 4096
    max_caption_len = 1024
    max_button_data_len = 64
    max_buttons = 100

    def __init__(
        self,
        token: str | None = None,
        *,
        bot: Any = None,
        webhook_secret: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if token is None and bot is None:
            raise ValueError("TelegramChannel needs a bot token or a running bot client")
        self._webhook_secret = webhook_secret
        scrub = _Scrubber((token or "", str(getattr(bot, "token", "") or ""), webhook_secret or ""))
        self._api: _HttpApi | _PtbApi = (
            _PtbApi(bot, scrub) if bot is not None else _HttpApi(token or "", transport, scrub)
        )

    @property
    def bot(self) -> Any:
        """The managed client this channel borrows, if any."""
        return self._api.bot if isinstance(self._api, _PtbApi) else None

    def text_length(self, text: str) -> int:
        return len(text.encode("utf-16-le")) // 2  # Telegram counts UTF-16 code units

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
        params = self._message(recipient_id, text, markdown)
        if reply_to is not None:
            params["reply_parameters"] = {
                "message_id": int(reply_to),
                "allow_sending_without_reply": True,
            }
        return self._sent(recipient_id, await self._api.call("sendMessage", params))

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
        params = self._message(recipient_id, text, markdown)
        params["reply_markup"] = _keyboard(buttons)
        return self._sent(recipient_id, await self._api.call("sendMessage", params))

    async def send_media(self, recipient_id: str | int, media: OutboundMedia) -> SentMessage:
        self.check_media(media)
        method, field = _MEDIA[media.kind]
        params: dict[str, Any] = {"chat_id": _chat(recipient_id)}
        if media.caption:
            params["caption"] = media.caption
        files = None
        if media.url is not None:
            params[field] = media.url
        elif media.data is not None:  # check_media: exactly one of url / data, with a filename
            files = {
                field: (
                    media.filename or "file",
                    media.data,
                    media.mime_type or "application/octet-stream",
                )
            }
        return self._sent(recipient_id, await self._api.call(method, params, files))

    async def edit_message(
        self,
        recipient_id: str | int,
        message_id: str | int,
        text: str,
        *,
        buttons: Buttons | None = None,
        markdown: bool = False,
    ) -> SentMessage:
        self.check_text(text)
        params = self._message(recipient_id, text, markdown)
        params["message_id"] = int(message_id)
        if buttons:  # Telegram drops the keyboard when an edit carries none
            self.check_buttons(buttons)
            params["reply_markup"] = _keyboard(buttons)
        result = await self._api.call("editMessageText", params)
        if not isinstance(result, dict):  # `True` for inline messages
            result = {"message_id": int(message_id)}
        return self._sent(recipient_id, result)

    async def set_typing(self, recipient_id: str | int) -> None:
        try:
            await self._api.call(
                "sendChatAction", {"chat_id": _chat(recipient_id), "action": "typing"}
            )
        except Exception as e:
            logger.debug("typing indicator not shown: %s", e)

    async def bot_info(self) -> dict[str, Any] | None:
        """getMe, or None when the bot cannot be reached or the token is refused."""
        ok, info = await self._get_me()
        return info if ok and isinstance(info, dict) else None

    async def check(self) -> tuple[bool, str]:
        """(reachable, "@username" or the reason) — for the status page."""
        ok, info = await self._get_me()
        if ok and isinstance(info, dict):
            return True, f"@{info.get('username', '')}"
        return False, str(info)

    async def _get_me(self) -> tuple[bool, Any]:
        try:
            return True, await self._api.call("getMe", {})
        except ChannelError as e:
            return False, "Unreachable" if e.code == "network" else str(e)
        except Exception as e:
            logger.warning("Telegram getMe failed: %s", type(e).__name__)
            return False, "Unreachable"

    # ── inbound ──
    def parse_inbound(self, payload: Any) -> InboundMessage | None:
        try:
            return self._parse(payload)
        except Exception as e:  # a malformed update must never break the caller
            logger.debug("unparseable Telegram update ignored: %s", type(e).__name__)
            return None

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        secret = self._webhook_secret
        if secret is None:
            from zenflow.settings import get_settings

            secret = get_settings().telegram_webhook_secret
        given = header(headers, SECRET_HEADER)
        if not secret or not given:
            return False
        return hmac.compare_digest(given.encode(), secret.encode())

    # ── helpers ──
    def _message(self, recipient_id: str | int, text: str, markdown: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": _chat(recipient_id), "text": text}
        if markdown:  # plain by default: user-typed words must not be parsed (B2)
            params["parse_mode"] = "Markdown"
        return params

    def _sent(self, recipient_id: str | int, result: Any) -> SentMessage:
        raw = result if isinstance(result, dict) else {"result": result}
        return SentMessage(
            channel=self.name,
            recipient_id=str(recipient_id).strip(),
            message_id=_id(raw.get("message_id")),
            raw=raw,
        )

    def _parse(self, payload: Any) -> InboundMessage | None:
        if not isinstance(payload, dict):
            return None
        if "callback_query" in payload:
            return self._parse_button(payload)
        msg = payload.get("message")
        if not isinstance(msg, dict):
            return None  # edits, member updates, channel posts: not patient messages
        chat = msg.get("chat")
        sender = _sender(msg.get("from"))
        if not isinstance(chat, dict) or chat.get("type") != "private" or sender is None:
            return None
        text = msg.get("text")
        if not isinstance(text, str):
            caption = msg.get("caption")
            text = caption if isinstance(caption, str) else None
        media = _media(msg)
        if text is None and not media:
            return None
        reply = msg.get("reply_to_message")
        return InboundMessage(
            channel=self.name,
            external_user_id=str(int(sender["id"])),
            text=text,
            received_at=_instant(msg.get("date")),
            message_id=_id(msg.get("message_id")),
            reply_to=_id(reply.get("message_id")) if isinstance(reply, dict) else None,
            media=media,
            display_name=_display_name(sender),
            raw=payload,
        )

    def _parse_button(self, payload: dict[str, Any]) -> InboundMessage | None:
        query = payload["callback_query"]
        if not isinstance(query, dict):
            return None
        data = query.get("data")
        sender = _sender(query.get("from"))
        if not isinstance(data, str) or not data or sender is None:
            return None
        msg = query.get("message")
        if isinstance(msg, dict):
            chat = msg.get("chat")
            if not isinstance(chat, dict) or chat.get("type") != "private":
                return None
        return InboundMessage(
            channel=self.name,
            external_user_id=str(int(sender["id"])),
            text=None,
            received_at=clock.iso_now(),  # Telegram does not date a tap
            message_id=_id(msg.get("message_id")) if isinstance(msg, dict) else None,
            button_data=data,
            display_name=_display_name(sender),
            raw=payload,
        )
