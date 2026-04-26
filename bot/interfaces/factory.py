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

import os

from .channel import MessagingChannel
from .telegram_channel import TelegramChannel


def get_default_channel() -> MessagingChannel:
    """Return the channel selected by `MESSAGING_CHANNEL` env (default: telegram)."""
    name = (os.getenv("MESSAGING_CHANNEL") or "telegram").strip().lower()
    if name == "telegram":
        return TelegramChannel()
    # Future:
    # if name == "whatsapp":
    #     from .whatsapp_channel import WhatsAppChannel
    #     return WhatsAppChannel()
    raise ValueError(f"Unknown MESSAGING_CHANNEL='{name}' — supported: telegram")
