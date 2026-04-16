"""
web/services/cache_service.py
──────────────────────────────
Redis caching helpers for the web layer.

Key schema (mirroring TECHNICAL_DECISIONS.md):
  zenflow:gcal:events:{tid}:{start}:{end}   — 10-min TTL — Google Calendar events
  zenflow:apts:all                           — 30-s TTL  — All appointments list
"""
import asyncio
import json
import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)


async def prefetch_calendar(therapist_id: str) -> None:
    """Pre-fetch the next 2 weeks of Google Calendar events into Redis (10-min TTL).

    Called as a BackgroundTask after every login so the schedule page loads instantly.
    """
    from web.gcal import GCalClient, is_authenticated, token_file_for
    if not is_authenticated(therapist_id):
        return
    try:
        today = date.today()
        start = today.isoformat() + "T00:00:00Z"
        end = (today + timedelta(weeks=2)).isoformat() + "T23:59:59Z"
        cache_key = f"zenflow:gcal:events:{therapist_id}:{start}:{end}"

        from bot.redis_client import get_async_redis
        r = get_async_redis()
        if await r.exists(cache_key):
            return  # already warm

        client = await asyncio.to_thread(GCalClient.load, token_file_for(therapist_id))
        events = await asyncio.to_thread(client.get_events, start, end)
        await r.set(cache_key, json.dumps(events, default=str), ex=600)
        logger.info(f"[{therapist_id}] Calendar pre-fetched ({len(events)} events)")
    except Exception as e:
        logger.debug(f"Calendar pre-fetch skipped for {therapist_id}: {e}")


async def purge_calendar(therapist_id: str) -> None:
    """Delete all Google Calendar cache keys for a therapist (called on logout)."""
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = [k async for k in r.scan_iter(f"zenflow:gcal:events:{therapist_id}:*")]
        if keys:
            await r.delete(*keys)
            logger.info(f"[{therapist_id}] Purged {len(keys)} calendar cache key(s) on logout")
    except Exception as e:
        logger.debug(f"Calendar cache purge skipped: {e}")


async def invalidate_appointments() -> None:
    """Clear the appointments list cache so next read is fresh."""
    try:
        from bot.redis_client import get_async_redis
        await get_async_redis().delete("zenflow:apts:all")
    except Exception:
        pass


async def get_relay_count() -> int:
    """Return the number of patients currently in an active relay session."""
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = await r.keys("zenflow:relay:active:*")
        return len(keys)
    except Exception:
        return 0
