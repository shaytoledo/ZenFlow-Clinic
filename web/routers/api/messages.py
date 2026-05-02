"""
web/routers/api/messages.py
────────────────────────────
Messaging endpoints: unread count, conversation list, send reply via Telegram.
"""
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import _active_therapist_or_redirect
from web.services import telegram_service

router = APIRouter(prefix="/api/messages")
logger = logging.getLogger(__name__)


class SendMessageIn(BaseModel):
    patient_id: int
    text: str


@router.get("/active")
async def get_active_messages():
    """Return number of unread patient messages across all active relay sessions."""
    count = await telegram_service.get_total_unread_count()
    return JSONResponse({"count": count})


@router.get("/conversations")
async def list_conversations(request: Request):
    """List all active relay conversations with patient metadata."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sessions = await telegram_service.get_active_relay_conversations()
    return JSONResponse(sessions)


@router.get("/history/{patient_id}")
async def get_message_history(patient_id: int, request: Request):
    """Return stored relay history; opening a conversation marks it as read."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

    messages = await telegram_service.get_relay_messages(patient_id)
    await telegram_service.mark_conversation_read(patient_id)
    return JSONResponse({"patient_id": patient_id, "messages": messages})


@router.post("/unread/{patient_id}")
async def mark_unread(patient_id: int, request: Request):
    """Mark a conversation as unread (removes the last-seen timestamp)."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    await telegram_service.mark_conversation_unread(patient_id)
    return JSONResponse({"ok": True, "patient_id": patient_id})


@router.delete("/history/{patient_id}")
async def delete_message_history(patient_id: int, request: Request):
    """Delete a relay conversation from Redis (history + presence + unread)."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    removed = await telegram_service.delete_conversation(patient_id)
    return JSONResponse({"ok": True, "patient_id": patient_id, "removed_keys": removed})


@router.post("/send")
async def send_message(body: SendMessageIn, request: Request):
    """Deliver to patient via patient bot, then echo into the therapist's bot chat."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

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
