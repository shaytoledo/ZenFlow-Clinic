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

from .channel import MessagingChannel, OutboundMessage

logger = logging.getLogger(__name__)


class TelegramChannel(MessagingChannel):
    """Sends messages via the patient bot token."""
    name = "telegram"

    async def send(self, message: OutboundMessage) -> dict:
        from web.services.telegram_service import send_to_patient
        parse_mode = (message.extra or {}).get("parse_mode", "Markdown")
        return await send_to_patient(
            patient_id=int(message.recipient_id),
            text=message.text,
            parse_mode=parse_mode,
        )


class TelegramTherapistChannel(MessagingChannel):
    """Sends messages via the *therapist* bot token (relay channel)."""
    name = "telegram_therapist"

    async def send(self, message: OutboundMessage) -> dict:
        from web.services.telegram_service import send_via_therapist_bot
        parse_mode = (message.extra or {}).get("parse_mode", "Markdown")
        return await send_via_therapist_bot(
            patient_id=int(message.recipient_id),
            text=message.text,
            parse_mode=parse_mode,
        )
