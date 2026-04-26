"""
bot/services/followup_scheduler.py
───────────────────────────────────
24h post-treatment auto follow-up.

Every `POLL_INTERVAL_SECONDS` we look at `treatment_notes.completed_at` and
pick rows that landed in the [22h, 26h] window — the 4-hour grace period
absorbs missed cycles (process restart, network blip) without double-sending,
since each row is also guarded by a Redis sentinel.

Outbound delivery goes through `bot.interfaces.get_default_channel()`, so when
WhatsApp is added later this scheduler keeps working without changes.

Reply capture lives in `consume_followup_rating()` — `bot/patient_bot/start.py`
calls it on every text message and we silently absorb the reply if the patient
has an outstanding follow-up. A non-numeric reply falls through to the normal
menu so we never trap users.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from bot.db import get_db
from bot.redis_client import get_async_redis

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1800            # 30 min
WINDOW_HOURS_MIN = 22                   # earliest = 22h after treatment
WINDOW_HOURS_MAX = 26                   # latest = 26h after treatment
SENT_TTL_SECONDS = 7 * 86400            # don't re-evaluate for a week
AWAITING_TTL_SECONDS = 24 * 3600        # patient has 24h to reply with a rating

FOLLOWUP_TEMPLATE = (
    "🌿 Hi {name}! Following up on your treatment yesterday.\n\n"
    "How are you feeling now? Reply with a number:\n"
    "1 — Much worse\n"
    "2 — A bit worse\n"
    "3 — About the same\n"
    "4 — A bit better\n"
    "5 — Much better"
)


# ── DB queries (run in a worker thread to keep the loop non-blocking) ─────────

def _find_due_followups() -> list[dict]:
    """Return treatment rows where completion landed in the [22h, 26h] window
    AND no rating has been captured yet.

    Stored as ISO-8601 UTC strings in `completed_at`. We compare lexicographically
    because ISO-8601 sorts the same as time when zero-padded.
    """
    now = datetime.now(timezone.utc)
    cutoff_max = (now - timedelta(hours=WINDOW_HOURS_MIN)).isoformat()
    cutoff_min = (now - timedelta(hours=WINDOW_HOURS_MAX)).isoformat()
    rows = get_db().execute(
        "SELECT t.appointment_id, t.patient_id, a.patient_name, a.therapist_id "
        "FROM treatment_notes t "
        "JOIN appointments a ON a.id = t.appointment_id "
        "WHERE t.completed_at IS NOT NULL "
        "  AND t.completed_at >= ? AND t.completed_at <= ? "
        "  AND (t.followup_rating IS NULL OR t.followup_rating = 0)",
        (cutoff_min, cutoff_max),
    ).fetchall()
    return [dict(r) for r in rows]


def _save_rating(appointment_id: int, rating: int) -> None:
    get_db().execute(
        "UPDATE treatment_notes SET followup_rating=?, updated_at=datetime('now') "
        "WHERE appointment_id=?",
        (rating, appointment_id),
    )


def _stamp_sent(appointment_id: int) -> None:
    get_db().execute(
        "UPDATE treatment_notes SET followup_sent_at=datetime('now'), updated_at=datetime('now') "
        "WHERE appointment_id=?",
        (appointment_id,),
    )


# ── Redis sentinels ───────────────────────────────────────────────────────────

def _sent_key(appointment_id: int) -> str:
    return f"zenflow:followup:sent:{appointment_id}"


def _awaiting_key(patient_id: int) -> str:
    return f"zenflow:followup:awaiting:{patient_id}"


async def _already_sent(appointment_id: int) -> bool:
    r = get_async_redis()
    return bool(await r.get(_sent_key(appointment_id)))


async def _mark_sent(appointment_id: int, patient_id: int) -> None:
    r = get_async_redis()
    await r.set(_sent_key(appointment_id), "1", ex=SENT_TTL_SECONDS)
    # awaiting key encodes which appointment the next numeric reply should bind to
    await r.set(_awaiting_key(patient_id), str(appointment_id), ex=AWAITING_TTL_SECONDS)


# ── Sender ────────────────────────────────────────────────────────────────────

async def _send_followup(appt: dict) -> None:
    appt_id = int(appt["appointment_id"])
    if await _already_sent(appt_id):
        return
    # Lazy import: keeps `bot.services` import side-effect free
    from bot.interfaces import get_default_channel
    channel = get_default_channel()
    first_name = (appt.get("patient_name") or "there").split()[0]
    text = FOLLOWUP_TEMPLATE.format(name=first_name)
    try:
        await channel.send_text(recipient_id=appt["patient_id"], text=text)
    except Exception as e:
        logger.warning(f"follow-up send failed for appt={appt_id}: {e}")
        return
    await _mark_sent(appt_id, int(appt["patient_id"]))
    try:
        await asyncio.to_thread(_stamp_sent, appt_id)
    except Exception as e:
        logger.debug(f"follow-up DB stamp failed for appt={appt_id}: {e}")
    logger.info(f"follow-up sent: appt={appt_id} patient={appt['patient_id']}")


# ── Loop + entry point ────────────────────────────────────────────────────────

async def _scheduler_loop() -> None:
    logger.info(
        "follow-up scheduler started — poll every %ss, window %sh–%sh",
        POLL_INTERVAL_SECONDS, WINDOW_HOURS_MIN, WINDOW_HOURS_MAX,
    )
    while True:
        try:
            due = await asyncio.to_thread(_find_due_followups)
            if due:
                logger.info(f"{len(due)} appointment(s) due for 24h follow-up")
            for appt in due:
                await _send_followup(appt)
        except asyncio.CancelledError:
            logger.info("follow-up scheduler cancelled")
            raise
        except Exception as e:
            logger.error(f"follow-up scheduler iteration failed: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def start_followup_scheduler() -> asyncio.Task:
    """Kick off the loop as a background task. Caller keeps the reference alive."""
    return asyncio.create_task(_scheduler_loop(), name="zenflow-followup")


# ── Reply capture (called from start handler) ─────────────────────────────────

def _parse_rating(text: str) -> int | None:
    s = (text or "").strip()
    if not s or len(s) > 2:
        return None
    try:
        n = int(s)
    except ValueError:
        return None
    return n if 1 <= n <= 5 else None


async def consume_followup_rating(patient_id: int, text: str) -> bool:
    """If `patient_id` has an outstanding follow-up AND `text` parses as 1-5,
    persist the rating and clear the awaiting flag. Returns True when consumed.

    Returns False otherwise — caller should continue with normal handling.
    """
    try:
        rating = _parse_rating(text)
        if rating is None:
            return False
        r = get_async_redis()
        raw = await r.get(_awaiting_key(patient_id))
        if not raw:
            return False
        appointment_id = int(raw)
        await asyncio.to_thread(_save_rating, appointment_id, rating)
        await r.delete(_awaiting_key(patient_id))
        logger.info(f"follow-up rating captured: appt={appointment_id} patient={patient_id} rating={rating}")
        return True
    except Exception as e:
        logger.warning(f"consume_followup_rating failed: {e}")
        return False
