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


class RecommendationsIn(BaseModel):
    items: list[dict]
    schedule_hours: int = 2


class CompleteSessionIn(BaseModel):
    tongue_observation: str = ""
    pulse_observation: str = ""
    session_notes: str = ""
    used_points: list[str] = []


class RediagnoseIn(BaseModel):
    tongue_observation: str = ""
    pulse_observation: str = ""


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/{patient_id}/{apt_date}/{apt_time}")
async def get_treatment_notes(
    patient_id: int, apt_date: str, apt_time: str, request: Request
):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    apt_id = await asyncio.to_thread(treatment_service.get_appointment_id, patient_id, apt_date, apt_time)
    if not apt_id:
        raise HTTPException(status_code=404, detail="Appointment not found")
    notes = await asyncio.to_thread(treatment_service.get_notes, apt_id)
    if not notes:
        return JSONResponse({"appointment_id": apt_id})
    return JSONResponse(notes)


@router.post("/{patient_id}/{apt_date}/{apt_time}")
async def save_treatment_notes(
    patient_id: int, apt_date: str, apt_time: str,
    body: TreatmentNotesIn, request: Request,
):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    apt_id = await asyncio.to_thread(treatment_service.get_appointment_id, patient_id, apt_date, apt_time)
    if not apt_id:
        raise HTTPException(status_code=404, detail="Appointment not found")
    await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, body.model_dump())
    return JSONResponse({"ok": True})


@router.post("/{patient_id}/{apt_date}/{apt_time}/complete")
async def complete_session(
    patient_id: int, apt_date: str, apt_time: str,
    body: CompleteSessionIn, request: Request,
):
    """Save session notes and mark complete in one atomic step, then redirect to dashboard."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    apt_id = await asyncio.to_thread(treatment_service.get_appointment_id, patient_id, apt_date, apt_time)
    if not apt_id:
        raise HTTPException(status_code=404, detail="Appointment not found")
    # Save notes + completion timestamp together
    import datetime as _dt
    notes = body.model_dump()
    notes["completed_at"] = _dt.datetime.now().isoformat()
    await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, notes)
    return JSONResponse({"ok": True, "redirect": "/"})


@router.post("/{patient_id}/{apt_date}/{apt_time}/send-recommendations")
async def send_recommendations(
    patient_id: int, apt_date: str, apt_time: str,
    body: RecommendationsIn, request: Request,
):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

    enabled = [item for item in body.items if item.get("enabled")]
    if not enabled:
        raise HTTPException(status_code=400, detail="No recommendations selected")

    icon_map = {"Diet": "🥗", "Sleep": "🌙", "Exercise": "🏃", "Movement": "🚶", "Stress": "🧘"}
    lines = ["*Your post-treatment lifestyle recommendations from ZenFlow Clinic:*\n"]
    for item in enabled:
        cat = item.get("category", "")
        text = item.get("text", "")
        icon = item.get("icon") or icon_map.get(cat, "•")
        lines.append(f"{icon} *{cat}:* {text}")
    lines.append("\n_Take care and see you at your next session! 🌿_")

    try:
        await telegram_service.send_to_patient(patient_id, "\n".join(lines))
    except Exception as e:
        logger.error(f"send_recommendations error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    # Record sent timestamp
    apt_id = await asyncio.to_thread(treatment_service.get_appointment_id, patient_id, apt_date, apt_time)
    if apt_id:
        import datetime as _dt
        await asyncio.to_thread(treatment_service.save_notes, apt_id, patient_id, {
            "recommendations_sent_at": _dt.datetime.now().isoformat()
        })
    return JSONResponse({"ok": True, "sent_to": patient_id})


@router.post("/{patient_id}/{apt_date}/{apt_time}/rediagnose")
async def rediagnose(
    patient_id: int, apt_date: str, apt_time: str,
    body: RediagnoseIn, request: Request,
):
    """Re-run TCM diagnosis with updated tongue/pulse findings."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

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

    findings_parts = []
    if body.tongue_observation:
        findings_parts.append(f"Tongue: {body.tongue_observation}")
    if body.pulse_observation:
        findings_parts.append(f"Pulse: {body.pulse_observation}")
    findings_context = ". ".join(findings_parts)

    try:
        from bot.patient_bot.services.ai_intake import _LLM, SYSTEM_PROMPT, TCM_DIAGNOSIS_PROMPT
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=(
                f"Clinical summary from intake:\n{summary}\n\n"
                f"Updated clinical examination findings:\n{findings_context}\n\n"
                f"{TCM_DIAGNOSIS_PROMPT}"
            )),
        ]
        resp = await asyncio.wait_for(_LLM.ainvoke(messages), timeout=60)
        raw = resp.content.strip()
        raw = _re.sub(r"^```(?:json)?\s*", "", raw)
        raw = _re.sub(r"\s*```$", "", raw)
        m = _re.search(r"\{[\s\S]*\}", raw)
        if m:
            raw = m.group(0)
        parsed = json.loads(raw)

        raw_pts = parsed.get("suggested_points", [])
        suggested_points = []
        for pt in raw_pts:
            if isinstance(pt, dict):
                suggested_points.append({
                    "code": str(pt.get("code", "")).upper(),
                    "rationale": str(pt.get("rationale", "")),
                })
            elif isinstance(pt, str) and pt.strip():
                suggested_points.append({"code": pt.strip().upper(), "rationale": ""})

        raw_certainty = parsed.get("diagnosis_certainty", 0)
        result = {
            "tcm_pattern": str(parsed.get("tcm_pattern", "")),
            "treatment_principles": str(parsed.get("treatment_principles", "")),
            "diagnosis_certainty": int(raw_certainty) if isinstance(raw_certainty, (int, float)) else 0,
            "suggested_points": suggested_points,
            "recommendations": parsed.get("recommendations", {}),
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sessions/history")
async def list_sessions(request: Request, sort: str = "date"):
    """List all treatment sessions, sorted by name / date / last_access."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sessions = treatment_service.list_all_sessions(
        therapist_id=therapist["id"], sort_by=sort
    )
    return JSONResponse(sessions)
