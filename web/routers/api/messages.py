"""
web/routers/api/messages.py
────────────────────────────
Messaging endpoints: unread count, conversation list, send reply via Telegram.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import require_active_therapist
from web.services import telegram_service

router = APIRouter(prefix="/api/messages")
logger = logging.getLogger(__name__)


class SendMessageIn(BaseModel):
    patient_id: int
    text: str


async def _own_sessions(therapist: dict) -> list[dict]:
    """Active relay sessions that belong to this therapist (tenant scoping, F6)."""
    sessions = await telegram_service.get_active_relay_conversations()
    return [s for s in sessions if s.get("therapist_id") == therapist["id"]]


async def _assert_conversation_owner(therapist: dict, patient_id: int) -> None:
    """403 if the patient's active relay session belongs to another therapist; 404 if there is
    no session and the patient never had an appointment with this therapist."""
    from bot.redis_client import get_async_redis

    data: dict | None = None
    try:
        raw = await get_async_redis().get(f"zenflow:relay:active:{patient_id}")
        parsed = json.loads(raw) if raw else None
        data = parsed if isinstance(parsed, dict) else None
    except Exception as e:  # Redis down / corrupt blob: treat as "no live session", never 500
        logger.warning(f"relay session lookup failed for patient {patient_id}: {e}")
        data = None
    if data is not None:
        if data.get("therapist_id") == therapist["id"]:
            return
        raise HTTPException(status_code=403, detail="Conversation belongs to another therapist")
    from web.repositories import appointment_repo

    owned = await asyncio.to_thread(appointment_repo.list_by_patient, patient_id, therapist["id"])
    if owned:
        return
    raise HTTPException(status_code=404, detail="Conversation not found")


@router.get("/active")
async def get_active_messages(request: Request):
    """Return number of unread patient messages across this therapist's active relay sessions."""
    therapist = require_active_therapist(request)
    sessions = await _own_sessions(therapist)
    count = sum(int(s.get("unread_count") or 0) for s in sessions)
    return JSONResponse({"count": count})


@router.get("/conversations")
async def list_conversations(request: Request):
    """List this therapist's active relay conversations with patient metadata."""
    therapist = require_active_therapist(request)
    return JSONResponse(await _own_sessions(therapist))


@router.get("/history/{patient_id}")
async def get_message_history(patient_id: int, request: Request):
    """Return stored relay history; opening a conversation marks it as read."""
    therapist = require_active_therapist(request)
    await _assert_conversation_owner(therapist, patient_id)

    messages = await telegram_service.get_relay_messages(patient_id)
    await telegram_service.mark_conversation_read(patient_id)
    return JSONResponse({"patient_id": patient_id, "messages": messages})


@router.post("/unread/{patient_id}")
async def mark_unread(patient_id: int, request: Request):
    """Mark a conversation as unread (removes the last-seen timestamp)."""
    therapist = require_active_therapist(request)
    await _assert_conversation_owner(therapist, patient_id)
    await telegram_service.mark_conversation_unread(patient_id)
    return JSONResponse({"ok": True, "patient_id": patient_id})


@router.delete("/history/{patient_id}")
async def delete_message_history(patient_id: int, request: Request):
    """Delete a relay conversation from Redis (history + presence + unread)."""
    therapist = require_active_therapist(request)
    await _assert_conversation_owner(therapist, patient_id)
    removed = await telegram_service.delete_conversation(patient_id)
    return JSONResponse({"ok": True, "patient_id": patient_id, "removed_keys": removed})


@router.post("/send")
async def send_message(body: SendMessageIn, request: Request):
    """Deliver to patient via patient bot, then echo into the therapist's bot chat."""
    therapist = require_active_therapist(request)
    await _assert_conversation_owner(therapist, body.patient_id)

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Message text is required")

    therapist_name = (therapist or {}).get("name", "Therapist")
    try:
        await telegram_service.send_to_patient(
            body.patient_id,
            f"👨‍⚕️ *{therapist_name}:*\n{body.text}",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"send_message → patient delivery failed: {e}")
        raise HTTPException(status_code=500, detail=f"Delivery to patient failed: {e}")

    await telegram_service.append_relay_message(body.patient_id, "therapist", body.text)
    await telegram_service.mark_conversation_read(body.patient_id)

    therapist_tg_id = (therapist or {}).get("telegram_id")
    last_msg_id = await _last_forwarded_msg_id(body.patient_id)
    await telegram_service.echo_to_therapist_chat(
        therapist_telegram_id=therapist_tg_id,
        text=f"💬 *Sent via web:*\n{body.text}",
        reply_to_msg_id=last_msg_id,
    )

    return JSONResponse({"ok": True})


async def _last_forwarded_msg_id(patient_id: int) -> int | None:
    """Look up the most recent therapist-bot message_id forwarded for this patient."""
    try:
        from bot.redis_client import get_async_redis

        r = get_async_redis()
        raw = await r.get(f"zenflow:relay:active:{patient_id}")
        if not raw:
            return None
        return json.loads(raw).get("last_msg_id")
    except Exception:
        return None
