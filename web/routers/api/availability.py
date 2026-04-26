"""
web/routers/api/availability.py
──────────────────────────────────
Calendar and availability slot endpoints.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import _get_session_therapist_id
from web.gcal import GCalClient, is_authenticated, token_file_for
from web.repositories import appointment_repo
from web.services import availability_service
from web.services.cache_service import (
    get_events_cached,
    prefetch_calendar,
    purge_calendar,
    set_events_cached,
)


SESSION_DURATION_MIN = 60


def _appointments_as_fc_events(tid: str, start: str, end: str) -> list[dict]:
    """Render appointments as FullCalendar events when Google Calendar isn't
    already representing them.

    Dedup rule: if a real Google Calendar event id exists on the row
    (anything that isn't empty or a `local_*` sentinel), skip — the GCal
    fetch will surface it. We only render rows that have NO GCal mirror,
    so the schedule never shows the same booking twice.
    """
    from datetime import datetime, timedelta
    start_d = (start or "")[:10] or "1970-01-01"
    end_d   = (end   or "")[:10] or "2999-12-31"
    rows = appointment_repo.list_in_date_range(tid, start_d, end_d)
    out = []
    for a in rows:
        gcal_id = (a.get("gcal_apt_event_id") or "").strip()
        if gcal_id and not gcal_id.startswith("local_"):
            continue  # GCal already returns this event — don't duplicate
        try:
            s = datetime.fromisoformat(f"{a['date']}T{a['time']}:00")
            e = s + timedelta(minutes=SESSION_DURATION_MIN)
        except Exception:
            continue
        date_slug = a["date"]
        time_slug = a["time"].replace(":", "-")
        out.append({
            "id":    f"appt-{a['id']}",
            "title": f"🌿 ZenFlow — {a['patient_name']}",
            "start": s.isoformat(),
            "end":   e.isoformat(),
            # Match the muted earth-tone of regular GCal events so manual
            # bookings don't stand out in a different colour.
            "backgroundColor": "#A8907E",
            "borderColor":     "#8B6F5C",
            "textColor":       "#FFFFFF",
            "url":             f"/treatment/{a['patient_id']}/{date_slug}/{time_slug}",
            "extendedProps": {
                "type":         "appointment",
                "appointment_id": a["id"],
                "patient_id":   a["patient_id"],
                "patient_name": a["patient_name"],
                "source":       a.get("source") or "telegram",
            },
        })
    return out

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class SlotIn(BaseModel):
    start: str
    end: str


@router.get("/calendars")
async def get_calendars(request: Request):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        raise HTTPException(status_code=401, detail="Not authenticated with Google Calendar")
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        return JSONResponse(await asyncio.to_thread(client.get_calendar_list))
    except Exception as e:
        logger.error(f"get_calendars error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/availability/free-slots")
async def get_free_slots(request: Request, weeks: int = 4):
    """Return available booking slots for the next `weeks` weeks.

    Shape: `{"YYYY-MM-DD": ["09:00", "10:00", ...], ...}` — only dates with
    at least one free hour are included. Drives the manual-booking modal so
    the therapist can only pick times they previously marked as available.
    """
    tid = _get_session_therapist_id(request)
    if not tid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Lazy import — pulls in langchain otherwise
    from bot.patient_bot.services.availability import (
        get_available_days,
        get_available_hours,
    )
    out: dict[str, list[str]] = {}
    try:
        for w in range(max(1, min(weeks, 12))):
            days = await get_available_days(week_offset=w, therapist_id=tid)
            for d in days:
                hours = await get_available_hours(d, therapist_id=tid)
                if hours:
                    out[d.isoformat()] = hours
    except Exception as e:
        logger.error(f"get_free_slots error: {e}")
    return JSONResponse(out)


@router.post("/calendars/refresh")
async def refresh_calendar(request: Request):
    """Force-refetch the next 14 days from Google: purge cache then re-warm.

    Wired to the Refresh button on the schedule page so the therapist can pull
    fresh data without waiting for the 10-minute TTL or having to re-login.
    """
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        # Local-only mode has no remote source to refetch — still acknowledge.
        return JSONResponse({"ok": True, "source": "local"})
    try:
        await purge_calendar(tid)
        await prefetch_calendar(tid)
        return JSONResponse({"ok": True, "source": "google"})
    except Exception as e:
        logger.error(f"refresh_calendar error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/events")
async def get_events(request: Request, start: str, end: str):
    tid = _get_session_therapist_id(request)
    # Appointments from the local DB are always merged in — even when Google
    # is connected — so manual bookings show on the calendar without needing
    # a Google Calendar event.
    appts = await asyncio.to_thread(_appointments_as_fc_events, tid, start, end)

    if not is_authenticated(tid):
        slots = await asyncio.to_thread(availability_service.list_local, tid)
        return JSONResponse(availability_service.to_fc_events(slots) + appts)

    cached = await get_events_cached(tid, start, end)
    if cached is not None:
        return JSONResponse(cached + appts)

    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        events = await asyncio.to_thread(client.get_events, start, end)
        await set_events_cached(tid, start, end, events)
        return JSONResponse(events + appts)
    except Exception as e:
        logger.error(f"get_events error: {e}")
        return JSONResponse(appts)


@router.post("/availability")
async def create_slot(request: Request, slot: SlotIn):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        fc_event = await asyncio.to_thread(availability_service.add_local, tid, slot.start, slot.end)
        return JSONResponse(fc_event)
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        cal_id = await asyncio.to_thread(client.get_or_create_availability_cal)
        event = await asyncio.to_thread(client.create_availability, cal_id, slot.start, slot.end)
        # Invalidate all cache keys for this therapist (rolling + legacy per-range)
        try:
            from bot.redis_client import get_async_redis
            r = get_async_redis()
            keys = [k async for k in r.scan_iter(f"zenflow:gcal:*:{tid}*")]
            if keys:
                await r.delete(*keys)
        except Exception:
            pass
        # Re-warm in the background so next page load is instant
        asyncio.create_task(prefetch_calendar(tid))
        return JSONResponse(event)
    except Exception as e:
        logger.error(f"create_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/availability/{event_id}")
async def delete_slot(request: Request, event_id: str, calendarId: str = "local"):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        await asyncio.to_thread(availability_service.remove_local, event_id)
        return JSONResponse({"ok": True})
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        await asyncio.to_thread(client.delete_availability, calendarId, event_id)
        # Invalidate cache so next /api/events fetch is fresh
        try:
            from bot.redis_client import get_async_redis
            r = get_async_redis()
            keys = [k async for k in r.scan_iter(f"zenflow:gcal:*:{tid}*")]
            if keys:
                await r.delete(*keys)
        except Exception:
            pass
        asyncio.create_task(prefetch_calendar(tid))
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"delete_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
