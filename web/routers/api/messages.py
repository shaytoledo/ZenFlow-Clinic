"""
web/routers/api/messages.py
────────────────────────────
Messaging endpoints: unread count, conversation list, send reply via Telegram.
"""

import asyncio
import functools
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from bot.interfaces import get_channel
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


async def _live_session(patient_id: int) -> dict | None:
    """The patient's live relay session, or None (missing, corrupt, or Redis down)."""
    from bot.redis_client import get_async_redis

    try:
        raw = await get_async_redis().get(f"zenflow:relay:active:{patient_id}")
        parsed = json.loads(raw) if raw else None
    except Exception as e:  # Redis down / corrupt blob: "no live session" — never 500
        logger.warning(f"relay session lookup failed for patient {patient_id}: {e}")
        return None
    return parsed if isinstance(parsed, dict) else None


async def _assert_conversation_owner(therapist: dict, patient_id: int) -> None:
    """Allow a therapist into their own conversation with the patient, and nobody else's.

    Ownership is structural (Phase 2.4, SF-008): history is stored per therapist, so "mine" means
    a live session owned by this therapist or a stored conversation under their own key. There is
    no longer any "has an appointment with this patient" rule — that let a therapist who treated
    the patient read another therapist's chat once the live session had ended.
    403 when the live session belongs to someone else and this therapist has no conversation of
    their own; 404 otherwise.
    """
    session = await _live_session(patient_id)
    if session is not None and session.get("therapist_id") == therapist["id"]:
        return
    if await telegram_service.has_relay_history(therapist["id"], patient_id):
        return
    if session is not None:
        raise HTTPException(status_code=403, detail="Conversation belongs to another therapist")
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

    messages = await telegram_service.get_relay_messages(therapist["id"], patient_id)
    await telegram_service.mark_conversation_read(therapist["id"], patient_id)
    return JSONResponse({"patient_id": patient_id, "messages": messages})


@router.post("/unread/{patient_id}")
async def mark_unread(patient_id: int, request: Request):
    """Mark a conversation as unread (removes the last-seen timestamp)."""
    therapist = require_active_therapist(request)
    await _assert_conversation_owner(therapist, patient_id)
    await telegram_service.mark_conversation_unread(therapist["id"], patient_id)
    return JSONResponse({"ok": True, "patient_id": patient_id})


@router.delete("/history/{patient_id}")
async def delete_message_history(patient_id: int, request: Request):
    """Delete a relay conversation from Redis (history + presence + unread)."""
    therapist = require_active_therapist(request)
    await _assert_conversation_owner(therapist, patient_id)
    removed = await telegram_service.delete_conversation(therapist["id"], patient_id)
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
        # Plain text, like the bot relay: a reply containing _ or * must not fail to send (B2).
        sent = await get_channel("telegram").send_text(
            body.patient_id, f"👨‍⚕️ {therapist_name}:\n{body.text}"
        )
    except Exception as e:
        logger.error(f"send_message → patient delivery failed: {e}")
        await _log_relay(body.patient_id, therapist["id"], "failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Delivery to patient failed: {e}")

    await _log_relay(body.patient_id, therapist["id"], "sent", provider_message_id=sent.message_id)

    await telegram_service.append_relay_message(
        therapist["id"], body.patient_id, "therapist", body.text
    )
    await telegram_service.mark_conversation_read(therapist["id"], body.patient_id)

    therapist_tg_id = (therapist or {}).get("telegram_id")
    last_msg_id = await _last_forwarded_msg_id(therapist["id"], body.patient_id)
    await telegram_service.echo_to_therapist_chat(
        therapist_telegram_id=therapist_tg_id,
        text=f"💬 Sent via web:\n{body.text}",
        reply_to_msg_id=last_msg_id,
    )

    return JSONResponse({"ok": True})


async def _log_relay(
    telegram_id: int,
    therapist_id: str,
    status: str,
    *,
    provider_message_id: object = None,
    error: str | None = None,
) -> None:
    """A reply typed on the messages page is a relay message like any other (plan 8.3)."""
    from web.repositories import message_log_repo

    await asyncio.to_thread(
        functools.partial(
            message_log_repo.record_relay,
            direction="out",
            channel="telegram",
            external_id=telegram_id,
            therapist_id=therapist_id,
            status=status,
            provider_message_id=provider_message_id,
            error=error,
        )
    )


async def _last_forwarded_msg_id(therapist_id: str, patient_id: int) -> int | None:
    """The last message forwarded into *this* therapist's chat for the patient, if live.

    Message ids are per chat; another therapist's id would thread the echo onto an unrelated
    message.
    """
    session = await _live_session(patient_id)
    if session is None or session.get("therapist_id") != therapist_id:
        return None
    msg_id = session.get("last_msg_id")
    return msg_id if isinstance(msg_id, int) else None
