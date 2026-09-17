"""
bot/interfaces/
────────────────
Channel-agnostic messaging (plan 7.1).

`ChannelAdapter` (channel.py) is the contract every chat backend implements: outbound text,
buttons, media, edits and typing, plus inbound webhook parsing and verification. Telegram is
`TelegramChannel`, the only module that talks to the Telegram Bot API. Every adapter must pass
the conformance suite in `tests/contract/channel_conformance.py`.

Adding a channel (WhatsApp, 7.4):
  1. Implement `ChannelAdapter` and make the conformance suite green for it.
  2. Return it from `get_channel()` behind its feature flag.

The Telegram conversation handlers (`bot/patient_bot`, `bot/therapist_bot`) stay Telegram's own
driver: python-telegram-bot owns their event loop and their in-conversation replies. Everything
the system initiates — confirmations, recommendations, follow-ups, relay deliveries, web
replies — goes through an adapter.
"""

from .channel import (
    ChannelAdapter,
    ChannelError,
    InboundMedia,
    InboundMessage,
    MessagingChannel,
    OutboundMedia,
    OutboundMessage,
    SentMessage,
    Template,
)
from .factory import get_channel, get_default_channel, get_staff_channel
from .telegram_channel import TelegramChannel
from .whatsapp_channel import WhatsAppChannel

__all__ = [
    "ChannelAdapter",
    "ChannelError",
    "InboundMedia",
    "InboundMessage",
    "MessagingChannel",
    "OutboundMedia",
    "OutboundMessage",
    "SentMessage",
    "TelegramChannel",
    "Template",
    "WhatsAppChannel",
    "get_channel",
    "get_default_channel",
    "get_staff_channel",
]
