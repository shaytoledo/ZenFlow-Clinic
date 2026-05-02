"""
web/routers/api/treatment.py
──────────────────────────────
Treatment notes CRUD, re-diagnosis, session completion, and Telegram recommendations.
"""
import asyncio
import json
import logging
import re as _re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import _active_therapist_or_redirect
from web.services import treatment_service, telegram_service

router = APIRouter(prefix="/api/treatment-notes")
logger = logging.getLogger(__name__)



# ── Models ─────────────────────────────────────────────────────────────────────

class TreatmentNotesIn(BaseModel):
    tongue_observation: str = ""
    pulse_observation: str = ""
    session_notes: str = ""
    used_points: list[str] = []
    recommendations_sent_at: str | None = None
    therapist_diagnosis: str = ""
    therapist_notes: str = ""


class RecommendationsIn(BaseModel):
    items: list[dict]
    schedule_hours: int = 2
    # Optional override — when supplied the server sends via email instead of Telegram.
    # The frontend collects this from a popup when the patient is a manual booking
    # without a Telegram account.
    email: str | None = None


class CompleteSessionIn(BaseModel):
    tongue_observation: str = ""
    pulse_observation: str = ""
    session_notes: str = ""
    used_points: list[str] = []
    therapist_diagnosis: str = ""
    therapist_notes: str = ""


class RediagnoseIn(BaseModel):
    tongue_observation: str = ""
    pulse_observation: str = ""


class ManualFeedbackIn(BaseModel):
    rating: int | None = None   # 1–5
    notes: str = ""


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _resolve_apt_id(patient_id: int, apt_date: str, apt_time: str) -> int:
    apt_id = await asyncio.to_thread(treatment_service.get_appointment_id, patient_id, apt_date, apt_time)
    if not apt_id:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return apt_id


def _require_auth(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return therapist


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/{patient_id}/{apt_date}/{apt_time}")
async def get_treatment_notes(
    patient_id: int, apt_date: str, apt_time: str, request: Request
):
    _require_auth(request)
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)
    notes = await asyncio.to_thread(treatment_service.get_notes, apt_id)
    if not notes:
        # Also return source so the UI can show the no-Telegram alert
        time_str = apt_time.replace("-", ":")
        from bot.db import get_db
        row = await asyncio.to_thread(
            lambda: get_db().execute(
                "SELECT source FROM appointments WHERE patient_id=? AND date=? AND time=? ORDER BY created_at DESC LIMIT 1",
                (patient_id, apt_date, time_str),
            ).fetchone()
        )
        src = (dict(row).get("source") if row else None) or "telegram"
        return JSONResponse({"appointment_id": apt_id, "source": src, "is_manual": src == "manual" or patient_id < 0})
    # Augment with source flag
    time_str = apt_time.replace("-", ":")
    from bot.db import get_db
    row = await asyncio.to_thread(
        lambda: get_db().execute(
            "SELECT source FROM appointments WHERE patient_id=? AND date=? AND time=? ORDER BY created_at DESC LIMIT 1",
            (patient_id, apt_date, time_str),
        ).fetchone()
    )
    src = (dict(row).get("source") if row else None) or "telegram"
    notes["source"] = src
    notes["is_manual"] = (src == "manual") or (patient_id < 0)
    return JSONResponse(notes)


@router.post("/{patient_id}/{apt_date}/{apt_time}")
async def save_treatment_notes(
    patient_id: int, apt_date: str, apt_time: str,
    body: TreatmentNotesIn, request: Request,
):
    _require_auth(request)
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)
    await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, body.model_dump())
    return JSONResponse({"ok": True})


