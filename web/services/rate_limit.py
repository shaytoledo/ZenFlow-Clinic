"""
web/services/rate_limit.py
───────────────────────────
A per-caller request budget, shared by every volume limit (Phase 7.3, extended in 9.5).

A fixed one-minute window in Redis: cheap, good enough to keep one caller from flooding a shared
resource, and honest about what it is. `hit(caller, per_minute)` counts one request under an
opaque `caller` key and returns the seconds to wait when that caller is over budget. Callers today:
the booking API (per API key / session), the AI diagnosis endpoints (per therapist) and the public
sign-up form (per source IP). If Redis is unavailable the request is allowed — a monitoring outage
must not stop the clinic working.
"""

from __future__ import annotations

import logging

from zenflow import clock
from zenflow.settings import get_settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


def ai_per_minute() -> int:
    """Diagnosis/point-generation requests a therapist may make per minute; 0 = no limit (9.5)."""
    return int(get_settings().flags.ai_rate_per_minute)


def signup_per_minute() -> int:
    """Public sign-ups allowed per minute per source IP; 0 = no limit (9.5)."""
    return int(get_settings().flags.signup_per_minute)


def window_key(caller: str, minute: int) -> str:
    return f"zenflow:ratelimit:v1:{caller}:{minute}"


async def hit(caller: str, per_minute: int) -> int | None:
    """Count one request. Returns the seconds to wait when the caller is over budget."""
    if per_minute <= 0:  # 0 = no limit
        return None
    now = clock.now_utc()
    minute = int(now.timestamp()) // WINDOW_SECONDS
    try:
        from bot.redis_client import get_async_redis

        redis = get_async_redis()
        used = await redis.incr(window_key(caller, minute))
        if used == 1:
            await redis.expire(window_key(caller, minute), WINDOW_SECONDS * 2)
    except Exception as e:  # never block a booking on the rate limiter
        logger.debug("rate limit not counted (%s): %s", caller, type(e).__name__)
        return None
    if used <= per_minute:
        return None
    return max(1, WINDOW_SECONDS - int(now.timestamp()) % WINDOW_SECONDS)
