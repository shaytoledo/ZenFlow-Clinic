"""
web/services/cache_service.py
──────────────────────────────
Redis caching helpers for the web layer.

Key schema (mirroring docs/DATA_LAYER.md):
  zenflow:gcal:rolling14d:{tid}        — 10-min TTL — Single rolling 14-day events blob
                                          per therapist; serves any FullCalendar query
                                          in the [today, today+14d] window.
  zenflow:gcal:events:{tid}:{s}:{e}    — 10-min TTL — Legacy fine-grained cache for
                                          requests that fall outside the rolling window.
  zenflow:apts:all                     — 30-s TTL  — All appointments list.
"""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

ROLLING_DAYS = 14
ROLLING_TTL = 600  # 10 minutes

def _rolling_key(therapist_id: str) -> str:
    return f"zenflow:gcal:rolling14d:{therapist_id}"


def _rolling_window() -> tuple[str, str]:
    """Return the (start, end) ISO-Z strings for the current rolling-14d window."""
    today = date.today()
    start = today.isoformat() + "T00:00:00Z"
    end = (today + timedelta(days=ROLLING_DAYS)).isoformat() + "T23:59:59Z"
    return start, end


def _parse_iso(s: str) -> datetime | None:
    """Best-effort parse of an ISO datetime string. Returns None on failure."""
    if not s:
        return None
    try:
        s = s.replace(" ", "+")  # URL-decoded `+` becomes space
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _filter_events(events: list[dict], start: str, end: str) -> list[dict]:
    """Filter a wider events list down to those overlapping [start, end]."""
    sd = _parse_iso(start)
    ed = _parse_iso(end)
    if not sd or not ed:
        return events
    out = []
    for ev in events:
        es = _parse_iso(ev.get("start", ""))
        ee = _parse_iso(ev.get("end", "")) or es
        if not es:
            continue
        # Overlap test
        if es < ed and (ee or es) > sd:
            out.append(ev)
    return out


async def get_events_cached(
    therapist_id: str,
    start: str,
    end: str,
) -> list[dict] | None:
    """Return cached events overlapping [start, end] or None if cache miss.

    Tries the rolling-14d cache first (covers any sub-window in today..today+14d).
    Falls back to a legacy per-range key when the request is outside the window.
    """
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()

        sd = _parse_iso(start)
        ed = _parse_iso(end)
        rolling_start = _parse_iso(_rolling_window()[0])
        rolling_end = _parse_iso(_rolling_window()[1])

        # Inside the rolling window? Use the shared blob.
        if sd and ed and rolling_start and rolling_end and \
                sd >= rolling_start and ed <= rolling_end:
            raw = await r.get(_rolling_key(therapist_id))
            if raw:
                return _filter_events(json.loads(raw), start, end)

        # Outside window — try per-range cache (rarely hit; keeps backward compat)
        legacy_key = f"zenflow:gcal:events:{therapist_id}:{start}:{end}"
        raw = await r.get(legacy_key)
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"get_events_cached miss/error: {e}")
    return None


async def set_events_cached(
    therapist_id: str,
    start: str,
    end: str,
    events: list[dict],
) -> None:
    """Write events to the appropriate cache slot for the requested range."""
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()

        sd = _parse_iso(start)
        ed = _parse_iso(end)
        rolling_start = _parse_iso(_rolling_window()[0])
        rolling_end = _parse_iso(_rolling_window()[1])

        # Inside rolling window: store as the rolling blob (so future sub-windows hit)
        if sd and ed and rolling_start and rolling_end and \
                sd >= rolling_start and ed <= rolling_end:
            # Only widen the cached range — never shrink it
            await r.set(
                _rolling_key(therapist_id),
                json.dumps(events, default=str),
                ex=ROLLING_TTL,
            )
            return

        legacy_key = f"zenflow:gcal:events:{therapist_id}:{start}:{end}"
        await r.set(legacy_key, json.dumps(events, default=str), ex=ROLLING_TTL)
    except Exception as e:
        logger.debug(f"set_events_cached error: {e}")


async def prefetch_calendar(therapist_id: str) -> None:
    """Pre-fetch the next 14 days of Google Calendar events into Redis.

    Called as a fire-and-forget task on every login so the schedule page loads
    instantly. Stores under the rolling-14d key so any FullCalendar sub-range
    request inside the window hits the cache.
    """
    from web.gcal import GCalClient, is_authenticated, token_file_for
    if not is_authenticated(therapist_id):
        return
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        if await r.exists(_rolling_key(therapist_id)):
            return  # already warm

        start, end = _rolling_window()
        client = await asyncio.to_thread(GCalClient.load, token_file_for(therapist_id))
        events = await asyncio.to_thread(client.get_events, start, end)
        await r.set(
            _rolling_key(therapist_id),
            json.dumps(events, default=str),
            ex=ROLLING_TTL,
        )
        logger.info(f"[{therapist_id}] Calendar pre-fetched ({len(events)} events, 14d)")
    except Exception as e:
        logger.debug(f"Calendar pre-fetch skipped for {therapist_id}: {e}")


async def purge_calendar(therapist_id: str) -> None:
    """Delete all Google Calendar cache keys for a therapist (called on logout)."""
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = [k async for k in r.scan_iter(f"zenflow:gcal:*:{therapist_id}*")]
        # Also catch the rolling key
        rolling = _rolling_key(therapist_id)
        try:
            if await r.exists(rolling):
                keys.append(rolling)
        except Exception:
            pass
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