@router.post("/{patient_id}/{apt_date}/{apt_time}/complete")
async def complete_session(
    patient_id: int, apt_date: str, apt_time: str,
    body: CompleteSessionIn, request: Request,
):
    """Save session notes, mark complete, and auto-queue 24h recommendation delivery.

    The auto-queue uses the saved AI recommendations (`ai_recommendations`) — if
    they exist and weren't already sent or queued, we schedule them to deliver
    exactly 24 hours from now. Therapist can still hit "Send Now" to override.
    """
    therapist = _require_auth(request)
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)

    import datetime as _dt
    notes = body.model_dump()
    notes["completed_at"] = _dt.datetime.now().isoformat()
    await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, notes)

    # Auto-queue: if AI recommendations exist + not yet sent + not already queued, schedule them
    try:
        from web.repositories.treatment_repo import get_by_appointment, save_pending_recommendations
        row = await asyncio.to_thread(get_by_appointment, apt_id)
        if row:
            recs       = row.get("ai_recommendations") or {}
            already_sent     = bool(row.get("recommendations_sent_at"))
            already_queued   = bool(row.get("pending_rec_send_at"))
            has_content      = isinstance(recs, dict) and any(recs.get(k) for k in ("diet","sleep","exercise","stress"))

            if has_content and not already_sent and not already_queued:
                items = []
                for cat_key, cat_label, icon in [("sleep","Sleep","🌙"),("diet","Diet","🥗"),("stress","Stress","🧘"),("exercise","Exercise","🏃")]:
                    if recs.get(cat_key):
                        items.append({"id": cat_key, "category": cat_label, "icon": icon,
                                      "text": recs[cat_key], "enabled": True})
                send_at = (_dt.datetime.now() + _dt.timedelta(hours=24)).isoformat()
                await asyncio.to_thread(save_pending_recommendations, apt_id, items, send_at)

                # Look up patient name + check contact info for the alert
                from bot.db import get_db
                apt_row = await asyncio.to_thread(
                    lambda: get_db().execute(
                        "SELECT patient_name, patient_phone, patient_email, source FROM appointments WHERE id=?",
                        (apt_id,),
                    ).fetchone()
                )
                ar = dict(apt_row) if apt_row else {}
                patient_name = ar.get("patient_name") or "Patient"
                source = ar.get("source") or "telegram"
                has_email = bool((ar.get("patient_email") or "").strip())
                is_manual = (source == "manual") or (patient_id < 0)

                from web.services import notification_service
                if is_manual and not has_email:
                    # Persistent missing-contact alert — won't be deliverable in 24h
                    await asyncio.to_thread(
                        notification_service.alert_missing_contact,
                        therapist["id"], apt_id, patient_id, patient_name,
                    )
                else:
                    # Info: queued for delivery
                    await asyncio.to_thread(
                        notification_service.alert_recommendations_queued,
                        therapist["id"], apt_id, patient_id, patient_name, send_at,
                    )
    except Exception as e:
        logger.warning(f"complete_session: auto-queue failed for apt {apt_id}: {e}")

    return JSONResponse({"ok": True, "redirect": "/"})


def _format_recommendations_for_telegram(enabled: list[dict]) -> str:
    icon_map = {"Diet": "🥗", "Sleep": "🌙", "Exercise": "🏃", "Movement": "🚶", "Stress": "🧘"}
    lines = ["*Your post-treatment lifestyle recommendations from ZenFlow Clinic:*\n"]
    for item in enabled:
        cat = item.get("category", "")
        text = item.get("text", "")
        icon = item.get("icon") or icon_map.get(cat, "•")
        lines.append(f"{icon} *{cat}:* {text}")
    lines.append("\n_Take care and see you at your next session! 🌿_")
    return "\n".join(lines)


def _format_recommendations_for_email(enabled: list[dict], patient_name: str) -> tuple[str, str]:
    """Return (subject, body_text) for the email version of the recommendations."""
    subject = "Your post-treatment recommendations — ZenFlow Clinic"
    icon_map = {"Diet": "Diet", "Sleep": "Sleep", "Exercise": "Exercise", "Movement": "Movement", "Stress": "Stress"}
    lines = [
        f"Hi {patient_name.split()[0] if patient_name else 'there'},",
        "",
        "Here are the lifestyle recommendations from your treatment session:",
        "",
    ]
    for item in enabled:
        cat = item.get("category", "")
        text = item.get("text", "")
        lines.append(f"• {icon_map.get(cat, cat)}: {text}")
    lines += ["", "Take care, and see you at your next session.", "", "— ZenFlow Clinic"]
    return subject, "\n".join(lines)


