"""
bot/interfaces/channel.py
──────────────────────────
The `MessagingChannel` abstract base class — every chat backend implements it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class OutboundMessage:
    """A platform-agnostic outbound message.

    Most channels accept a chat / phone identifier and Markdown-style text.
    Channel-specific extras (Telegram parse_mode, WhatsApp template name,
    SMS sender id) belong in `extra` so they round-trip without polluting
    the shared interface.
    """
    recipient_id: str          # Telegram user id, WhatsApp phone, etc. (str for portability)
    text: str
    reply_to_message_id: str | None = None
    extra: dict | None = None


class MessagingChannel(ABC):
    """Outbound messaging contract."""

    name: str = "abstract"

    @abstractmethod
    async def send(self, message: OutboundMessage) -> dict:
        """Deliver one message. Return the provider's success payload.

        Implementations should raise `RuntimeError` on a hard failure so the
        web layer can surface a 500 to the therapist's browser. Best-effort
        side channels (echo into the therapist's bot chat) should swallow
        their own errors and log instead.
        """
        ...

    async def send_text(
        self,
        recipient_id: str | int,
        text: str,
        reply_to_message_id: str | int | None = None,
    ) -> dict:
        """Convenience wrapper — most callers don't need the dataclass."""
        return await self.send(OutboundMessage(
            recipient_id=str(recipient_id),
            text=text,
            reply_to_message_id=str(reply_to_message_id) if reply_to_message_id else None,
        ))
