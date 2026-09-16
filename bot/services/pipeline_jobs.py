"""
bot/services/pipeline_jobs.py
──────────────────────────────
The AI generation pipeline as durable queued jobs (Phase 3.1, ADR-23). Importing this module
registers the handlers on `default_registry`.

    intake.finalize      Stage 0   clinical summary from the stored conversation
    diagnosis.generate   Stage 1   TCM pattern, principles, certainty, lifestyle advice
    points.generate      Stage 2A  first batch of points          (payload batch=1)
    points.generate      Stage 2B  complementary batch            (payload batch=2)

Each stage enqueues the next. Every handler:

* reads its input from the database (`intake_sessions.history_json`), never from the Redis
  intake history, which expires after 30 minutes and does not survive a restart;
* skips work the database shows is already done, so a replayed job generates nothing twice;
* raises `GenerationError` when the AI fails, so the queue retries with backoff — on the last
  attempt it degrades (Stage 0) or marks the session FAILED (Stages 1-2);
* holds the per-appointment lease `generation:{appointment_id}`, so two generations for one
  session never run at once, even with a second worker process;
* moves `treatment_notes.points_status` forward only:
  GENERATING_STAGE_0 → _1 → _2A → _2B → COMPLETED, or FAILED.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from zenflow import leases
from zenflow.queue import get_default_queue
from zenflow.worker import current_job, default_registry, is_last_attempt

logger = logging.getLogger(__name__)

INTAKE_FINALIZE = "intake.finalize"
DIAGNOSIS_GENERATE = "diagnosis.generate"
POINTS_GENERATE = "points.generate"

#: LLM calls are slow and costly; three tries with the queue's backoff (60 s, 120 s) is enough.
MAX_ATTEMPTS = 3
#: longer than the worker's handler timeout (300 s), so a live handler never loses its lease
LOCK_TTL_SECONDS = 360

STAGE_0 = "GENERATING_STAGE_0"
STAGE_1 = "GENERATING_STAGE_1"
STAGE_2A = "GENERATING_STAGE_2A"
STAGE_2B = "GENERATING_STAGE_2B"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
_ORDER = (STAGE_0, STAGE_1, STAGE_2A, STAGE_2B, COMPLETED)
# statuses an earlier or unrelated run may have left behind; a new stage may replace them
_LEGACY = ("GENERATING",)
#: every status that means "a generation is in progress"
IN_PROGRESS = (*_ORDER[:-1], *_LEGACY)
_NAMES = (INTAKE_FINALIZE, DIAGNOSIS_GENERATE, POINTS_GENERATE)


class PipelineBusy(RuntimeError):
    """Another generation holds this appointment; the queue retries later."""


def lock_name(appointment_id: int) -> str:
    return f"generation:{int(appointment_id)}"


def _key(stage: str, appointment_id: int, run: str, batch: int | None = None) -> str:
    suffix = f":{batch}" if batch is not None else ""
    return f"pipeline:{stage}:{int(appointment_id)}:{run}{suffix}"


def _enqueue(name: str, payload: dict[str, Any]) -> int:
    return get_default_queue().enqueue(
        name,
        payload,
        idempotency_key=_key(
            name, payload["appointment_id"], payload.get("run", "intake"), payload.get("batch")
        ),
        max_attempts=MAX_ATTEMPTS,
    )


# ── status ──
async def _advance(appointment_id: int, status: str) -> None:
    """Move forward only: a replayed job must never drag a finished session backwards."""
    from web.repositories.treatment_repo import advance_points_status

    earlier = _ORDER[: _ORDER.index(status)] + _LEGACY
    await asyncio.to_thread(advance_points_status, appointment_id, status, only_from=earlier)


async def _fail(appointment_id: int) -> None:
    from web.repositories.treatment_repo import advance_points_status

    await asyncio.to_thread(
        advance_points_status, appointment_id, FAILED, only_from=_ORDER[:-1] + _LEGACY
    )


async def _cancelled(appointment_id: int) -> bool:
    from web.repositories.treatment_repo import get_by_appointment

    notes = await asyncio.to_thread(get_by_appointment, appointment_id)
    return bool(notes) and (notes or {}).get("points_status") == CANCELLED


# ── inputs ──
def _load(appointment_id: int) -> dict[str, Any] | None:
    """The appointment, its stored conversation and its notes, or None if it no longer exists."""
    from bot.db import get_db
    from web.repositories import treatment_repo

    conn = get_db()
    apt = conn.execute(
        "SELECT id, patient_id, summary FROM appointments WHERE id=?", (appointment_id,)
    ).fetchone()
    if apt is None:
        return None
    row = conn.execute(
        "SELECT history_json FROM intake_sessions WHERE appointment_id=? ORDER BY id DESC LIMIT 1",
        (appointment_id,),
    ).fetchone()
    try:
        history = json.loads(row["history_json"]) if row and row["history_json"] else []
    except (TypeError, ValueError):
        history = []
    return {
        "patient_id": int(apt["patient_id"]),
        "summary": (apt["summary"] or "").strip(),
        "history": history if isinstance(history, list) else [],
        "notes": treatment_repo.get_by_appointment(appointment_id) or {},
    }


@contextmanager
def _exclusive(appointment_id: int) -> Iterator[None]:
    job = current_job()
    holder = f"job-{job.id}" if job is not None else f"direct-{uuid.uuid4().hex[:8]}"
    with leases.held(lock_name(appointment_id), holder, ttl_seconds=LOCK_TTL_SECONDS) as got:
        if not got:
            raise PipelineBusy(f"another generation is running for appointment {appointment_id}")
        yield


# ── public entry point ──
def start_intake_pipeline(appointment_id: int, patient_id: int, run: str = "intake") -> int:
    """Called when the intake is saved: mark the session as queued and enqueue Stage 0."""
    from web.repositories.treatment_repo import set_points_status

    set_points_status(appointment_id, STAGE_0)
    return _enqueue(
        INTAKE_FINALIZE,
        {"appointment_id": int(appointment_id), "patient_id": int(patient_id), "run": run},
    )


def start_points_regeneration(appointment_id: int, patient_id: int, lang: str = "en") -> str:
    """Replace the session's points: clear them, then queue batch 1 of a fresh run (Phase 3.3).

    A run id of its own keeps it apart from the intake run's idempotency keys, so the same
    session can be regenerated any number of times.
    """
    from web.repositories.treatment_repo import reset_points

    run = f"regen-{uuid.uuid4().hex[:12]}"
    reset_points(appointment_id, STAGE_2A)
    _enqueue(
        POINTS_GENERATE,
        {
            "appointment_id": int(appointment_id),
            "patient_id": int(patient_id),
            "run": run,
            "batch": 1,
            "lang": lang,
        },
    )
    return run


def cancel_generation(appointment_id: int) -> tuple[bool, int]:
    """Stop whatever is generating for the session. Returns (was_generating, jobs_cancelled).

    The status flips to CANCELLED first, so a stage already inside an AI call discards its result
    when it returns (its write expects the status it started with). Its queued successors are
    cancelled here and never run. The SQL reaches into the SQLite `jobs` table (ADR-20).
    """
    from bot.db import get_db
    from web.repositories.treatment_repo import advance_points_status

    was_generating = advance_points_status(
        appointment_id, CANCELLED, only_from=IN_PROGRESS, allow_empty=False
    )
    rows = (
        get_db()
        .execute(
            """SELECT id FROM jobs
           WHERE status IN ('pending', 'running')
             AND name IN (SELECT value FROM json_each(?))
             AND json_extract(payload_json, '$.appointment_id') = ?""",
            (json.dumps(list(_NAMES)), int(appointment_id)),
        )
        .fetchall()
    )
    queue = get_default_queue()
    cancelled = sum(1 for row in rows if queue.cancel(int(row["id"])))
    logger.info("generation cancelled (was running: %s, jobs: %s)", was_generating, cancelled)
    return was_generating, cancelled


# ── Stage 0 ──
@default_registry.handler(INTAKE_FINALIZE)
async def finalize_intake(payload: dict[str, Any]) -> None:
    from bot.patient_bot.services.ai_intake import (
        FALLBACK_SUMMARY,
        GenerationError,
        summarize_history,
    )
    from bot.patient_bot.services.appointments import update_appointment_summary

    apt_id = int(payload["appointment_id"])
    with _exclusive(apt_id):
        data = await asyncio.to_thread(_load, apt_id)
        if data is None:
            logger.warning("appointment is gone; pipeline stopped")
            return
        if data["notes"].get("points_status") == CANCELLED:
            return
        if not data["summary"]:
            try:
                summary = await summarize_history(data["history"], strict=True, log_tag=str(apt_id))
            except GenerationError:
                if not is_last_attempt():
                    raise
                logger.warning("summary unavailable after all attempts; continuing without it")
                summary = FALLBACK_SUMMARY
            await asyncio.to_thread(update_appointment_summary, apt_id, summary, data["history"])
        await _advance(apt_id, STAGE_1)
    if await _cancelled(apt_id):
        return
    _enqueue(DIAGNOSIS_GENERATE, payload)


# ── Stage 1 ──
@default_registry.handler(DIAGNOSIS_GENERATE)
async def generate_diagnosis(payload: dict[str, Any]) -> None:
    from bot.patient_bot.services.ai_intake import (
        GenerationError,
        diagnosis_context,
        format_transcript,
        generate_diagnosis_only,
    )
    from bot.patient_bot.services.appointments import save_treatment_notes

    apt_id = int(payload["appointment_id"])
    with _exclusive(apt_id):
        data = await asyncio.to_thread(_load, apt_id)
        if data is None:
            logger.warning("appointment is gone; pipeline stopped")
            return
        if data["notes"].get("points_status") == CANCELLED:
            return
        if not data["notes"].get("tcm_pattern"):
            transcript = format_transcript(data["history"]) or data["summary"]
            diagnosis = await generate_diagnosis_only(
                diagnosis_context(data["summary"], data["history"]),
                transcript,
                log_tag=str(apt_id),
            )
            if not diagnosis.get("tcm_pattern"):
                if not is_last_attempt():
                    raise GenerationError("diagnosis failed: no TCM pattern returned")
                await _fail(apt_id)
                return
            await asyncio.to_thread(save_treatment_notes, apt_id, data["patient_id"], diagnosis)
        await _advance(apt_id, STAGE_2A)
    if await _cancelled(apt_id):
        return
    _enqueue(POINTS_GENERATE, {**payload, "batch": 1})


# ── Stage 2 ──
@default_registry.handler(POINTS_GENERATE)
async def generate_points(payload: dict[str, Any]) -> None:
    from bot.patient_bot.services.ai_intake import (
        GenerationError,
        format_transcript,
        select_points_for_diagnosis,
    )
    from web.repositories.treatment_repo import append_points

    apt_id = int(payload["appointment_id"])
    batch = int(payload["batch"])
    with _exclusive(apt_id):
        data = await asyncio.to_thread(_load, apt_id)
        if data is None:
            logger.warning("appointment is gone; pipeline stopped")
            return
        notes = data["notes"]
        status = notes.get("points_status") or ""
        existing = [
            p["code"]
            for p in (notes.get("ai_suggested_points") or [])
            if isinstance(p, dict) and p.get("code")
        ]
        if status == CANCELLED:
            logger.info("generation was cancelled; points batch %s not run", batch)
            return
        if status == COMPLETED or (batch == 1 and (status == STAGE_2B or existing)):
            logger.info("points batch %s already done; skipping", batch)
        elif not notes.get("tcm_pattern"):
            logger.error("no diagnosis to select points for")
            await _fail(apt_id)
            return
        else:
            points = await select_points_for_diagnosis(
                tcm_pattern=notes["tcm_pattern"],
                treatment_principles=notes.get("treatment_principles") or "",
                intake_context=format_transcript(data["history"]) or data["summary"],
                log_tag=str(apt_id),
                batch_number=batch,
                existing_codes=existing if batch == 2 else None,
                lang=str(payload.get("lang") or "en"),
                retry_once=False,
            )
            if not points and not is_last_attempt():
                raise GenerationError(f"points batch {batch} failed: no points returned")
            # Batch 2 finishes the run: COMPLETED if the session has any points at all.
            finished = COMPLETED if (existing or points) else FAILED
            next_status = STAGE_2B if batch == 1 else finished
            # Points and status in one statement, and only if nobody changed the status while the
            # AI was working (Cancel): a crash or a cancel can't leave them disagreeing.
            wrote = await asyncio.to_thread(append_points, apt_id, points, next_status, status)
            if not wrote:
                logger.info("status changed during batch %s (cancelled?); result discarded", batch)
                return
            logger.info("points batch %s saved (%s points)", batch, len(points))
    if batch == 1:
        _enqueue(POINTS_GENERATE, {**payload, "batch": 2})


# ── dead letters: the therapist must see a failure, not an endless spinner ──
async def _mark_failed(payload: dict[str, Any], error: str) -> None:
    logger.error("generation gave up for appointment %s: %s", payload.get("appointment_id"), error)
    await _fail(int(payload["appointment_id"]))


for _name in (INTAKE_FINALIZE, DIAGNOSIS_GENERATE, POINTS_GENERATE):
    default_registry.on_dead(_name)(_mark_failed)