@router.post("/{patient_id}/{apt_date}/{apt_time}/send-recommendations")
async def send_recommendations(
    patient_id: int, apt_date: str, apt_time: str,
    body: RecommendationsIn, request: Request,
):
    """Deliver recommendations to the patient.

    Routing:
      - schedule_hours >= 24 (and no explicit email) → queue in DB for auto-send
      - explicit `email` in the body → send via SMTP immediately
      - Telegram-source patient (positive id) → send via patient bot immediately
      - Manual patient (negative id):
          * has phone but no email → 422 "needs_email" so the frontend can ask
          * has neither phone nor email → 400 "no_contact"
    """
    therapist = _require_auth(request)
    enabled = [item for item in body.items if item.get("enabled")]
    if not enabled:
        raise HTTPException(status_code=400, detail="No recommendations selected")

    # ── Delayed queue: schedule_hours >= 24 without an email override → store for later
    if body.schedule_hours >= 24 and not body.email:
        apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)
        import datetime as _dt
        send_at = (_dt.datetime.now() + _dt.timedelta(hours=body.schedule_hours)).isoformat()
        from web.repositories.treatment_repo import save_pending_recommendations as _save_pending
        await asyncio.to_thread(_save_pending, apt_id, enabled, send_at)

        # Lookup name for the notification
        from bot.db import get_db
        ar = await asyncio.to_thread(
            lambda: get_db().execute(
                "SELECT patient_name FROM appointments WHERE id=?", (apt_id,)
            ).fetchone()
        )
        pname = (dict(ar).get("patient_name") if ar else "Patient") or "Patient"
        from web.services import notification_service
        await asyncio.to_thread(
            notification_service.alert_recommendations_queued,
            therapist["id"], apt_id, patient_id, pname, send_at,
        )
        return JSONResponse({
            "ok": True,
            "queued": True,
            "send_at": send_at,
            "hours": body.schedule_hours,
        })

    # Look up the appointment so we know patient_phone + name + source
    time_str = apt_time.replace("-", ":")
    from bot.db import get_db
    row = await asyncio.to_thread(
        lambda: get_db().execute(
            """SELECT id, patient_name, patient_phone, source
               FROM appointments
               WHERE patient_id=? AND date=? AND time=?
               ORDER BY created_at DESC LIMIT 1""",
            (patient_id, apt_date, time_str),
        ).fetchone()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")
    apt_id        = row["id"]
    patient_name  = row["patient_name"] or "Patient"
    patient_phone = (row["patient_phone"] or "").strip()
    is_manual     = (row["source"] == "manual") or patient_id < 0

    sent_via: str
    sent_to:  str

    # ── 1) explicit email override (or manual patient defaulting to email)
    if body.email:
        from web.services.email_service import send_email, EmailNotConfigured
        subject, text = _format_recommendations_for_email(enabled, patient_name)
        try:
            await asyncio.to_thread(
                send_email,
                therapist["id"],       # therapist_id — uses their Gmail OAuth token
                body.email.strip(),    # to
                subject,
                text,
            )
        except EmailNotConfigured:
            # Gmail not connected — return the text so the UI can show a copy-paste fallback
            return JSONResponse(content={
                "ok": False,
                "status": "no_smtp",
                "text": f"{subject}\n\n{text}",
                "detail": "Gmail is not connected. Go to Settings → Connect Google, then retry.",
            })
        except Exception as e:
            logger.error(f"send_recommendations(email) error: {e}")
            raise HTTPException(status_code=502, detail=f"Email send failed: {e}")
        sent_via = "email"
        sent_to  = body.email.strip()

    # ── 2) manual patient — bot can't reach them; always redirect to email popup
    elif is_manual:
        detail = (
            "This patient was booked manually and has no Telegram account linked. "
            "Enter their email address to send the recommendations."
        )
        if patient_phone:
            detail = (
                f"No Telegram account is linked to {patient_phone}. The Telegram Bot "
                f"API can only message users who have started a chat with the bot. "
                f"Send the recommendations by email instead?"
            )
        return JSONResponse(
            status_code=422,
            content={
                "status": "needs_email",
                "detail": detail,
                "phone": patient_phone,
                "patient_name": patient_name,
            },
        )

    # ── 3) Telegram-source patient — original happy path
    else:
        try:
            await telegram_service.send_to_patient(patient_id, _format_recommendations_for_telegram(enabled))
        except Exception as e:
            logger.error(f"send_recommendations(telegram) error: {e}")
            raise HTTPException(status_code=500, detail=str(e))
        sent_via = "telegram"
        sent_to  = str(patient_id)

    # Stamp delivery time on the treatment row (best-effort)
    import datetime as _dt
    try:
        await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, {
            "recommendations_sent_at": _dt.datetime.now().isoformat()
        })
    except Exception:
        pass

    # Record a success notification
    try:
        from web.services import notification_service
        await asyncio.to_thread(
            notification_service.alert_recommendations_sent,
            therapist["id"], apt_id, patient_id, patient_name, sent_via, sent_to,
        )
    except Exception as e:
        logger.debug(f"send_recommendations: notification create failed: {e}")
    return JSONResponse({"ok": True, "sent_via": sent_via, "sent_to": sent_to})


