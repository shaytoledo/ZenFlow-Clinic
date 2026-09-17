"""
bot/interfaces/telegram_channel.py
───────────────────────────────────
Telegram implementation of `MessagingChannel`.

Wraps the existing `web.services.telegram_service` helpers so we don't
duplicate retry/error handling in two places. When a WhatsApp adapter is
added the wrapper pattern is the same — just point at the WhatsApp client.
"""

from __future__ import annotations

import logging
from typing import Any

from .channel import MessagingChannel, OutboundMessage

logger = logging.getLogger(__name__)


class TelegramChannel(MessagingChannel):
    """Sends messages via the patient bot token."""

    name = "telegram"

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        from web.services.telegram_service import send_to_patient

        extra = message.extra or {}
        return await send_to_patient(
            patient_id=int(message.recipient_id),
            text=message.text,
            parse_mode=extra.get("parse_mode", "Markdown"),
            reply_markup=inline_keyboard(extra.get("buttons")),
        )


def inline_keyboard(buttons: list[list[tuple[str, str]]] | None) -> dict[str, Any] | None:
    """`[[(label, callback_data), …], …]` → Telegram's `reply_markup`, or None without buttons."""
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in buttons
        ]
    }


class TelegramTherapistChannel(MessagingChannel):
    """Sends messages via the *therapist* bot token (relay channel)."""

    name = "telegram_therapist"

    async def send(self, message: OutboundMessage) -> dict[str, Any]:
        from web.services.telegram_service import send_via_therapist_bot

        parse_mode = (message.extra or {}).get("parse_mode", "Markdown")
        return await send_via_therapist_bot(
            patient_id=int(message.recipient_id),
            text=message.text,
            parse_mode=parse_mode,
        )
