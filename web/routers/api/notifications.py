"""
web/routers/api/notifications.py
────────────────────────────────
REST endpoints for the topbar notification bell.
"""
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from web.deps import _active_therapist_or_redirect
from web.services import notification_service

router = APIRouter(prefix="/api/notifications")


def _therapist(request: Request) -> dict:
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect or not therapist:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return therapist


@router.get("")
async def list_notifications(request: Request):
    t = _therapist(request)
    items = await asyncio.to_thread(notification_service.list_for_therapist, t["id"], 50)
    unread = await asyncio.to_thread(notification_service.unread_count, t["id"])
    return JSONResponse({"items": items, "unread": unread})


@router.get("/unread-count")
async def get_unread_count(request: Request):
    t = _therapist(request)
    n = await asyncio.to_thread(notification_service.unread_count, t["id"])
    return JSONResponse({"unread": n})


@router.post("/{notification_id}/read")
async def mark_read(notification_id: int, request: Request):
    t = _therapist(request)
    await asyncio.to_thread(notification_service.mark_read, t["id"], notification_id)
    return JSONResponse({"ok": True})


@router.post("/read-all")
async def mark_all_read(request: Request):
    t = _therapist(request)
    await asyncio.to_thread(notification_service.mark_read, t["id"], None)
    return JSONResponse({"ok": True})


@router.post("/{notification_id}/resolve")
async def resolve_notification(notification_id: int, request: Request):
    t = _therapist(request)
    await asyncio.to_thread(notification_service.resolve, t["id"], notification_id)
    return JSONResponse({"ok": True})
