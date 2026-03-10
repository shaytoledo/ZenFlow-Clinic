"""
web/routers/api/messages.py
────────────────────────────
Messaging endpoints: active relay count, conversation list, send reply via Telegram.
"""
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import _active_therapist_or_redirect
from web.services import telegram_service
from web.services.cache_service import get_relay_count

router = APIRouter(prefix="/api/messages")
logger = logging.getLogger(__name__)


class SendMessageIn(BaseModel):
    patient_id: int
    text: str


@router.get("/active")
async def get_active_messages():
    """Return count of patients currently in an active relay session."""
    count = await get_relay_count()
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
    """Return stored relay message history for a patient."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

    messages = await telegram_service.get_relay_messages(patient_id)
    return JSONResponse({"patient_id": patient_id, "messages": messages})


@router.post("/send")
async def send_message(body: SendMessageIn, request: Request):
    """Send a message from the web UI to a patient via the therapist bot."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Message text is required")

    try:
        await telegram_service.send_via_therapist_bot(body.patient_id, body.text, parse_mode="")
        # Record in relay history
        await telegram_service.append_relay_message(
            body.patient_id, "therapist", body.text
        )
    except Exception as e:
        logger.error(f"send_message error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    return JSONResponse({"ok": True})
