"""
bot/interfaces/
────────────────
Channel-agnostic messaging abstractions.

The clinic currently runs on Telegram (`bot/interfaces/telegram_channel.py`),
but every code path that *sends* an outbound message — patient confirmations,
recommendation deliveries, the 24h follow-up prompt — should go through
`MessagingChannel` defined here, not call the Telegram Bot API directly.

This makes adding a WhatsApp (or SMS, web push, …) backend a matter of:
  1. Subclass `MessagingChannel`.
  2. Register it in `get_default_channel()` based on an env flag.
  3. No other code changes.

The patient `ConversationHandler` itself stays Telegram-specific because
python-telegram-bot owns its event loop. To swap chat platforms entirely you
also need a parallel `whatsapp_bot/` driver — but the OUTBOUND helpers
(notifications, recommendations, follow-ups) work without that change.
"""
from .channel import MessagingChannel, OutboundMessage
from .telegram_channel import TelegramChannel
from .factory import get_default_channel

__all__ = [
    "MessagingChannel",
    "OutboundMessage",
    "TelegramChannel",
    "get_default_channel",
]
