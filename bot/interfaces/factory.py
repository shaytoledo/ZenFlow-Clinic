"""
bot/interfaces/factory.py
──────────────────────────
Pick the active `MessagingChannel` implementation.

Today this always returns `TelegramChannel`. To add WhatsApp:
  1. Implement `WhatsAppChannel(MessagingChannel)` in `whatsapp_channel.py`.
  2. Set `MESSAGING_CHANNEL=whatsapp` in `.env`.
  3. The branch below dispatches on that env var.

Callers should NEVER instantiate channels directly — go through this factory
so the swap is one line of config, not a code change in every endpoint.
"""

from __future__ import annotations

from zenflow.settings import get_settings

from .channel import MessagingChannel
from .telegram_channel import TelegramChannel


def get_default_channel() -> MessagingChannel:
    """Return the channel selected by `MESSAGING_CHANNEL` (default: telegram)."""
    s = get_settings()
    name = (s.messaging_channel or "telegram").strip().lower()
    if name == "telegram":
        return TelegramChannel()
    if name == "whatsapp":
        if not s.flags.channel_whatsapp:
            raise ValueError("MESSAGING_CHANNEL=whatsapp requires ZF_CHANNEL_WHATSAPP=1")
        raise NotImplementedError("WhatsApp adapter is delivered in Phase 7.4")
    raise ValueError(f"Unknown MESSAGING_CHANNEL='{name}' — supported: telegram, whatsapp")