@router.post("/{patient_id}/{apt_date}/{apt_time}/manual-feedback")
async def save_manual_feedback(
    patient_id: int, apt_date: str, apt_time: str,
    body: ManualFeedbackIn, request: Request,
):
    """Save therapist-entered patient feedback (fallback when no Telegram)."""
    _require_auth(request)
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)
    if body.rating is not None and not (1 <= body.rating <= 5):
        raise HTTPException(status_code=400, detail="Rating must be 1–5")
    from web.repositories.treatment_repo import save_manual_feedback as _save
    await asyncio.to_thread(_save, apt_id, body.rating, body.notes)
    return JSONResponse({"ok": True})


def _parse_diagnosis_json(raw: str) -> dict:
    """Best-effort JSON parser for the AI's diagnosis response.

    The model frequently returns malformed JSON (trailing commas, single quotes,
    truncation, code-fence noise). We try strict parsing first, then a sequence
    of repair strategies, and finally regex field extraction so the therapist
    always gets *something* — never a 500.
    """
    text = (raw or "").strip()
    text = _re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    m = _re.search(r"\{[\s\S]*\}", text)
    if m:
        text = m.group(0)

    # 1. strict
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. cheap repairs: trailing commas, smart quotes, single-quoted strings
    repaired = text
    repaired = _re.sub(r",(\s*[}\]])", r"\1", repaired)              # trailing comma before } or ]
    repaired = repaired.replace("“", '"').replace("”", '"')  # smart double quotes
    repaired = repaired.replace("‘", "'").replace("’", "'")  # smart single quotes
    try:
        return json.loads(repaired)
    except Exception:
        pass

    # 3. swap single-quoted strings to double-quoted (rough heuristic — only when no double quotes present)
    if '"' not in repaired and "'" in repaired:
        try:
            return json.loads(repaired.replace("'", '"'))
        except Exception:
            pass

    # 4. last-ditch: regex out the fields one by one and synthesise a partial dict
    out: dict = {}
    pat = _re.search(r'"tcm_pattern"\s*:\s*"([^"]*)"', text)
    if pat: out["tcm_pattern"] = pat.group(1)
    tp = _re.search(r'"treatment_principles"\s*:\s*"([^"]*)"', text)
    if tp: out["treatment_principles"] = tp.group(1)
    cert = _re.search(r'"diagnosis_certainty"\s*:\s*(\d+)', text)
    if cert: out["diagnosis_certainty"] = int(cert.group(1))
    # Acupuncture point codes appear as 1–3 letters + 1–3 digits (LR3, ST36, BL23) or extras (Yintang, Taiyang)
    codes = _re.findall(r'"code"\s*:\s*"([A-Z]{2,3}\d{1,3}|GV\d{1,3}|CV\d{1,3}|REN\d{1,3}|DU\d{1,3}|YIN(?:TANG)?|TAIYANG)"', text, _re.IGNORECASE)
    if codes:
        out["suggested_points"] = [{"code": c.upper(), "rationale": ""} for c in codes[:8]]
    return out


def _normalize_points(raw_pts) -> list[dict]:
    """Coerce whatever the AI returned into a list of {code, rationale} dicts."""
    out = []
    if not isinstance(raw_pts, list):
        return out
    for pt in raw_pts:
        if isinstance(pt, dict):
            code = str(pt.get("code") or pt.get("point") or "").strip().upper()
            if code:
                out.append({"code": code, "rationale": str(pt.get("rationale") or pt.get("why") or "")})
        elif isinstance(pt, str) and pt.strip():
            out.append({"code": pt.strip().upper(), "rationale": ""})
    return out


def _load_intake_context(apt_id: int) -> str:
    """Return the full intake conversation as a readable transcript, or '' if missing."""
    from bot.db import get_db
    row = get_db().execute(
        "SELECT history_json FROM intake_sessions WHERE appointment_id=? "
        "ORDER BY id DESC LIMIT 1",
        (apt_id,),
    ).fetchone()
    if not row or not row["history_json"]:
        return ""
    try:
        history = json.loads(row["history_json"])
    except Exception:
        return ""
    lines = []
    for msg in history:
        role = (msg.get("role") or "").lower()
        content = (msg.get("content") or msg.get("text") or "").strip()
        if not content:
            continue
        label = "Patient" if role in ("user", "human", "patient") else "Assistant"
        lines.append(f"{label}: {content}")
    return "\n".join(lines)


