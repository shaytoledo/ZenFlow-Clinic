"""
bot/services/followup_jobs.py
──────────────────────────────
Job handlers and enqueue helpers for the 24h follow-up and lifestyle-recommendation delivery
(Phase 1.3, ADR-21). Importing this module registers the handlers on `default_registry`.

Primary path is event-driven: "Complete Session" enqueues both jobs. The reconciliation sweep in
`followup_scheduler.reconcile()` re-enqueues anything missed. Every handler is idempotent against
the database (a job may run more than once — at-least-once delivery):

* followup.send_step1        skip if the session is gone, not completed, already followed up
                             (`followup_sent_at` / conversation / rating), older than
                             FOLLOWUP_EXPIRE_HOURS, or the patient has no messaging channel.
* recommendations.dispatch   skip if nothing is queued any more (sent/cleared) or the queue entry
                             was rescheduled (payload `send_at` no longer matches).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from zenflow import clock
from zenflow.queue import get_default_queue
from zenflow.worker import default_registry

logger = logging.getLogger(__name__)

FOLLOWUP_JOB = "followup.send_step1"
RECOMMENDATIONS_JOB = "recommendations.dispatch"
FOLLOWUP_DELAY_HOURS = 24
#: a follow-up that could not go out within this many hours of completion is dropped, not sent
FOLLOWUP_EXPIRE_HOURS = 48
#: an email send waiting for the therapist's Google looks again this often (connecting Google
#: wakes it at once — `resume_after_google_connected`); a safety net, not the main path
GOOGLE_RECHECK_HOURS = 6


def followup_key(appointment_id: int, completed_at: str) -> str:
    # completed_at is part of the key: completing the session again schedules a new follow-up
    # from the latest completion, and the superseded job skips itself.
    return f"followup:{int(appointment_id)}:{completed_at}"


def recommendations_key(appointment_id: int, send_at: str) -> str:
    return f"recommendations:{int(appointment_id)}:{send_at}"


def enqueue_followup(appointment_id: int, completed_at: str) -> int:
    """Schedule follow-up step 1 at completed_at + 24h (once per appointment)."""
    from web.repositories import followup_repo

    completed_at = clock.normalize(completed_at)
    run_at = clock.to_iso(clock.parse_iso(completed_at) + timedelta(hours=FOLLOWUP_DELAY_HOURS))
    followup_repo.schedule(appointment_id, run_at)
    return get_default_queue().enqueue(
        FOLLOWUP_JOB,
        {"appointment_id": int(appointment_id), "completed_at": completed_at},
        run_at=run_at,
        idempotency_key=followup_key(appointment_id, completed_at),
    )


def enqueue_recommendations(appointment_id: int, send_at: str) -> int:
    """Schedule delivery of the queued recommendations at `send_at` (once per send time)."""
    send_at = clock.normalize(send_at)
    return get_default_queue().enqueue(
        RECOMMENDATIONS_JOB,
        {"appointment_id": int(appointment_id), "send_at": send_at},
        run_at=send_at,
        idempotency_key=recommendations_key(appointment_id, send_at),
    )


def safe_enqueue(fn: Callable[..., int], *args: Any) -> int | None:
    """Enqueue from a request path without failing the request; the reconciliation sweep is the
    safety net if this ever fails."""
    try:
        return fn(*args)
    except Exception:
        logger.exception("enqueue via %s failed — reconciliation will retry", fn.__name__)
        return None


@default_registry.handler(FOLLOWUP_JOB)
async def handle_followup(payload: dict[str, Any]) -> None:
    from bot.services import followup_scheduler as fs
    from web.repositories import followup_repo, treatment_repo

    apt_id = int(payload["appointment_id"])
    row = await asyncio.to_thread(treatment_repo.get_followup_candidate, apt_id)
    if not row or not row.get("completed_at"):
        logger.info("follow-up skipped: session missing, cancelled or not completed")
        return
    if (
        row.get("followup_sent_at")
        or row.get("followup_conversation")
        or row.get("followup_rating")
    ):
        logger.info("follow-up skipped: already sent")
        return
    completed = clock.parse_iso(str(row["completed_at"]))
    expected = payload.get("completed_at")
    if expected and clock.to_iso(completed) != expected:
        logger.info("follow-up skipped: superseded by a later completion")
        return
    # The row exists for sessions completed before Phase 6.3 too (the start-up backfill); a job
    # enqueued before the backfill ran still gets one here.
    await asyncio.to_thread(
        followup_repo.schedule,
        apt_id,
        clock.to_iso(completed + timedelta(hours=FOLLOWUP_DELAY_HOURS)),
    )
    if clock.now_utc() > completed + timedelta(hours=FOLLOWUP_EXPIRE_HOURS):
        logger.warning(
            "follow-up skipped: expired (fired more than %sh after completion)",
            FOLLOWUP_EXPIRE_HOURS,
        )
        await asyncio.to_thread(followup_repo.expire_unsent, apt_id)
        return
    if row.get("source") == "manual" or int(row["patient_id"]) < 0:
        # No messaging channel. Phase 6.4 turns this into a persistent therapist alert.
        logger.info("follow-up skipped: patient has no messaging channel")
        return
    await fs._send_followup(row, raise_errors=True)


@default_registry.handler(RECOMMENDATIONS_JOB)
async def handle_recommendations(payload: dict[str, Any]) -> None:
    from bot.services import followup_scheduler as fs
    from web.repositories import treatment_repo

    apt_id = int(payload["appointment_id"])
    row = await asyncio.to_thread(treatment_repo.get_pending_recommendation, apt_id)
    if not row:
        logger.info("recommendations skipped: nothing queued (already sent or cleared)")
        await asyncio.to_thread(_settle_waiting_alert, apt_id)
        return
    expected = payload.get("send_at")
    queued_at = row.get("pending_rec_send_at")
    if expected and queued_at and clock.normalize(str(queued_at)) != expected:
        logger.info("recommendations skipped: rescheduled to %s", queued_at)
        return
    await fs.dispatch_recommendations(row)


def _settle_waiting_alert(appointment_id: int) -> None:
    """Nothing is queued any more (sent by hand, or cleared): a "waiting for Google" alert is moot."""
    from bot.db import get_db
    from web.services import notification_service

    try:
        row = (
            get_db()
            .execute("SELECT therapist_id FROM appointments WHERE id=?", (appointment_id,))
            .fetchone()
        )
        if row and row["therapist_id"]:
            notification_service.resolve_waiting_for_google(
                str(row["therapist_id"]), appointment_id
            )
    except Exception:
        logger.exception("could not resolve the waiting-for-Google alert")


def resume_after_google_connected(therapist_id: str) -> int:
    """The therapist connected Google: their queued recommendation sends run now instead of at
    their next recheck (Phase 5.4). Returns how many were woken. Never raises."""
    from bot.db import get_db

    woken = 0
    try:
        queue = get_default_queue()
        for job in queue.pending(RECOMMENDATIONS_JOB):
            row = (
                get_db()
                .execute(
                    "SELECT therapist_id FROM appointments WHERE id=?",
                    (int(job.payload.get("appointment_id") or 0),),
                )
                .fetchone()
            )
            if row and row["therapist_id"] == therapist_id and queue.run_now(job.id):
                woken += 1
    except Exception:
        logger.exception("could not wake the recommendation sends after Google was connected")
    if woken:
        logger.info("%s recommendation send(s) resumed after Google was connected", woken)
    return woken


@default_registry.on_dead(RECOMMENDATIONS_JOB)
async def recommendations_dead(payload: dict[str, Any], error: str) -> None:
    """Every attempt failed (including timeouts): one persistent alert for the therapist."""
    from web.repositories import treatment_repo
    from web.services import notification_service

    row = await asyncio.to_thread(
        treatment_repo.get_pending_recommendation, int(payload["appointment_id"])
    )
    if not row:
        return
    await asyncio.to_thread(
        notification_service.alert_send_failed,
        row.get("therapist_id", "") or "",
        int(row["appointment_id"]),
        int(row["patient_id"]),
        row.get("patient_name", "Patient") or "Patient",
        f"Delivery failed after every retry: {error}",
    )
