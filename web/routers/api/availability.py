"""
web/routers/api/availability.py
──────────────────────────────────
Calendar and availability slot endpoints.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import _get_session_therapist_id
from web.gcal import GCalClient, is_authenticated, token_file_for
from web.services import availability_service
from web.services.cache_service import prefetch_calendar

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


@router.get("/events")
async def get_events(request: Request, start: str, end: str):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        slots = await asyncio.to_thread(availability_service.list_local, tid)
        return JSONResponse(availability_service.to_fc_events(slots))

    cache_key = f"zenflow:gcal:events:{tid}:{start}:{end}"
    _r = None
    try:
        from bot.redis_client import get_async_redis
        _r = get_async_redis()
        cached = await _r.get(cache_key)
        if cached:
            return JSONResponse(json.loads(cached))
    except Exception:
        _r = None

    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        events = await asyncio.to_thread(client.get_events, start, end)
        if _r:
            try:
                await _r.set(cache_key, json.dumps(events, default=str), ex=600)
            except Exception:
                pass
        return JSONResponse(events)
    except Exception as e:
        logger.error(f"get_events error: {e}")
        return JSONResponse([])


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
        # Invalidate cache
        try:
            from bot.redis_client import get_async_redis
            r = get_async_redis()
            async for key in r.scan_iter(f"zenflow:gcal:events:{tid}:*"):
                await r.delete(key)
        except Exception:
            pass
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
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"delete_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