@router.post("/{patient_id}/{apt_date}/{apt_time}/rediagnose")
async def rediagnose(
    patient_id: int, apt_date: str, apt_time: str,
    body: RediagnoseIn, request: Request,
):
    """Re-run TCM diagnosis with the full intake transcript + updated tongue/pulse.

    Behaviour: never raise to the user for parse errors. If the AI returns
    garbage we still save and return whatever fields we recovered so the
    therapist sees an updated diagnosis instead of a red error banner.
    """
    therapist = _require_auth(request)
    lang = (therapist.get("language") or "en") if isinstance(therapist, dict) else "en"

    time_str = apt_time.replace("-", ":")
    from bot.db import get_db
    row = get_db().execute(
        """SELECT a.id as apt_id, a.summary
           FROM appointments a
           WHERE a.patient_id=? AND a.date=? AND a.time=?
           ORDER BY a.created_at DESC LIMIT 1""",
        (patient_id, apt_date, time_str),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Appointment not found")

    apt_id = row["apt_id"]
    summary = row["summary"] or ""
    transcript = await asyncio.to_thread(_load_intake_context, apt_id)

    findings_parts = []
    if body.tongue_observation:
        findings_parts.append(f"Tongue: {body.tongue_observation}")
    if body.pulse_observation:
        findings_parts.append(f"Pulse: {body.pulse_observation}")
    findings_context = ". ".join(findings_parts) or "No tongue/pulse observation recorded yet."

    try:
        from bot.patient_bot.services.ai_intake import (
            _LLM_LONG, SYSTEM_PROMPT, get_diagnosis_prompt, select_points_for_diagnosis,
        )
        from langchain_core.messages import HumanMessage, SystemMessage

        if _LLM_LONG is None:
            raise HTTPException(status_code=503, detail="AI model not available")

        diag_prompt = get_diagnosis_prompt(lang)

        # Build context block for the diagnosis step
        context_block = ""
        if transcript:
            context_block += f"Full intake conversation:\n{transcript}\n\n"
        if summary:
            context_block += f"Clinical summary:\n{summary}\n\n"
        if not context_block:
            context_block = "No prior intake on file. Diagnose from examination findings only.\n\n"

        # ── Step 1: diagnosis (pattern, principles, certainty, recommendations) ──
        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=(
                f"{context_block}"
                f"Updated clinical examination findings:\n{findings_context}\n\n"
                f"{diag_prompt}"
            )),
        ]
        resp = await asyncio.wait_for(_LLM_LONG.ainvoke(messages), timeout=180)
        parsed = _parse_diagnosis_json(resp.content)

        raw_certainty = parsed.get("diagnosis_certainty", 0)
        try:
            certainty = max(0, min(100, int(raw_certainty)))
        except (TypeError, ValueError):
            certainty = 0

        result = {
            "tcm_pattern":          str(parsed.get("tcm_pattern") or ""),
            "treatment_principles": str(parsed.get("treatment_principles") or ""),
            "diagnosis_certainty":  certainty,
            "ai_suggested_points":  [],       # Stage 2 is called separately by the frontend
            "recommendations":      parsed.get("recommendations") or {},
        }

        # Persist Stage 1 fields immediately so generate-points can read them from the DB
        await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, {
            "tcm_pattern":          result["tcm_pattern"],
            "treatment_principles": result["treatment_principles"],
            "diagnosis_certainty":  result["diagnosis_certainty"],
            "ai_recommendations":   result["recommendations"],
            "tongue_observation":   body.tongue_observation,
            "pulse_observation":    body.pulse_observation,
        })
        logger.info(f"rediagnose apt{apt_id} — Stage 1 saved, pattern='{result['tcm_pattern']}'")

        return JSONResponse(result)

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="AI model timed out — try again")
    except Exception as e:
        logger.error(f"rediagnose error: {e}")
        raise HTTPException(status_code=500, detail=f"AI service error: {e}")


