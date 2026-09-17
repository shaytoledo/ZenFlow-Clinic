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
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from bot.db import get_db
from bot.redis_client import get_async_redis
from bot.services import followup_checkin as checkin
from zenflow import clock
from zenflow.worker import JobDeferred

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 1800
SENT_TTL_SECONDS = 7 * 86400


def _get_therapist_lang(therapist_id: str) -> str:
    """Return the stored language preference for a therapist ('en' or 'he')."""
    try:
        from bot.db import get_db

        row = (
            get_db()
            .execute("SELECT language FROM therapists WHERE id=?", (therapist_id,))
            .fetchone()
        )
        return (dict(row).get("language") if row else None) or "en"
    except Exception:
        return "en"


# ── DB helpers ────────────────────────────────────────────────────────────────


def _stamp_sent(appointment_id: int) -> None:
    get_db().execute(
        "UPDATE treatment_notes SET followup_sent_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'), updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
        "WHERE appointment_id=?",
        (appointment_id,),
    )


# ── Redis keys ────────────────────────────────────────────────────────────────


def _sent_key(appointment_id: int) -> str:
    return f"zenflow:followup:sent:{appointment_id}"


def _conv_key(sender_id: int | str) -> str:
    return f"zenflow:followup:conv:{sender_id}"


# Legacy key used by the old single-rating flow
def _legacy_awaiting_key(sender_id: int | str) -> str:
    return f"zenflow:followup:awaiting:{sender_id}"


async def _already_sent(appointment_id: int) -> bool:
    r = get_async_redis()
    return bool(await r.get(_sent_key(appointment_id)))


async def _mark_sent(appointment_id: int, patient_id: int) -> None:
    r = get_async_redis()
    await r.set(_sent_key(appointment_id), "1", ex=SENT_TTL_SECONDS)


# ── Conversation state ────────────────────────────────────────────────────────


