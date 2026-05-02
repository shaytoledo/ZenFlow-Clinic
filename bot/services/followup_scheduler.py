"""
bot/services/followup_scheduler.py
───────────────────────────────────
24h post-treatment follow-up — now a 3-step AI-guided conversation.

Step 1  →  Pain level 1–10
Step 2  →  Improvement rating 1–5
Step 3  →  Free-text notes for the therapist (or "skip")

When all steps complete the full conversation is stored in
`treatment_notes.followup_conversation` (JSON) so the web dashboard
can show it inside the treatment session view.

Legacy: the old single-rating flow used `zenflow:followup:awaiting:{id}`.
That key is still checked as a fallback so existing in-flight sessions
are not silently dropped on a rolling deploy.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from bot.db import get_db
from bot.redis_client import get_async_redis

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1800
WINDOW_HOURS_MIN = 22
WINDOW_HOURS_MAX = 26
SENT_TTL_SECONDS = 7 * 86400
CONV_TTL_SECONDS = 48 * 3600   # patient has 48h to finish the conversation

# ── Message templates ────────────────────────────────────────────────────────

_TEMPLATES = {
    "en": {
        "step1": (
            "🌿 Hi {name}! Following up on your acupuncture session yesterday.\n\n"
            "On a scale of *1–10*, how would you rate any pain or discomfort you have right now?\n"
            "_(1 = no pain at all · 10 = very severe)_"
        ),
        "step2": (
            "Thank you for sharing! 🙏\n\n"
            "Overall, how has your condition *changed* since the treatment?\n\n"
            "1 — Much worse\n"
            "2 — Slightly worse\n"
            "3 — About the same\n"
            "4 — Noticeably better\n"
            "5 — Much better"
        ),
        "step3": (
            "Great, noted! 📝\n\n"
            "Last question — is there anything specific you'd like to share with your therapist?\n"
            "Any symptoms, feelings, or questions? _(Reply *skip* to finish)_"
        ),
        "complete": (
            "🙏 Thank you for your feedback! Your therapist will review it before your next session.\n\n"
            "Take good care of yourself. See you soon! 🌿"
        ),
        "improvement_labels": {
            1: "Much worse", 2: "Slightly worse", 3: "About the same",
            4: "Noticeably better", 5: "Much better",
        },
        "err_pain": "Please reply with a number between *1* and *10*. 🙏",
        "err_improvement": "Please reply with a number between *1* and *5*. 🙏",
    },
    "he": {
        "step1": (
            "🌿 שלום {name}! מתעדכנים אחרי הטיפול אתמול.\n\n"
            "בסולם של *1–10*, איך תדרגי/ת את רמת הכאב כרגע?\n"
            "_(1 = ללא כאב · 10 = כאב חמור מאוד)_"
        ),
        "step2": (
            "תודה שחלקת/ת! 🙏\n\n"
            "בסך הכל, איך השתנה מצבך מאז הטיפול?\n\n"
            "1 — הורע מאוד\n"
            "2 — הורע מעט\n"
            "3 — ללא שינוי\n"
            "4 — השתפר בניכר\n"
            "5 — השתפר מאוד"
        ),
        "step3": (
            "מעולה, רשמתי! 📝\n\n"
            "שאלה אחרונה — יש משהו שתרצה/י לשתף את המטפל/ת?\n"
            "תסמינים, תחושות, שאלות? _(ענה/י *דלג* לסיום)_"
        ),
        "complete": (
            "🙏 תודה על המשוב! המטפל/ת יסקור/תסקור לפני הפגישה הבאה.\n\n"
            "תשמור/י על עצמך. להתראות! 🌿"
        ),
        "improvement_labels": {
            1: "הורע מאוד", 2: "הורע מעט", 3: "ללא שינוי",
            4: "השתפר בניכר", 5: "השתפר מאוד",
        },
        "err_pain": "אנא ענה/י במספר בין *1* ל־*10*. 🙏",
        "err_improvement": "אנא ענה/י במספר בין *1* ל־*5*. 🙏",
    },
}

# Keep legacy module-level names pointing to English for any outside callers
FOLLOWUP_STEP1    = _TEMPLATES["en"]["step1"]
FOLLOWUP_STEP2    = _TEMPLATES["en"]["step2"]
FOLLOWUP_STEP3    = _TEMPLATES["en"]["step3"]
FOLLOWUP_COMPLETE = _TEMPLATES["en"]["complete"]
IMPROVEMENT_LABELS = _TEMPLATES["en"]["improvement_labels"]


def _get_therapist_lang(therapist_id: str) -> str:
    """Return the stored language preference for a therapist ('en' or 'he')."""
    try:
        from bot.db import get_db
        row = get_db().execute(
            "SELECT language FROM therapists WHERE id=?", (therapist_id,)
        ).fetchone()
        return (dict(row).get("language") if row else None) or "en"
    except Exception:
        return "en"


def _tmpl(therapist_id: str) -> dict:
    lang = _get_therapist_lang(therapist_id)
    return _TEMPLATES.get(lang, _TEMPLATES["en"])


# ── DB helpers ────────────────────────────────────────────────────────────────

def _find_due_followups() -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff_max = (now - timedelta(hours=WINDOW_HOURS_MIN)).isoformat()
    cutoff_min = (now - timedelta(hours=WINDOW_HOURS_MAX)).isoformat()
    rows = get_db().execute(
        "SELECT t.appointment_id, t.patient_id, a.patient_name, a.therapist_id "
        "FROM treatment_notes t "
        "JOIN appointments a ON a.id = t.appointment_id "
        "WHERE t.completed_at IS NOT NULL "
        "  AND t.completed_at >= ? AND t.completed_at <= ? "
        "  AND (t.followup_rating IS NULL OR t.followup_rating = 0)"
        "  AND t.followup_conversation IS NULL",
        (cutoff_min, cutoff_max),
    ).fetchall()
    return [dict(r) for r in rows]


def _stamp_sent(appointment_id: int) -> None:
    get_db().execute(
        "UPDATE treatment_notes SET followup_sent_at=datetime('now'), updated_at=datetime('now') "
        "WHERE appointment_id=?",
        (appointment_id,),
    )


# ── Redis keys ────────────────────────────────────────────────────────────────

def _sent_key(appointment_id: int) -> str:
    return f"zenflow:followup:sent:{appointment_id}"


def _conv_key(patient_id: int) -> str:
    return f"zenflow:followup:conv:{patient_id}"


# Legacy key used by the old single-rating flow
def _legacy_awaiting_key(patient_id: int) -> str:
    return f"zenflow:followup:awaiting:{patient_id}"


async def _already_sent(appointment_id: int) -> bool:
    r = get_async_redis()
    return bool(await r.get(_sent_key(appointment_id)))


async def _mark_sent(appointment_id: int, patient_id: int) -> None:
    r = get_async_redis()
    await r.set(_sent_key(appointment_id), "1", ex=SENT_TTL_SECONDS)


# ── Conversation state ────────────────────────────────────────────────────────

async def _get_conv_state(patient_id: int) -> dict | None:
    r = get_async_redis()
    raw = await r.get(_conv_key(patient_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _set_conv_state(patient_id: int, state: dict) -> None:
    r = get_async_redis()
    await r.set(_conv_key(patient_id), json.dumps(state), ex=CONV_TTL_SECONDS)


async def _clear_conv_state(patient_id: int) -> None:
    r = get_async_redis()
    await r.delete(_conv_key(patient_id))
    await r.delete(_legacy_awaiting_key(patient_id))


# ── Sender ────────────────────────────────────────────────────────────────────

async def _send_followup(appt: dict) -> None:
    appt_id = int(appt["appointment_id"])
    if await _already_sent(appt_id):
        return

    from bot.interfaces import get_default_channel
    channel = get_default_channel()
    first_name = (appt.get("patient_name") or "there").split()[0]
    tmpl = _tmpl(appt.get("therapist_id", ""))
    text = tmpl["step1"].format(name=first_name)

    try:
        await channel.send_text(recipient_id=appt["patient_id"], text=text)
    except Exception as e:
        logger.warning(f"follow-up send failed for appt={appt_id}: {e}")
        return

    await _mark_sent(appt_id, int(appt["patient_id"]))

    # Initialise conversation state at step 1 (awaiting pain level)
    state = {
        "appointment_id": appt_id,
        "step": 1,
        "first_name": first_name,
        "therapist_id": appt.get("therapist_id", ""),
        "pain_level": None,
        "improvement_rating": None,
        "notes": None,
        "conversation": [{"role": "ai", "content": text}],
    }
    await _set_conv_state(int(appt["patient_id"]), state)

    try:
        await asyncio.to_thread(_stamp_sent, appt_id)
    except Exception as e:
        logger.debug(f"follow-up DB stamp failed for appt={appt_id}: {e}")

    logger.info(f"follow-up step-1 sent: appt={appt_id} patient={appt['patient_id']}")


# ── Pending recommendations dispatcher ───────────────────────────────────────

async def _dispatch_pending_recommendations() -> None:
    """Send any queued lifestyle recommendations whose send_at time has passed.

    Routing:
      - Telegram-source patient → patient bot.
      - Manual patient with email → SMTP fallback.
      - Manual patient without email → persistent "missing contact" notification.

    Each outcome creates a notification for the therapist.
    """
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        from web.repositories.treatment_repo import (
            list_due_pending_recommendations,
            clear_pending_recommendations,
        )
        due = await asyncio.to_thread(list_due_pending_recommendations, now_iso)
    except Exception as e:
        logger.error(f"list_due_pending_recommendations failed: {e}")
        return

    if not due:
        return

    from web.services import notification_service

    for row in due:
        apt_id      = row["appointment_id"]
        pat_id      = row["patient_id"]
        items       = row["pending_recommendations"]
        source      = row.get("source", "telegram")
        therapist_id = row.get("therapist_id", "") or ""
        patient_name = row.get("patient_name", "Patient") or "Patient"
        is_manual   = (source == "manual") or (pat_id < 0)

        # Look up email for manual patients
        patient_email = ""
        if is_manual:
            try:
                row2 = await asyncio.to_thread(
                    lambda: get_db().execute(
                        "SELECT patient_email FROM appointments WHERE id=?", (apt_id,)
                    ).fetchone()
                )
                patient_email = (dict(row2).get("patient_email") if row2 else "") or ""
            except Exception:
                patient_email = ""

        try:
            if is_manual and patient_email:
                # SMTP fallback
                from web.services.email_service import send_email, EmailNotConfigured
                lines = [
                    f"Hi {patient_name.split()[0] if patient_name else 'there'},",
                    "",
                    "Here are your post-treatment lifestyle recommendations:",
                    "",
                ]
                for item in items:
                    cat  = item.get("category", "")
                    text = item.get("text", "")
                    lines.append(f"• {cat}: {text}")
                lines += ["", "Take care, and see you at your next session.", "", "— ZenFlow Clinic"]
                body_text = "\n".join(lines)
                try:
                    await asyncio.to_thread(
                        send_email,
                        patient_email,
                        "Your post-treatment recommendations — ZenFlow Clinic",
                        body_text,
                    )
                    await asyncio.to_thread(
                        notification_service.alert_recommendations_sent,
                        therapist_id, apt_id, pat_id, patient_name, "email", patient_email,
                    )
                    logger.info(f"pending recommendations EMAILED: appt={apt_id} → {patient_email}")
                except EmailNotConfigured:
                    await asyncio.to_thread(
                        notification_service.alert_send_failed,
                        therapist_id, apt_id, pat_id, patient_name,
                        "SMTP not configured — set SMTP_* env vars to enable email fallback.",
                    )
                    # Don't clear queue — let the therapist fix SMTP and retry on next pass
                    continue

            elif is_manual and not patient_email:
                # Persistent alert — no contact info
                await asyncio.to_thread(
                    notification_service.alert_missing_contact,
                    therapist_id, apt_id, pat_id, patient_name,
                )
                logger.info(f"pending recs for manual patient (appt={apt_id}) — no email/Telegram, alert raised")

            else:
                # Telegram patient
                from bot.interfaces import get_default_channel
                channel = get_default_channel()
                icon_map = {"Diet": "🥗", "Sleep": "🌙", "Exercise": "🏃", "Movement": "🚶", "Stress": "🧘"}
                lines = ["*Your post-treatment lifestyle recommendations from ZenFlow Clinic:*\n"]
                for item in items:
                    cat  = item.get("category", "")
                    text = item.get("text", "")
                    icon = item.get("icon") or icon_map.get(cat, "•")
                    lines.append(f"{icon} *{cat}:* {text}")
                lines.append("\n_Take care and see you at your next session! 🌿_")
                message = "\n".join(lines)
                await channel.send_text(recipient_id=pat_id, text=message)
                await asyncio.to_thread(
                    notification_service.alert_recommendations_sent,
                    therapist_id, apt_id, pat_id, patient_name, "telegram", str(pat_id),
                )
                logger.info(f"pending recommendations sent: appt={apt_id} patient={pat_id}")

            # Clear the queue entry on success / handled-alert
            await asyncio.to_thread(clear_pending_recommendations, apt_id)

        except Exception as e:
            logger.error(f"dispatch_pending_recommendations failed for appt={apt_id}: {e}")
            try:
                await asyncio.to_thread(
                    notification_service.alert_send_failed,
                    therapist_id, apt_id, pat_id, patient_name, str(e),
                )
            except Exception:
                pass


# ── Loop ──────────────────────────────────────────────────────────────────────

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
            # Also dispatch any queued lifestyle recommendations
            await _dispatch_pending_recommendations()
        except asyncio.CancelledError:
            logger.info("follow-up scheduler cancelled")
            raise
        except Exception as e:
            logger.error(f"follow-up scheduler iteration failed: {e}")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def start_followup_scheduler() -> asyncio.Task:
    return asyncio.create_task(_scheduler_loop(), name="zenflow-followup")


# ── Conversation handler (called from bot/patient_bot/start.py) ───────────────

async def consume_followup_conversation(patient_id: int, text: str) -> tuple[bool, str | None]:
    """Handle an incoming patient message as part of the follow-up conversation.

    Returns (consumed, reply_text):
      - (True, reply)  — message was part of the follow-up; send `reply` to patient
      - (True, None)   — conversation complete; caller should send a thank-you
      - (False, None)  — not part of a follow-up; caller continues normal flow
    """
    try:
        state = await _get_conv_state(patient_id)

        # Legacy single-rating flow compatibility
        if state is None:
            r = get_async_redis()
            legacy_raw = await r.get(_legacy_awaiting_key(patient_id))
            if legacy_raw:
                try:
                    n = int((text or "").strip())
                    if 1 <= n <= 5:
                        apt_id = int(legacy_raw)
                        from bot.db import get_db
                        await asyncio.to_thread(
                            lambda: get_db().execute(
                                "UPDATE treatment_notes SET followup_rating=?, updated_at=datetime('now') WHERE appointment_id=?",
                                (n, apt_id),
                            )
                        )
                        await r.delete(_legacy_awaiting_key(patient_id))
                        return (True, None)
                except (ValueError, TypeError):
                    pass
            return (False, None)

        step = state.get("step", 1)
        txt = (text or "").strip()
        conv = state.get("conversation", [])
        conv.append({"role": "user", "content": txt})
        tmpl = _tmpl(state.get("therapist_id", ""))

        if step == 1:
            # Expecting pain level 1–10
            try:
                n = int(txt)
                if 1 <= n <= 10:
                    state["pain_level"] = n
                    state["step"] = 2
                    state["conversation"] = conv + [{"role": "ai", "content": tmpl["step2"]}]
                    await _set_conv_state(patient_id, state)
                    return (True, tmpl["step2"])
                else:
                    return (True, tmpl["err_pain"])
            except (ValueError, TypeError):
                return (True, tmpl["err_pain"])

        elif step == 2:
            # Expecting improvement 1–5
            try:
                n = int(txt)
                if 1 <= n <= 5:
                    state["improvement_rating"] = n
                    state["step"] = 3
                    state["conversation"] = conv + [{"role": "ai", "content": tmpl["step3"]}]
                    await _set_conv_state(patient_id, state)
                    return (True, tmpl["step3"])
                else:
                    return (True, tmpl["err_improvement"])
            except (ValueError, TypeError):
                return (True, tmpl["err_improvement"])

        elif step == 3:
            # Expecting free-text notes or "skip" / "דלג"
            skip_words = {"skip", "s", "no", "none", "-", "דלג", "לא"}
            notes = None if txt.lower() in skip_words else txt
            state["notes"] = notes
            state["step"] = 4  # done
            improvement_label = tmpl["improvement_labels"].get(state.get("improvement_rating"), "")
            state["improvement_label"] = improvement_label

            conv_final = conv + [{"role": "ai", "content": tmpl["complete"]}]
            state["conversation"] = conv_final

            # Persist to DB
            apt_id = state["appointment_id"]
            save_data = {
                "pain_level": state.get("pain_level"),
                "improvement_rating": state.get("improvement_rating"),
                "improvement_label": improvement_label,
                "notes": notes,
                "conversation": conv_final,
            }
            try:
                from web.repositories.treatment_repo import save_followup_conversation
                await asyncio.to_thread(save_followup_conversation, apt_id, save_data)
            except Exception as e:
                logger.error(f"save_followup_conversation failed: {e}")
                # Fallback: at least save the simple rating
                try:
                    if state.get("improvement_rating"):
                        from bot.db import get_db
                        await asyncio.to_thread(
                            lambda: get_db().execute(
                                "UPDATE treatment_notes SET followup_rating=?, updated_at=datetime('now') WHERE appointment_id=?",
                                (state["improvement_rating"], apt_id),
                            )
                        )
                except Exception:
                    pass

            await _clear_conv_state(patient_id)
            logger.info(
                f"follow-up complete: appt={apt_id} patient={patient_id} "
                f"pain={state.get('pain_level')} improvement={state.get('improvement_rating')}"
            )
            return (True, tmpl["complete"])

        else:
            # Conversation already done or in unknown state — clear and fall through
            await _clear_conv_state(patient_id)
            return (False, None)

    except Exception as e:
        logger.warning(f"consume_followup_conversation failed: {e}")
        return (False, None)


# Keep the old function name as a thin wrapper so any other callers still work
async def consume_followup_rating(patient_id: int, text: str) -> bool:
    consumed, _reply = await consume_followup_conversation(patient_id, text)
    return consumed