@router.post("/{patient_id}/{apt_date}/{apt_time}/generate-points")
async def generate_points(
    patient_id: int, apt_date: str, apt_time: str, request: Request,
):
    """Stage 2: select acupuncture points for an already-diagnosed appointment.

    Called directly by the frontend immediately after /rediagnose returns Stage 1.
    Reads the diagnosis from the DB — the caller does not need to repeat it.
    Runs synchronously so the frontend receives the points the moment they are ready
    (no polling needed; the HTTP response IS the result).
    """
    therapist = _require_auth(request)
    lang = (therapist.get("language") or "en") if isinstance(therapist, dict) else "en"
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)

    from web.repositories.treatment_repo import (
        get_by_appointment as _get,
        save_points as _save_pts,
        set_points_status as _set_st,
    )
    row = await asyncio.to_thread(_get, apt_id)
    if not row:
        raise HTTPException(status_code=404, detail="No treatment notes for this appointment")

    tcm_pattern         = row.get("tcm_pattern") or ""
    treatment_principles = row.get("treatment_principles") or ""
    if not tcm_pattern:
        raise HTTPException(status_code=422, detail="No TCM diagnosis yet — run diagnosis first")

    intake_context = await asyncio.to_thread(_load_intake_context, apt_id)

    try:
        from bot.patient_bot.services.ai_intake import select_points_for_diagnosis, _LLM_POINTS
        if _LLM_POINTS is None:
            raise HTTPException(status_code=503, detail="AI model not available")

        await asyncio.to_thread(_set_st, apt_id, "GENERATING")
        logger.info(f"generate-points apt{apt_id} — started, pattern='{tcm_pattern}'")

        points = await select_points_for_diagnosis(
            tcm_pattern=tcm_pattern,
            treatment_principles=treatment_principles,
            intake_context=intake_context or "No prior intake on file.",
            log_tag=f"apt{apt_id}",
            lang=lang,
        )

        logger.info(f"generate-points apt{apt_id} — AI returned {len(points)} point(s)")

        if points:
            await asyncio.to_thread(_save_pts, apt_id, points)   # stamps COMPLETED in the same UPDATE
            logger.info(f"generate-points apt{apt_id} — COMPLETED: {len(points)} points saved")
        else:
            await asyncio.to_thread(_set_st, apt_id, "FAILED")
            logger.error(f"generate-points apt{apt_id} — FAILED: AI returned 0 points")

        return JSONResponse({
            "ai_suggested_points": points,
            "points_status":       "COMPLETED" if points else "FAILED",
            "point_count":         len(points),
        })

    except asyncio.TimeoutError:
        await asyncio.to_thread(_set_st, apt_id, "FAILED")
        raise HTTPException(status_code=504, detail="AI model timed out on point selection")
    except Exception as e:
        logger.error(f"generate-points error: {e}", exc_info=True)
        try:
            await asyncio.to_thread(_set_st, apt_id, "FAILED")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"AI service error: {e}")


