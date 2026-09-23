"""
bot/services/flood.py
──────────────────────
Per-Telegram-user flood control for the bots (Phase 9.5 part 3).

The plan asks for per-user limits on three Telegram surfaces: **activation-code entry** (a stranger
guessing a pending registration code on the therapist bot), the **patient→therapist relay**
(spamming a therapist), and **intake answers** (pinning the shared Ollama box). All three are the
same shape — too many messages from one Telegram id in a minute — so they share one budget
(`ZF_BOT_FLOOD_PER_MINUTE`, default 20; 0 disables) and the booking API's fixed-window limiter.

`too_fast(kind, user_id)` counts one message and returns True when this user is over budget for that
surface. Like the rest of the limiter it is **fail-open**: a Redis error counts as "not too fast",
because a cache blip must not stop a patient reaching their therapist.
"""

from __future__ import annotations

from web.services import rate_limit
from zenflow.settings import get_settings


def per_minute() -> int:
    """Messages one Telegram user may send per minute on a bot surface; 0 = no limit."""
    return int(get_settings().flags.bot_flood_per_minute)


async def too_fast(kind: str, user_id: int) -> bool:
    """Count one message from `user_id` on surface `kind` (relay/intake/activation); True if over
    budget. Fail-open — a limiter error is never treated as flooding."""
    retry_after = await rate_limit.hit(f"bot:{kind}:{user_id}", per_minute())
    return retry_after is not None
