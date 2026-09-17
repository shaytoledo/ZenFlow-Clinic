"""
bot/services/booking_jobs.py
─────────────────────────────
The booking confirmation (Phase 7.3): "your appointment is confirmed for …".

A booking made through the API has no conversation to answer in, so the confirmation is a queued
job (ADR-20) — the patient is told even if the provider is briefly down. Importing this module
registers the handler.

The Telegram booking flow confirms inside its own conversation and asks for no job.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from zenflow.queue import get_default_queue
from zenflow.worker import default_registry

logger = logging.getLogger(__name__)

CONFIRM_JOB = "booking.confirm"


def confirmation_key(appointment_id: int) -> str:
    return f"booking-confirm:{int(appointment_id)}"


def enqueue_confirmation(appointment_id: int) -> int:
    """One confirmation per appointment, whatever retries the caller makes."""
    return get_default_queue().enqueue(
        CONFIRM_JOB,
        {"appointment_id": int(appointment_id)},
        idempotency_key=confirmation_key(appointment_id),
    )


@default_registry.handler(CONFIRM_JOB)
async def handle_confirmation(payload: dict[str, Any]) -> None:
    from bot.db import get_db
    from bot.interfaces import get_channel
    from bot.locales import get_lang, t
    from web.repositories import message_log_repo, patient_repo
    from zenflow import clock

    apt_id = int(payload["appointment_id"])
    row = await asyncio.to_thread(
        lambda: get_db()
        .execute(
            """SELECT a.id, a.patient_id, a.patient_name, a.therapist_id, a.date, a.time,
                      a.status, th.name AS therapist_name
               FROM appointments a
               LEFT JOIN therapists th ON th.id = a.therapist_id
               WHERE a.id = ?""",
            (apt_id,),
        )
        .fetchone()
    )
    if row is None or row["status"] != "active":
        logger.info("booking confirmation skipped: appointment %s is gone or cancelled", apt_id)
        return
    contact = await asyncio.to_thread(patient_repo.messaging_contact, int(row["patient_id"]))
    if contact is None:
        logger.info("booking confirmation skipped: patient has no messaging channel")
        return

    lang = await asyncio.to_thread(get_lang, row["therapist_id"])
    text = t(
        "bot_booking_confirmed",
        lang,
        therapist=row["therapist_name"] or "ZenFlow",
        date=clock.format_clinic(f"{row['date']}T{row['time']}:00", "%d %B %Y"),
        time=row["time"],
    )
    sent = await get_channel(contact.channel).send_text(contact.external_id, text)
    await asyncio.to_thread(
        message_log_repo.record,
        channel=contact.channel,
        kind="confirmation",
        status="sent",
        therapist_id=str(row["therapist_id"] or ""),
        patient_id=int(row["patient_id"]),
        appointment_id=apt_id,
        provider_message_id=message_log_repo.provider_id(sent),
    )
    logger.info("booking confirmation sent for appointment %s", apt_id)


@default_registry.on_dead(CONFIRM_JOB)
async def confirmation_dead(payload: dict[str, Any], error: str) -> None:
    """Every attempt failed: record the attempt so the therapist can see it was never delivered."""
    from bot.db import get_db
    from web.repositories import message_log_repo

    apt_id = int(payload["appointment_id"])
    row = await asyncio.to_thread(
        lambda: get_db()
        .execute("SELECT patient_id, therapist_id FROM appointments WHERE id=?", (apt_id,))
        .fetchone()
    )
    if row is None:
        return
    await asyncio.to_thread(
        message_log_repo.record,
        channel="telegram",
        kind="confirmation",
        status="failed",
        therapist_id=str(row["therapist_id"] or ""),
        patient_id=int(row["patient_id"]),
        appointment_id=apt_id,
        error=error,
    )