@router.post("/{patient_id}/{apt_date}/{apt_time}/regenerate-points")
async def regenerate_points(
    patient_id: int, apt_date: str, apt_time: str, request: Request,
):
    """Re-run Stage 2A + 2B point selection from scratch using the saved diagnosis.

    Clears existing ai_suggested_points, then runs both batches with the same
    status-flip pattern as the bot's background pipeline so the frontend poller
    can render each batch incrementally.
    """
    therapist = _require_auth(request)
    lang = (therapist.get("language") or "en") if isinstance(therapist, dict) else "en"
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)

    from web.repositories.treatment_repo import (
        get_by_appointment as _get,
        append_points as _append,
        set_points_status as _set_st,
    )
    row = await asyncio.to_thread(_get, apt_id)
    if not row:
        raise HTTPException(status_code=404, detail="No treatment notes for this appointment")

    tcm_pattern          = row.get("tcm_pattern") or ""
    treatment_principles = row.get("treatment_principles") or ""
    if not tcm_pattern:
        raise HTTPException(status_code=422, detail="No TCM diagnosis yet — run diagnosis first")

    intake_context = await asyncio.to_thread(_load_intake_context, apt_id)

    # Clear existing points so the UI starts fresh
    from bot.db import get_db
    await asyncio.to_thread(
        lambda: get_db().execute(
            "UPDATE treatment_notes SET ai_suggested_points=NULL, points_status=?, updated_at=datetime('now') WHERE appointment_id=?",
            ("GENERATING_STAGE_2A", apt_id),
        )
    )

    try:
        from bot.patient_bot.services.ai_intake import select_points_for_diagnosis, _LLM_POINTS
        if _LLM_POINTS is None:
            await asyncio.to_thread(_set_st, apt_id, "FAILED")
            raise HTTPException(status_code=503, detail="AI model not available")

        logger.info(f"regenerate-points apt{apt_id} — Stage 2A start, pattern='{tcm_pattern}'")
        batch_a = await select_points_for_diagnosis(
            tcm_pattern=tcm_pattern,
            treatment_principles=treatment_principles,
            intake_context=intake_context or "No prior intake on file.",
            log_tag=f"apt{apt_id}",
            batch_number=1,
            lang=lang,
        )
        if batch_a:
            await asyncio.to_thread(_append, apt_id, batch_a)
            logger.info(f"regenerate-points apt{apt_id} — Stage 2A done ({len(batch_a)} points)")

        await asyncio.to_thread(_set_st, apt_id, "GENERATING_STAGE_2B")
        existing_codes = [p["code"] for p in batch_a if isinstance(p, dict) and p.get("code")]
        logger.info(f"regenerate-points apt{apt_id} — Stage 2B start, avoiding {existing_codes}")
        batch_b = await select_points_for_diagnosis(
            tcm_pattern=tcm_pattern,
            treatment_principles=treatment_principles,
            intake_context=intake_context or "No prior intake on file.",
            log_tag=f"apt{apt_id}",
            batch_number=2,
            existing_codes=existing_codes,
            lang=lang,
        )
        if batch_b:
            await asyncio.to_thread(_append, apt_id, batch_b)
            logger.info(f"regenerate-points apt{apt_id} — Stage 2B done ({len(batch_b)} points)")

        total = len(batch_a) + len(batch_b)
        final_status = "COMPLETED" if total > 0 else "FAILED"
        await asyncio.to_thread(_set_st, apt_id, final_status)
        logger.info(f"regenerate-points apt{apt_id} — {final_status}: {total} points total")

        # Return the merged list so the caller can render it directly if desired
        final_row = await asyncio.to_thread(_get, apt_id)
        return JSONResponse({
            "ai_suggested_points": final_row.get("ai_suggested_points") or [],
            "points_status":       final_status,
            "point_count":         total,
        })

    except asyncio.TimeoutError:
        await asyncio.to_thread(_set_st, apt_id, "FAILED")
        raise HTTPException(status_code=504, detail="AI model timed out on point selection")
    except Exception as e:
        logger.error(f"regenerate-points error: {e}", exc_info=True)
        try:
            await asyncio.to_thread(_set_st, apt_id, "FAILED")
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"AI service error: {e}")


@router.get("/sessions/history")
async def list_sessions(request: Request, sort: str = "date"):
    """List all treatment sessions, sorted by name / date / last_access."""
    therapist = _require_auth(request)
    sessions = treatment_service.list_all_sessions(
        therapist_id=therapist["id"], sort_by=sort
    )
    return JSONResponse(sessions)


@router.get("/{appointment_id}/debug")
async def debug_points(appointment_id: int, request: Request):
    """Return the raw DB record for a treatment_notes row — useful for diagnosing Stage-2 failures.

    Returns: appointment_id, points_status, ai_suggested_points (raw JSON string + parsed list),
             tcm_pattern, and updated_at so you can tell exactly what the DB contains.
    """
    _require_auth(request)
    from web.repositories.treatment_repo import get_by_appointment as _get
    row = await asyncio.to_thread(_get, appointment_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"No treatment_notes row for appointment {appointment_id}")

    # Also fetch the raw JSON string so we can show exactly what is stored
    from bot.db import get_db
    raw_row = await asyncio.to_thread(
        lambda: get_db().execute(
            "SELECT ai_suggested_points, points_status, updated_at FROM treatment_notes WHERE appointment_id=?",
            (appointment_id,),
        ).fetchone()
    )
    raw_json = dict(raw_row)["ai_suggested_points"] if raw_row else None

    points_list = row.get("ai_suggested_points") or []
    logger.info(
        f"[DEBUG] apt{appointment_id} — points_status={row.get('points_status')!r} "
        f"point_count={len(points_list)} raw_bytes={len(raw_json or '')}"
    )
    return JSONResponse({
        "appointment_id":    appointment_id,
        "points_status":     row.get("points_status"),
        "tcm_pattern":       row.get("tcm_pattern"),
        "point_count":       len(points_list),
        "ai_suggested_points_parsed": points_list,
        "ai_suggested_points_raw":    raw_json,
        "updated_at":        row.get("updated_at"),
    })