async def _get_conv_state(patient_id: int | str) -> dict | None:
    r = get_async_redis()
    raw = await r.get(_conv_key(patient_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def _clear_conv_state(patient_id: int | str) -> None:
    r = get_async_redis()
    await r.delete(_conv_key(patient_id))
    await r.delete(_legacy_awaiting_key(patient_id))


# ── Who to message ────────────────────────────────────────────────────────────


def _contact(row: dict) -> Any:
    """The patient's messaging contact (`patient_repo.Contact`), or None when they have none.

    Candidate rows carry it (`contact_channel` / `contact_id`); anything else is looked up.
    """
    from web.repositories import patient_repo

    if "contact_id" in row:
        if not row.get("contact_id") or not row.get("contact_channel"):
            return None
        return patient_repo.Contact(str(row["contact_channel"]), str(row["contact_id"]))
    return patient_repo.messaging_contact(int(row["patient_id"]))


async def _send_template(channel: Any, contact: Any, appt: dict, name: str, lang: str) -> Any:
    """The pre-approved stand-in for step 1 when free-form text is not allowed (7.4)."""
    from bot.interfaces.channel import ChannelError, Template
    from zenflow.settings import get_settings

    template = get_settings().whatsapp_template_followup
    if not template:
        raise ChannelError(
            "the service window is closed and no check-in template is configured",
            permanent=True,
            code="no_template",
        )
    logger.info(
        "follow-up for appt=%s sent as template %s (the service window is closed)",
        appt.get("appointment_id"),
        template,
    )
    return await channel.send_template(
        contact.external_id, Template(name=template, language=lang, params=(name,))
    )


def _open_checkin(channel: str, sender_id: int | str) -> dict | None:
    """The open check-in of the patient behind a channel identity, if any."""
    from web.repositories import followup_repo, patient_repo

    try:
        patient_id = patient_repo.find_by_channel(channel, sender_id)
    except ValueError:  # not a valid channel identity
        return None
    return None if patient_id is None else followup_repo.open_for_patient(patient_id)


# ── Sender ────────────────────────────────────────────────────────────────────


async def _send_followup(appt: dict, *, raise_errors: bool = False) -> None:
    """Send step 1 and open the conversation. With raise_errors=True (job handler) a delivery
    failure propagates so the job retries instead of being silently dropped."""
    appt_id = int(appt["appointment_id"])
    patient_id = int(appt["patient_id"])
    redis_sent = False
    with contextlib.suppress(Exception):  # Redis is a secondary guard; never block on it
        redis_sent = await _already_sent(appt_id)
    if redis_sent:
        if raise_errors:
            # An earlier attempt sent but could not stamp the database: repair, don't re-send.
            await asyncio.to_thread(_stamp_sent, appt_id)
        return

    from bot.interfaces import get_channel
    from bot.interfaces.channel import ChannelError, OutboundMessage

    contact = await asyncio.to_thread(_contact, appt)
    if contact is None:
        logger.info(f"follow-up not sent: appt={appt_id} has no messaging channel")
        return
    channel = get_channel(contact.channel)
    first_name = (appt.get("patient_name") or "there").split()[0]
    lang = await asyncio.to_thread(_get_therapist_lang, appt.get("therapist_id", ""))
    prompt = checkin.question(1, appt_id, lang, name=first_name)
    text = prompt.text

    try:
        sent = await channel.send(
            OutboundMessage(
                recipient_id=contact.external_id,
                text=text,
                extra={"buttons": prompt.buttons},
            )
        )
    except ChannelError as e:
        if e.code != "outside_session_window":
            raise
        # The check-in falls due as WhatsApp's 24-hour window closes: only a template gets in.
        sent = await _send_template(channel, contact, appt, first_name, lang)
    except Exception as e:
        logger.warning(f"follow-up send failed for appt={appt_id}: {e}")
        await _best_effort(log_delivery, contact.channel, "followup", appt, None, str(e))
        if raise_errors:
            raise
        return

    # Durable stamp FIRST (review fix): after a successful send nothing may make a retry resend.
    try:
        await asyncio.to_thread(_stamp_sent, appt_id)
    except Exception as e:
        logger.error(f"follow-up DB stamp failed for appt={appt_id}: {e}")
        with contextlib.suppress(Exception):  # lets the retry repair the stamp instead of resending
            await _mark_sent(appt_id, patient_id)
        if raise_errors:
            raise
        return
    with contextlib.suppress(Exception):
        await _mark_sent(appt_id, patient_id)
    await _best_effort(log_delivery, contact.channel, "followup", appt, sent)

    # Open the conversation at step 1 (awaiting pain level) — in the database (Phase 6.3)
    from web.repositories import followup_repo

    try:
        await asyncio.to_thread(
            followup_repo.mark_sent, appt_id, [{"role": "ai", "content": text}], 1
        )
    except Exception as e:  # message already delivered; answers fall back to the normal flow
        logger.error(f"follow-up conversation not opened for appt={appt_id}: {e}")

    logger.info(f"follow-up step-1 sent: appt={appt_id} patient={appt['patient_id']}")


# ── Pending recommendations dispatcher ───────────────────────────────────────


_ICON_MAP = {"Diet": "🥗", "Sleep": "🌙", "Exercise": "🏃", "Movement": "🚶", "Stress": "🧘"}
_EMAIL_SUBJECT = "Your post-treatment recommendations — ZenFlow Clinic"


def _recommendations_email_body(patient_name: str, items: list[dict]) -> str:
    first = patient_name.split()[0] if patient_name else "there"
    lines = [f"Hi {first},", "", "Here are your post-treatment lifestyle recommendations:", ""]
    for item in items:
        lines.append(f"• {item.get('category', '')}: {item.get('text', '')}")
    lines += ["", "Take care, and see you at your next session.", "", "— ZenFlow Clinic"]
    return "\n".join(lines)


def _recommendations_telegram_text(items: list[dict]) -> str:
    lines = ["*Your post-treatment lifestyle recommendations from ZenFlow Clinic:*\n"]
    for item in items:
        cat = item.get("category", "")
        icon = item.get("icon") or _ICON_MAP.get(cat, "•")
        lines.append(f"{icon} *{cat}:* {item.get('text', '')}")
    lines.append("\n_Take care and see you at your next session! 🌿_")
    return "\n".join(lines)


async def dispatch_recommendations(row: dict) -> None:
    """Deliver one appointment's queued recommendations (job handler body, Phase 1.3).

    Routing:
      - Telegram patient                 → patient bot; queue entry cleared.
      - Manual patient with email        → therapist's Gmail; queue entry cleared.
          * Google not connected          → one "waiting for Google" alert; the job is DEFERRED
                                            (entry kept, no attempt charged) until the therapist
                                            connects — `resume_after_google_connected()` — or
                                            the next recheck (Phase 5.4).
          * token refused / revoked       → send_email raised one reconnect alert; the job
                                            retries, then dead-letters with one "send failed".
      - No messaging channel, no email   → persistent "missing contact" alert; entry cleared.
    Any other failure raises so the job retries; the therapist is alerted on the final attempt.
    """
    from web.repositories.treatment_repo import (
        clear_pending_recommendations,
        mark_recommendations_delivered,
    )
    from web.services import notification_service

    apt_id = int(row["appointment_id"])
    pat_id = int(row["patient_id"])
    items = row.get("pending_recommendations") or []
    therapist_id = row.get("therapist_id", "") or ""
    patient_name = row.get("patient_name", "Patient") or "Patient"
    contact = await asyncio.to_thread(_contact, row)
    patient_email = (row.get("patient_email") or "").strip()
    attempt = ""  # the channel being tried, for the delivery log (Phase 6.6)

    try:
        if contact is None and patient_email:
            from web.services import email_service

            try:
                attempt = "email"
                # F1 fix: the therapist id comes first — send_email(therapist_id, to, subject, body).
                sent = await asyncio.to_thread(
                    email_service.send_email,
                    therapist_id,
                    patient_email,
                    _EMAIL_SUBJECT,
                    _recommendations_email_body(patient_name, items),
                )
            except email_service.EmailNotConfigured as e:
                if e.reason == email_service.TOKEN_INVALID:
                    raise  # send_email asked for a reconnect; retry, then dead-letter
                from bot.services.followup_jobs import GOOGLE_RECHECK_HOURS

                await _best_effort(
                    notification_service.alert_waiting_for_google,
                    therapist_id,
                    apt_id,
                    pat_id,
                    patient_name,
                )
                logger.warning(f"recommendations wait for Google to be connected: appt={apt_id}")
                raise JobDeferred(
                    clock.hours_ahead(GOOGLE_RECHECK_HOURS),
                    "waiting for the therapist to connect Google",
                ) from e
            await asyncio.to_thread(mark_recommendations_delivered, apt_id)
            await _best_effort(log_delivery, "email", "recommendations", row, sent)
            await _best_effort(
                notification_service.resolve_waiting_for_google, therapist_id, apt_id
            )
            await _best_effort(
                notification_service.alert_recommendations_sent,
                therapist_id,
                apt_id,
                pat_id,
                patient_name,
                "email",
                patient_email,
            )
            logger.info(f"pending recommendations EMAILED: appt={apt_id}")

        elif contact is None:
            await asyncio.to_thread(
                notification_service.alert_missing_contact,
                therapist_id,
                apt_id,
                pat_id,
                patient_name,
            )
            await asyncio.to_thread(clear_pending_recommendations, apt_id)
            logger.info(f"pending recs for appt={apt_id}: the patient has no contact, alert raised")

        else:
            from bot.interfaces import get_channel

            attempt = contact.channel
            sent = await get_channel(contact.channel).send_text(
                contact.external_id, _recommendations_telegram_text(items), markdown=True
            )
            await asyncio.to_thread(mark_recommendations_delivered, apt_id)
            await _best_effort(log_delivery, contact.channel, "recommendations", row, sent)
            await _best_effort(
                notification_service.alert_recommendations_sent,
                therapist_id,
                apt_id,
                pat_id,
                patient_name,
                contact.channel,
                contact.external_id,
            )
            logger.info(f"pending recommendations sent: appt={apt_id} patient={pat_id}")

    except JobDeferred:
        raise  # waiting, not failing
    except Exception as e:
        # Retried by the queue; the therapist is alerted once, when retries are exhausted
        # (followup_jobs.recommendations_dead — covers timeouts too).
        logger.error(f"dispatch_recommendations failed for appt={apt_id}: {e}")
        if attempt:
            await _best_effort(log_delivery, attempt, "recommendations", row, None, str(e))
        raise


def log_delivery(
    channel: str,
    kind: str,
    row: dict,
    result: Any = None,
    error: str | None = None,
) -> None:
    """One `message_log` row for an outbound patient message (Phase 6.6, plan 8.3)."""
    from web.repositories import message_log_repo

    message_log_repo.record(
        channel=channel,
        kind=kind,
        status="failed" if error else "sent",
        therapist_id=str(row.get("therapist_id") or ""),
        patient_id=int(row["patient_id"]) if row.get("patient_id") is not None else None,
        appointment_id=int(row["appointment_id"]) if row.get("appointment_id") else None,
        provider_message_id=None if error else message_log_repo.provider_id(result),
        error=error,
    )


async def _best_effort(fn: Any, *args: Any) -> None:
    """A notification after a delivered message must never trigger a retry (= a duplicate send)."""
    try:
        await asyncio.to_thread(fn, *args)
    except Exception as e:
        logger.error(
            f"notification {getattr(fn, '__name__', fn)} failed (message was delivered): {e}"
        )


# ── Reconciliation sweep (safety net; the primary path enqueues at completion time) ──────────

RECONCILE_INTERVAL_SECONDS = POLL_INTERVAL_SECONDS


def reconcile() -> dict[str, int]:
    """Enqueue jobs for sessions completed or recommendations queued without one (rows written
    before Phase 1.3, or an enqueue that failed). Idempotency keys make this safe to repeat."""
    from bot.services import followup_jobs as fj
    from web.repositories import treatment_repo

    followups = recommendations = errors = 0
    # Look back as far as a follow-up may still be sent (the handler's expiry), not just 26h.
    for row in treatment_repo.list_recent_completions_without_followup(
        clock.hours_ago(fj.FOLLOWUP_EXPIRE_HOURS)
    ):
        try:
            fj.enqueue_followup(int(row["appointment_id"]), str(row["completed_at"]))
            followups += 1
        except Exception as e:  # one bad row must not abort the sweep
            errors += 1
            logger.error(
                f"reconcile: follow-up enqueue failed for appt={row['appointment_id']}: {e}"
            )
    for row in treatment_repo.list_all_pending_recommendations():
        try:
            fj.enqueue_recommendations(int(row["appointment_id"]), str(row["pending_rec_send_at"]))
            recommendations += 1
        except Exception as e:
            errors += 1
            logger.error(
                f"reconcile: recommendations enqueue failed for appt={row['appointment_id']}: {e}"
            )
    auto = 0
    from zenflow.settings import get_settings

    if get_settings().flags.auto_followup:  # Q7, off until the owner decides
        auto, auto_errors = _reconcile_auto_followups(fj)
        errors += auto_errors
    try:
        from web.repositories import followup_repo

        expired = followup_repo.expire_stale()
    except Exception as e:
        errors += 1
        expired = 0
        logger.error(f"reconcile: expiring unanswered follow-ups failed: {e}")
    return {
        "followups": followups,
        "recommendations": recommendations,
        "expired": expired,
        "auto": auto,
        "errors": errors,
    }


def _reconcile_auto_followups(fj: Any) -> tuple[int, int]:
    """Enqueue the automatic check-in of every session that ended within the send window and
    was never completed (ZF_AUTO_FOLLOWUP). Idempotent: one job per appointment."""
    from datetime import timedelta

    from web.repositories import treatment_repo

    now = clock.now_utc()
    earliest = now - timedelta(hours=fj.FOLLOWUP_EXPIRE_HOURS)
    first_date = (earliest - timedelta(days=1)).astimezone(clock.clinic_tz()).date().isoformat()
    enqueued = errors = 0
    for row in treatment_repo.list_uncompleted_since(first_date):
        try:
            ended = fj.appointment_end(str(row["date"]), str(row["time"]))
            if earliest <= ended <= now:
                fj.enqueue_auto_followup(int(row["appointment_id"]), clock.to_iso(ended))
                enqueued += 1
        except Exception as e:  # one odd row (a bad time string) must not stop the sweep
            errors += 1
            logger.error(
                f"reconcile: automatic follow-up failed for appt={row['appointment_id']}: {e}"
            )
    return enqueued, errors


async def _scheduler_loop() -> None:
    from zenflow import logging as zlog

    logger.info("follow-up reconciliation started — every %ss", RECONCILE_INTERVAL_SECONDS)
    while True:
        with zlog.log_context(request_id=f"job-reconcile-{zlog.new_request_id()}"):
            try:
                counts = await asyncio.to_thread(reconcile)
                if any(counts.values()):
                    logger.info("reconciliation checked %s", counts)
            except asyncio.CancelledError:
                logger.info("follow-up reconciliation cancelled")
                raise
            except Exception as e:
                logger.error(f"follow-up reconciliation failed: {e}")
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)


def start_followup_scheduler() -> asyncio.Task:
    return asyncio.create_task(_scheduler_loop(), name="zenflow-followup")


# ── Conversation handler (called from bot/patient_bot/start.py) ───────────────


async def consume_followup_conversation(
    sender_id: int | str, text: str, channel: str = "telegram"
) -> tuple[bool, Any]:
    """Handle a typed patient message as part of the check-in (Phase 6.2).

    `sender_id` is the channel identity (a Telegram user id); the patient behind it is looked up
    in `patient_channels` (Phase 7.2).

    The open check-in and its answers live in the `followups` table (Phase 6.3). A conversation
    opened in Redis before 6.3 is adopted into the table on its next answer.

    Returns (consumed, prompt): `prompt` is the `followup_checkin.Prompt` to send back (the next
    question, an error with the same buttons, or the thank-you); (False, None) means the message
    is not part of a check-in and the normal flow continues.
    """

    try:
        state = await asyncio.to_thread(_open_checkin, channel, sender_id)
        if state is None:
            state = await _adopt_legacy_conversation(sender_id)
        if state is None:
            consumed = await _consume_legacy_rating(sender_id, text)
            return (consumed, checkin.completion("en") if consumed else None)

        lang = await asyncio.to_thread(_get_therapist_lang, state.get("therapist_id", ""))
        step = int(state.get("step") or 1)
        understood, value = checkin.parse_text(step, text, lang)
        if not understood:
            again = checkin.question(
                step, int(state["appointment_id"]), lang, selected=_chosen(state)
            )
            return (True, checkin.Prompt(checkin.error(step, lang), again.buttons))
        return (True, await _take_answer(state, step, value, (text or "").strip(), lang))

    except Exception as e:
        logger.warning(f"consume_followup_conversation failed: {e}")
        return (False, None)


@dataclass
class ButtonResult:
    """What the Telegram layer does with a tapped check-in button."""

    consumed: bool
    toast: str = ""
    #: new buttons for the tapped message (a toggle)
    keep_buttons: list | None = None
    #: the tapped question is answered or out of date: take its buttons away
    remove_buttons: bool = False
    prompt: Any = None


async def consume_followup_button(
    sender_id: int | str, data: str, channel: str = "telegram"
) -> ButtonResult:
    """Handle a tapped check-in button (`fu:<appointment>:<step>:<value>`) from `sender_id`."""
    from web.repositories import followup_repo

    parsed = checkin.parse_callback(data)
    if parsed is None:
        return ButtonResult(consumed=False)
    apt_id, step, value = parsed
    try:
        state = await asyncio.to_thread(_open_checkin, channel, sender_id)
        lang = "en"
        if state is not None:
            lang = await asyncio.to_thread(_get_therapist_lang, state.get("therapist_id", ""))
        current = int(state.get("step") or 1) if state else None
        if state is None or int(state["appointment_id"]) != apt_id or current != step:
            return ButtonResult(consumed=True, toast=checkin.stale(lang), remove_buttons=True)

        if step == 3 and value.startswith("t:"):  # a side effect switched on or off
            effect = value[2:]
            if effect not in checkin.SIDE_EFFECTS or effect == "none":
                return ButtonResult(consumed=True, toast=checkin.error(step, lang))
            chosen = set(_chosen(state)) ^ {effect}
            ordered = [e for e in checkin.SIDE_EFFECTS if e in chosen]
            await asyncio.to_thread(
                followup_repo.save_progress,
                apt_id,
                step=3,
                conversation=list(state.get("conversation") or []),
                answers={"side_effects": ordered},
            )
            again = checkin.question(3, apt_id, lang, selected=ordered)
            return ButtonResult(consumed=True, keep_buttons=again.buttons)

        if step == 3 and value == "done":
            understood, answer_value = True, list(_chosen(state))
        else:
            understood, answer_value = checkin.parse_button(step, value)
        if not understood:
            return ButtonResult(consumed=True, toast=checkin.error(step, lang))
        typed = checkin.label(step, answer_value, lang)
        prompt = await _take_answer(state, step, answer_value, typed, lang)
        return ButtonResult(consumed=True, prompt=prompt, remove_buttons=True)
    except Exception as e:  # keep the question's buttons so the patient can tap again
        logger.warning(f"consume_followup_button failed: {e}")
        return ButtonResult(consumed=True)


def _chosen(state: dict) -> list[str]:
    return [e for e in (state.get("side_effects") or []) if e in checkin.SIDE_EFFECTS]


def _audit_answer(state: dict, answers: dict) -> None:
    """The patient answered a check-in question (8.1) — recorded in their own name."""
    from web.services import audit

    with audit.acting_as("patient", state.get("patient_id", "")):
        audit.record("followup.answered", "followup", int(state["appointment_id"]), after=answers)


async def _take_answer(state: dict, step: int, value: Any, shown: str, lang: str) -> Any:
    """Store one answer, apply the red-flag rule, and return the next prompt."""
    from web.repositories import followup_repo

    apt_id = int(state["appointment_id"])
    answers, next_step = checkin.answer(step, value)
    conversation = list(state.get("conversation") or [])
    conversation.append({"role": "user", "content": shown})
    merged = {**{k: state.get(k) for k in _ANSWER_KEYS}, **answers}

    await asyncio.to_thread(_audit_answer, state, answers)

    flags = checkin.red_flags(merged)
    if flags and not state.get("needs_attention"):
        await _raise_red_flag(state, flags)

    if next_step is None:
        return await _finish_checkin(state, conversation, answers, merged, bool(flags), lang)

    prompt = checkin.question(next_step, apt_id, lang)
    conversation.append({"role": "ai", "content": prompt.text})
    await asyncio.to_thread(
        followup_repo.save_progress,
        apt_id,
        step=next_step,
        conversation=conversation,
        answers=answers,
    )
    return prompt


_ANSWER_KEYS = (
    "pain_level",
    "improvement_rating",
    "side_effects",
    "sleep_quality",
    "adherence",
    "free_text",
)


async def _raise_red_flag(state: dict, reasons: list[str]) -> None:
    """Safety first (plan 6.2): tell the therapist now, not when the check-in ends."""
    from web.repositories import followup_repo
    from web.services import notification_service

    apt_id = int(state["appointment_id"])
    await asyncio.to_thread(followup_repo.flag_attention, apt_id)
    state["needs_attention"] = True
    name = await asyncio.to_thread(_patient_name, apt_id)
    await _best_effort(
        notification_service.alert_followup_red_flag,
        state.get("therapist_id", "") or "",
        apt_id,
        int(state["patient_id"]),
        name,
        reasons,
    )
    logger.warning(f"follow-up red flag: appt={apt_id} reasons={reasons}")


def _patient_name(appointment_id: int) -> str:
    row = (
        get_db()
        .execute("SELECT patient_name FROM appointments WHERE id=?", (appointment_id,))
        .fetchone()
    )
    return (row["patient_name"] if row else "") or "Patient"


async def _finish_checkin(
    state: dict,
    conversation: list[dict],
    answers: dict[str, Any],
    merged: dict[str, Any],
    needs_attention: bool,
    lang: str,
) -> Any:
    from web.repositories import followup_repo

    apt_id = int(state["appointment_id"])
    patient_id = int(state["patient_id"])
    done = checkin.completion(lang)
    conversation.append({"role": "ai", "content": done.text})
    text_summary = await summarize_checkin(merged, lang)
    await asyncio.to_thread(
        followup_repo.complete,
        apt_id,
        conversation=conversation,
        answers=answers,
        needs_attention=needs_attention,
        ai_summary=text_summary,
    )
    # Parallel write for one release (plan 6.3): pages still read treatment_notes.
    improvement = merged.get("improvement_rating")
    save_data = {
        "pain_level": merged.get("pain_level"),
        "improvement_rating": improvement,
        "improvement_label": (
            checkin.TEXT[checkin.lang_of(lang)]["improvement"].get(improvement, "")
            if improvement
            else ""
        ),
        "side_effects": merged.get("side_effects") or [],
        "sleep_quality": merged.get("sleep_quality"),
        "adherence": merged.get("adherence"),
        "notes": merged.get("free_text"),
        "summary": text_summary,
        "needs_attention": needs_attention,
        "conversation": conversation,
    }
    try:
        from web.repositories.treatment_repo import save_followup_conversation

        await asyncio.to_thread(save_followup_conversation, apt_id, save_data)
    except Exception as e:  # the followups row already holds the answers
        logger.error(f"save_followup_conversation failed: {e}")
    with contextlib.suppress(Exception):
        await _clear_conv_state(patient_id)
    logger.info(
        f"follow-up complete: appt={apt_id} patient={patient_id} "
        f"pain={merged.get('pain_level')} improvement={improvement} attention={needs_attention}"
    )
    return done


#: the AI may word the summary, never add to it; this bounds how long a check-in waits for it
SUMMARY_TIMEOUT_SECONDS = 20


async def summarize_checkin(answers: dict[str, Any], lang: str) -> str:
    """A two-line summary for the therapist: the AI's wording when it is quick and well-formed,
    otherwise the fixed one. The AI only sees the answers."""
    fallback = checkin.summary(answers, lang)
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from bot.patient_bot.services import ai_intake

        model = ai_intake._LLM
        if model is None:
            return fallback
        language = "Hebrew" if checkin.lang_of(lang) == "he" else "English"
        instructions = (
            "You write a two-line clinical summary of a patient's 24-hour check-in after "
            f"acupuncture, in {language}, for their therapist. Use only the facts below. Do not "
            "add symptoms, scores, diagnoses or advice. At most two lines, under 250 characters."
        )
        response = await asyncio.wait_for(
            model.ainvoke([SystemMessage(content=instructions), HumanMessage(content=fallback)]),
            timeout=SUMMARY_TIMEOUT_SECONDS,
        )
        text = str(getattr(response, "content", "") or "").strip()
    except Exception as e:
        logger.info(f"check-in summary uses the fixed wording ({type(e).__name__})")
        return fallback
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 2 or len(text) > 300:
        return fallback
    return "\n".join(lines)


async def _adopt_legacy_conversation(sender_id: int | str) -> dict | None:
    """A check-in opened in Redis before Phase 6.3 (keyed by the Telegram user id): copy it into
    its `followups` row."""
    from web.repositories import followup_repo

    legacy = None
    with contextlib.suppress(Exception):
        legacy = await _get_conv_state(sender_id)
    if not legacy or not legacy.get("appointment_id"):
        return None
    apt_id = int(legacy["appointment_id"])
    answers = {
        key: legacy[key]
        for key in ("pain_level", "improvement_rating")
        if legacy.get(key) is not None
    }
    # The old script asked pain, improvement, notes: its step 3 is the new step 6.
    step = {1: 1, 2: 2, 3: 6}.get(int(legacy.get("step") or 1), 1)

    def _adopt() -> dict | None:
        followup_repo.schedule(apt_id, clock.iso_now())
        followup_repo.mark_sent(apt_id, list(legacy.get("conversation") or []))
        followup_repo.save_progress(
            apt_id,
            step=step,
            conversation=list(legacy.get("conversation") or []),
            answers=answers,
        )
        row = followup_repo.get(apt_id)
        return row if row and row["status"] in followup_repo.OPEN_STATUSES else None

    adopted = await asyncio.to_thread(_adopt)
    with contextlib.suppress(Exception):
        await _clear_conv_state(sender_id)
    if adopted:
        logger.info(f"legacy follow-up conversation adopted: appt={apt_id}")
    return adopted


async def _consume_legacy_rating(sender_id: int | str, text: str) -> bool:
    """The single-rating flow that predates the conversation: a 1–5 reply to an old prompt."""
    r = get_async_redis()
    legacy_raw = await r.get(_legacy_awaiting_key(sender_id))
    if not legacy_raw:
        return False
    try:
        n = int((text or "").strip())
    except ValueError:
        return False
    if not 1 <= n <= 5:
        return False
    apt_id = int(legacy_raw)
    await asyncio.to_thread(
        lambda: get_db().execute(
            "UPDATE treatment_notes SET followup_rating=?, updated_at=? WHERE appointment_id=?",
            (n, clock.iso_now(), apt_id),
        )
    )
    await r.delete(_legacy_awaiting_key(sender_id))
    return True
