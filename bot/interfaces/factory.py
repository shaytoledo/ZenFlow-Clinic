"""
bot/interfaces/factory.py
──────────────────────────
Pick a `ChannelAdapter`. Callers never build channels themselves, so switching a channel is
configuration, not a code change in every endpoint.

* `get_channel(name)`     — the patient-facing adapter for one channel ("telegram", "whatsapp");
* `get_default_channel()` — the one selected by `MESSAGING_CHANNEL` (default: telegram);
* `get_staff_channel()`   — therapists: always the therapist bot on Telegram.
"""

from __future__ import annotations

from zenflow.settings import get_settings

from .channel import ChannelAdapter
from .telegram_channel import TelegramChannel

CHANNELS = ("telegram", "whatsapp")


def get_channel(name: str) -> ChannelAdapter:
    s = get_settings()
    name = (name or "").strip().lower()
    if name == "telegram":
        return TelegramChannel(token=s.telegram_token)
    if name == "whatsapp":
        if not s.flags.channel_whatsapp:
            raise ValueError("the WhatsApp channel requires ZF_CHANNEL_WHATSAPP=1")
        from .whatsapp_channel import WhatsAppChannel

        return WhatsAppChannel()
    raise ValueError(f"Unknown channel '{name}' — supported: {', '.join(CHANNELS)}")


def get_default_channel() -> ChannelAdapter:
    """Return the channel selected by `MESSAGING_CHANNEL` (default: telegram)."""
    return get_channel(get_settings().messaging_channel or "telegram")


def get_staff_channel() -> TelegramChannel:
    """The therapist bot — how the system reaches therapists."""
    return TelegramChannel(token=get_settings().therapist_bot_token)
