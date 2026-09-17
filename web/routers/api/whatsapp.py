"""
web/routers/api/whatsapp.py
────────────────────────────
WhatsApp's webhook (plan 7.4b): the patient's side of the channel.

Meta delivers every inbound message and every delivery receipt to one endpoint. It is mounted
only while `ZF_CHANNEL_WHATSAPP=1`, and:

* **verifies before it reads** — `X-Hub-Signature-256` over the raw body, fail-closed;
* **answers 200 to anything it cannot use** — Meta retries a non-200, and retrying will not make
  a stranger's message meaningful;
* **acts once** — Meta re-delivers until it sees a 200, so message ids are remembered (Redis)
  and a repeat is dropped;
* **answers the check-in** — the same `followup_scheduler` the Telegram handler uses (Phase 6.2),
  which resolves the patient through `patient_channels` (7.2).

Sending is `bot/interfaces/whatsapp_channel.py`; the conversation logic is shared with Telegram.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["whatsapp"])

#: how long a delivered message id is remembered, so a retry is not acted on twice
SEEN_TTL_SECONDS = 24 * 3600


def _channel() -> Any:
    """The adapter, or None while `ZF_CHANNEL_WHATSAPP` is off (the endpoint then does not exist)."""
    from bot.interfaces import get_channel

    try:
        return get_channel("whatsapp")
    except ValueError:
        return None


def _absent() -> Response:
    return JSONResponse({"detail": "Not Found"}, status_code=404)


@router.get("/whatsapp", include_in_schema=False)
async def verify_subscription(request: Request) -> Response:
    """Meta's subscription handshake: echo the challenge when the verify token matches."""
    channel = _channel()
    if channel is None:
        return _absent()
    params = request.query_params
    challenge = channel.verify_handshake(
        params.get("hub.mode", ""),
        params.get("hub.verify_token", ""),
        params.get("hub.challenge", ""),
    )
    if challenge is None:
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(challenge)


@router.post("/whatsapp", include_in_schema=False)
async def receive(request: Request) -> Response:
    """One inbound delivery: a patient's message, or a receipt for one of ours."""
    channel = _channel()
    if channel is None:
        return _absent()
    body = await request.body()
    if not channel.verify_webhook(request.headers, body):
        logger.warning("WhatsApp delivery refused: bad or missing signature")
        return JSONResponse({"ok": False}, status_code=401)

    payload = _json(body)
    if payload is None:
        return JSONResponse({"ok": True})  # nothing to do; a retry would not help

    for receipt in channel.statuses(payload):
        await _record_receipt(receipt)

    message = channel.parse_inbound(payload)
    if message is None:
        return JSONResponse({"ok": True})
    if not await _claim(message.message_id):
        logger.info("WhatsApp delivery %s already handled", message.message_id)
        return JSONResponse({"ok": True})

    await _remember_window(message)
    await _answer(channel, message)
    return JSONResponse({"ok": True})


def _json(body: bytes) -> Any:
    import json

    try:
        return json.loads(body)
    except ValueError:
        logger.warning("WhatsApp delivery was not JSON")
        return None


async def _claim(message_id: str | None) -> bool:
    """True the first time this delivery is seen. A Redis outage lets it through."""
    if not message_id:
        return True
    try:
        from bot.redis_client import get_async_redis

        return bool(
            await get_async_redis().set(
                f"zenflow:whatsapp:seen:{message_id}", "1", ex=SEEN_TTL_SECONDS, nx=True
            )
        )
    except Exception as e:
        logger.debug("WhatsApp delivery not de-duplicated: %s", type(e).__name__)
        return True


async def _remember_window(message: Any) -> None:
    """The patient's message opens their 24-hour service window.

    Stamped when it reaches us, not with the provider's own timestamp: answering a message we are
    holding right now must never be refused because of a stale or skewed clock.
    """
    from bot.interfaces.whatsapp_channel import remember_inbound

    try:
        await remember_inbound(message.external_user_id, message.message_id or "")
    except Exception as e:
        logger.debug("WhatsApp service window not recorded: %s", type(e).__name__)


async def _answer(channel: Any, message: Any) -> None:
    """Let the check-in have it; anything else is not a conversation we hold on WhatsApp yet."""
    from bot.services.followup_scheduler import (
        consume_followup_button,
        consume_followup_conversation,
    )

    sender = message.external_user_id
    prompt = None
    if message.button_data:
        result = await consume_followup_button(sender, message.button_data, channel="whatsapp")
        if not result.consumed:
            return
        prompt = result.prompt
        if result.toast and prompt is None:
            await _send(channel, sender, result.toast, None)
            return
    elif message.text:
        consumed, prompt = await consume_followup_conversation(
            sender, message.text, channel="whatsapp"
        )
        if not consumed:
            logger.info("WhatsApp message from %s is not part of a check-in", sender)
            return
    else:
        return
    if prompt is not None:
        await _send(channel, sender, prompt.text, prompt.buttons)


async def _send(channel: Any, recipient: str, text: str, buttons: Any) -> None:
    try:
        if buttons:
            await channel.send_buttons(recipient, text, buttons)
        else:
            await channel.send_text(recipient, text)
    except Exception as e:  # the answer is recorded either way
        logger.warning("WhatsApp reply not delivered to %s: %s", recipient, e)


async def _record_receipt(receipt: dict[str, Any]) -> None:
    """Only failures are worth a row: the therapist needs to know a message never arrived."""
    if receipt.get("status") != "failed":
        return
    import asyncio

    from bot.db import get_db
    from web.repositories import message_log_repo

    def _write() -> None:
        row = (
            get_db()
            .execute(
                """SELECT therapist_id, patient_id, appointment_id, kind FROM message_log
                   WHERE provider_message_id=? ORDER BY id DESC LIMIT 1""",
                (receipt.get("message_id"),),
            )
            .fetchone()
        )
        message_log_repo.record(
            channel="whatsapp",
            kind=str(row["kind"]) if row else "followup",
            status="failed",
            therapist_id=str(row["therapist_id"]) if row else "",
            patient_id=int(row["patient_id"]) if row and row["patient_id"] is not None else None,
            appointment_id=(
                int(row["appointment_id"]) if row and row["appointment_id"] is not None else None
            ),
            provider_message_id=receipt.get("message_id"),
            error=receipt.get("error") or "WhatsApp reported the message as failed",
        )

    try:
        await asyncio.to_thread(_write)
    except Exception:
        logger.exception("WhatsApp delivery receipt not recorded")
