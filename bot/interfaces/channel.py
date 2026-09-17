"""
bot/interfaces/channel.py
──────────────────────────
The channel contract (plan 7.1): `ChannelAdapter` is what every chat backend implements —
outbound (text, buttons, media, edits, typing) and inbound (webhook parsing + verification).

Every adapter must pass `tests/contract/channel_conformance.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

#: rows of (label, data) — data comes back in `InboundMessage.button_data` when tapped
Buttons = list[list[tuple[str, str]]]

MEDIA_KINDS = ("image", "audio", "video", "document")


class ChannelError(RuntimeError):
    """A message could not be delivered.

    `permanent` — retrying the same message will not help (blocked, unknown recipient, invalid
    content). `retry_after` — seconds the provider asked us to wait. The text is the provider's
    reason (safe for the delivery log: never a token or secret).
    """

    def __init__(
        self,
        message: str,
        *,
        permanent: bool = False,
        retry_after: float | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after
        self.code = code


@dataclass
class OutboundMessage:
    """A platform-agnostic outbound message.

    `extra` carries optional behaviour: `buttons` (see `Buttons`) and `markdown` (default True,
    the historical behaviour of this entry point).
    """

    recipient_id: str  # Telegram user id, WhatsApp phone, etc. (str for portability)
    text: str
    reply_to_message_id: str | None = None
    extra: dict[str, Any] | None = None


@dataclass(frozen=True)
class OutboundMedia:
    """A file to send: by `url` (https, fetched by the provider) or by `data` with a filename."""

    kind: str  # one of MEDIA_KINDS
    url: str | None = None
    data: bytes | None = None
    filename: str | None = None
    mime_type: str | None = None
    caption: str | None = None


@dataclass(frozen=True)
class Template:
    """A pre-approved message, for channels that only allow those outside a session window."""

    name: str
    language: str = "en"
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class SentMessage:
    channel: str
    recipient_id: str
    message_id: str | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


@dataclass(frozen=True)
class InboundMedia:
    kind: str  # one of MEDIA_KINDS
    file_id: str  # the provider's handle — download it through the provider, never trust a URL
    mime_type: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    """One message or button tap from a patient, whatever the channel."""

    channel: str
    external_user_id: str
    text: str | None
    received_at: str  # canonical UTC instant (zenflow.clock)
    message_id: str | None = None
    reply_to: str | None = None
    button_data: str | None = None
    media: tuple[InboundMedia, ...] = ()
    display_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def kind(self) -> str:
        if self.button_data is not None:
            return "button"
        return "media" if self.media else "text"


def header(headers: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (HTTP header names are case-insensitive)."""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


class Scrubber:
    """Removes this channel's own secrets from any text that leaves it."""

    def __init__(self, secrets: tuple[str, ...]) -> None:
        self._secrets = tuple(s for s in secrets if s)

    def __call__(self, text: str) -> str:
        from zenflow.logging import redact

        for secret in self._secrets:
            text = text.replace(secret, "***")
        return redact(text)


class ChannelAdapter(ABC):
    """Outbound + inbound messaging contract."""

    name: str = "abstract"
    max_text_len: int = 4096
    max_caption_len: int = 1024
    max_button_data_len: int = 64  # bytes
    max_buttons: int = 100
    #: can a sent message be replaced? (WhatsApp cannot; Telegram can)
    supports_edit: bool = True
    #: hours after a patient's last message during which free-form text is allowed;
    #: None = no such window. Outside it, only `send_template` gets through.
    session_window_hours: int | None = None

    # ── outbound ──
    @abstractmethod
    async def send_text(
        self,
        recipient_id: str | int,
        text: str,
        *,
        reply_to: str | int | None = None,
        markdown: bool = False,
    ) -> SentMessage: ...

    @abstractmethod
    async def send_buttons(
        self,
        recipient_id: str | int,
        text: str,
        buttons: Buttons,
        *,
        markdown: bool = False,
    ) -> SentMessage: ...

    @abstractmethod
    async def send_media(self, recipient_id: str | int, media: OutboundMedia) -> SentMessage: ...

    @abstractmethod
    async def edit_message(
        self,
        recipient_id: str | int,
        message_id: str | int,
        text: str,
        *,
        buttons: Buttons | None = None,
        markdown: bool = False,
    ) -> SentMessage:
        """Replace a sent message's text; `buttons=None` removes its buttons."""

    @abstractmethod
    async def set_typing(self, recipient_id: str | int) -> None:
        """Show a typing indicator. Best effort: never raises."""

    async def send_template(self, recipient_id: str | int, template: Template) -> SentMessage:
        """Send a pre-approved template. Channels without templates refuse, permanently."""
        raise ChannelError(
            f"{self.name} has no message templates", permanent=True, code="unsupported"
        )

    # ── inbound ──
    @abstractmethod
    def parse_inbound(self, payload: Any) -> InboundMessage | None:
        """A webhook payload → a message, or None for anything unsupported. Never raises."""

    @abstractmethod
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        """Is this request really from the provider? No secret configured → False."""

    # ── shared behaviour ──
    async def send(self, message: OutboundMessage) -> SentMessage:
        """The generic entry point: buttons via `extra["buttons"]`, Markdown unless told not to."""
        extra = message.extra or {}
        markdown = bool(extra.get("markdown", True))
        buttons = extra.get("buttons")
        if buttons:
            return await self.send_buttons(
                message.recipient_id, message.text, buttons, markdown=markdown
            )
        return await self.send_text(
            message.recipient_id,
            message.text,
            reply_to=message.reply_to_message_id,
            markdown=markdown,
        )

    def text_length(self, text: str) -> int:
        return len(text)

    def check_text(self, text: str, *, allow_empty: bool = False) -> None:
        if not allow_empty and not (text or "").strip():
            raise ChannelError("message text is empty", permanent=True, code="empty")
        if self.text_length(text or "") > self.max_text_len:
            raise ChannelError(
                f"message text is longer than {self.max_text_len} characters",
                permanent=True,
                code="too_long",
            )

    def check_buttons(self, buttons: Buttons) -> None:
        flat = [b for row in buttons or [] for b in row]
        if not flat:
            raise ChannelError("no buttons given", permanent=True, code="no_buttons")
        if len(flat) > self.max_buttons:
            raise ChannelError(
                f"more than {self.max_buttons} buttons", permanent=True, code="too_many_buttons"
            )
        for label, data in flat:
            if not (label or "").strip():
                raise ChannelError("a button has no label", permanent=True, code="button_label")
            if not data or len(data.encode("utf-8")) > self.max_button_data_len:
                raise ChannelError(
                    f"button data must be 1–{self.max_button_data_len} bytes",
                    permanent=True,
                    code="button_data",
                )

    def check_media(self, media: OutboundMedia) -> None:
        def refuse(reason: str) -> ChannelError:
            return ChannelError(reason, permanent=True, code="media")

        if media.kind not in MEDIA_KINDS:
            raise refuse(f"unsupported media kind {media.kind!r}")
        if (media.url is None) == (media.data is None):
            raise refuse("media needs exactly one of url or data")
        if media.url is not None and not media.url.lower().startswith("https://"):
            raise refuse("media urls must be https")
        if media.data is not None and not media.filename:
            raise refuse("an uploaded file needs a file name")
        if media.caption and self.text_length(media.caption) > self.max_caption_len:
            raise refuse(f"a caption is limited to {self.max_caption_len} characters")


#: the outbound-only name used before Phase 7.1
MessagingChannel = ChannelAdapter
