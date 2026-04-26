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
        return JSONResponse({"appointment_id": apt_id})
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
    """Save session notes and mark complete in one atomic step, then redirect to dashboard."""
    _require_auth(request)
    apt_id = await _resolve_apt_id(patient_id, apt_date, apt_time)
    # Save notes + completion timestamp together
    import datetime as _dt
    notes = body.model_dump()
    notes["completed_at"] = _dt.datetime.now().isoformat()
    await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, notes)
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
      - explicit `email` in the body → send via SMTP, regardless of source
      - Telegram-source patient (positive id) → send via patient bot
      - Manual patient (negative id):
          * has phone but no email → 422 "needs_email" so the frontend can ask
          * has neither phone nor email → 400 "no_contact"
    """
    _require_auth(request)
    enabled = [item for item in body.items if item.get("enabled")]
    if not enabled:
        raise HTTPException(status_code=400, detail="No recommendations selected")

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
            await asyncio.to_thread(send_email, body.email.strip(), subject, text)
        except EmailNotConfigured as e:
            raise HTTPException(status_code=503, detail=str(e))
        except Exception as e:
            logger.error(f"send_recommendations(email) error: {e}")
            raise HTTPException(status_code=502, detail=f"Email send failed: {e}")
        sent_via = "email"
        sent_to  = body.email.strip()

    # ── 2) manual patient — bot can't reach them by phone
    elif is_manual:
        if not patient_phone:
            raise HTTPException(
                status_code=400,
                detail="No phone number on file for this patient. Add a phone number "
                       "when scheduling, or send by email instead.",
            )
        # Has phone but no Telegram chat — ask the UI to collect an email.
        # 422 = Unprocessable Entity. The body carries enough context for the
        # frontend to render the right popup.
        return JSONResponse(
            status_code=422,
            content={
                "status": "needs_email",
                "detail": (
                    f"No Telegram account is linked to {patient_phone}. The Telegram Bot "
                    f"API can only message users who have started a chat with the bot. "
                    f"Send the recommendations by email instead?"
                ),
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
    return JSONResponse({"ok": True, "sent_via": sent_via, "sent_to": sent_to})


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
    _require_auth(request)

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
        from bot.patient_bot.services.ai_intake import _LLM, SYSTEM_PROMPT, TCM_DIAGNOSIS_PROMPT
        from langchain_core.messages import HumanMessage, SystemMessage

        # Build a context block. Prefer full transcript; fall back to summary.
        context_block = ""
        if transcript:
            context_block += f"Full intake conversation between patient and assistant:\n{transcript}\n\n"
        if summary:
            context_block += f"Clinical summary from intake:\n{summary}\n\n"
        if not context_block:
            context_block = "No prior intake on file. Diagnose from the clinical examination findings alone.\n\n"

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=(
                f"{context_block}"
                f"Updated clinical examination findings:\n{findings_context}\n\n"
                f"{TCM_DIAGNOSIS_PROMPT}"
            )),
        ]
        resp = await asyncio.wait_for(_LLM.ainvoke(messages), timeout=90)
        parsed = _parse_diagnosis_json(resp.content)

        suggested_points = _normalize_points(parsed.get("suggested_points"))

        raw_certainty = parsed.get("diagnosis_certainty", 0)
        try:
            certainty = int(raw_certainty)
        except (TypeError, ValueError):
            certainty = 0
        certainty = max(0, min(100, certainty))

        result = {
            "tcm_pattern": str(parsed.get("tcm_pattern") or ""),
            "treatment_principles": str(parsed.get("treatment_principles") or ""),
            "diagnosis_certainty": certainty,
            "suggested_points": suggested_points,
            "recommendations": parsed.get("recommendations") or {},
        }

        await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, {
            "tcm_pattern": result["tcm_pattern"],
            "treatment_principles": result["treatment_principles"],
            "diagnosis_certainty": result["diagnosis_certainty"],
            "ai_suggested_points": result["suggested_points"],
            "ai_recommendations": result["recommendations"],
            "tongue_observation": body.tongue_observation,
            "pulse_observation": body.pulse_observation,
        })
        return JSONResponse(result)

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="AI model timed out — try again")
    except Exception as e:
        logger.error(f"rediagnose error: {e}")
        raise HTTPException(status_code=500, detail=f"AI service error: {e}")


@router.get("/sessions/history")
async def list_sessions(request: Request, sort: str = "date"):
    """List all treatment sessions, sorted by name / date / last_access."""
    therapist = _require_auth(request)
    sessions = treatment_service.list_all_sessions(
        therapist_id=therapist["id"], sort_by=sort
    )
    return JSONResponse(sessions)
