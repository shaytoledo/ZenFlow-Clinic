"""
web/routers/api/appointments.py
─────────────────────────────────
REST endpoints for appointments and patient data.
"""
import asyncio
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from web.deps import _active_therapist_or_redirect
from web.repositories import appointment_repo
from web.services import appointment_service

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class ManualAppointmentIn(BaseModel):
    patient_name: str
    date: str           # YYYY-MM-DD
    time: str           # HH:MM
    patient_phone: str = ""
    patient_email: str = ""
    notes: str = ""     # free-text — saved into appointments.summary
    existing_patient_id: int | None = None  # when picking an existing patient


@router.get("/appointments/today")
async def get_today_appointments():
    from datetime import date as _date

    all_apts = await asyncio.to_thread(appointment_repo.list_all)
    today_str = _date.today().isoformat()

    today_apts = sorted(
        [a for a in all_apts if a.get("date") == today_str and a.get("status") == "active"],
        key=lambda x: x.get("time", ""),
    )
    all_active = [a for a in all_apts if a.get("status") == "active"]
    patient_count = len({a["patient_id"] for a in all_active if a.get("patient_id")})
    session_count = len(all_active)
    intake_count = sum(1 for a in today_apts if a.get("intake_history"))
    recent = sorted(all_active, key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)[:10]

    def _fmt(a: dict) -> dict:
        return {
            "patient_id":   a["patient_id"],
            "patient_name": a.get("patient_name", ""),
            "date":         a.get("date"),
            "time":         a.get("time"),
            "summary":      (a.get("summary") or "")[:200],
            "has_intake":   bool(a.get("intake_history")),
        }

    today_count = len(today_apts)
    today_label = f"{today_count} appointment{'s' if today_count != 1 else ''} today"

    return JSONResponse({
        "today_count":    today_count,
        "today_label":    today_label,
        "intake_count":   intake_count,
        "patient_count":  patient_count,
        "session_count":  session_count,
        "today":          [_fmt(a) for a in today_apts],
        "intake_alerts":  [],
        "recent":         [_fmt(a) for a in recent],
    })


@router.post("/appointments")
async def create_manual_appointment(body: ManualAppointmentIn, request: Request):
    """Create an appointment from the dashboard (no Telegram intake).

    The appointment gets a unique negative `patient_id` so it never collides
    with real Telegram user IDs. Source is marked 'manual' so we know to skip
    intake-history lookups when rendering the treatment screen for it.
    """
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")

    name = (body.patient_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Patient name is required")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.date):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    if not re.fullmatch(r"\d{2}:\d{2}", body.time):
        raise HTTPException(status_code=400, detail="Time must be HH:MM")

    therapist_id = (therapist or {}).get("id") or "t1"
    try:
        appt_id, patient_id = await asyncio.to_thread(
            appointment_repo.insert_manual,
            patient_name=name,
            therapist_id=therapist_id,
            apt_date=body.date,
            apt_time=body.time,
            patient_phone=body.patient_phone.strip(),
            patient_email=body.patient_email.strip(),
            summary=body.notes.strip(),
            existing_patient_id=body.existing_patient_id,
        )

        # Mirror the bot's booking flow: consume the availability slot and (if
        # Google is connected) create the calendar event. Failure here doesn't
        # roll back the appointment row — the row is the source of truth and
        # we'd rather show the booking with no GCal mirror than lose it.
        try:
            from datetime import date as _date
            from bot.patient_bot.services.availability import book_slot
            day = _date.fromisoformat(body.date)
            gcal_id = await book_slot(
                day=day,
                time_slot=body.time,
                patient_name=name,
                summary=body.notes.strip() or f"Manual booking for {name}",
                therapist_id=therapist_id,
            )
            if gcal_id:
                await asyncio.to_thread(
                    appointment_repo.set_gcal_event_id, appt_id, gcal_id
                )
        except Exception as e:
            logger.warning(f"create_manual_appointment: book_slot failed (kept appt {appt_id}): {e}")

        # Bust caches — appointments list + rolling Google-events cache
        try:
            from web.services.cache_service import invalidate_appointments, purge_calendar
            await invalidate_appointments()
            await purge_calendar(therapist_id)
        except Exception:
            pass

        return JSONResponse({
            "ok": True,
            "appointment_id": appt_id,
            "patient_id": patient_id,
            "treatment_url": f"/treatment/{patient_id}/{body.date}/{body.time.replace(':','-')}",
        })
    except Exception as e:
        logger.error(f"create_manual_appointment failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patients/search")
async def search_patients(q: str = ""):
    """Return matching patients for autocomplete with full contact info."""
    rows = await asyncio.to_thread(appointment_repo.search_patients, q, 10)
    return JSONResponse({"results": [
        {
            "patient_id":    r["patient_id"],
            "patient_name":  r["patient_name"],
            "patient_phone": r.get("patient_phone") or "",
            "patient_email": r.get("patient_email") or "",
            "source":        r.get("source") or "telegram",
        } for r in rows
    ]})


@router.get("/patients")
async def get_patients():
    appointments = await appointment_service.list_all_cached()
    patients = appointment_service.aggregate_patients(appointments)
    return JSONResponse(patients)


@router.get("/patients/{patient_id}")
async def get_patient_detail(patient_id: int):
    records = appointment_service.list_by_patient(patient_id)
    if not records:
        raise HTTPException(status_code=404, detail="Patient not found")
    name = f"Patient {patient_id}"

    # Fetch treatment notes for each appointment to enrich EHR view
    from bot.db import get_db
    appointments = []
    for d in records:
        if d.get("patient_name") and name == f"Patient {patient_id}":
            name = d["patient_name"]

        # Look up treatment notes for this appointment
        apt_id = d.get("id") or await asyncio.to_thread(
            lambda: (get_db().execute(
                "SELECT id FROM appointments WHERE patient_id=? AND date=? AND time=? ORDER BY created_at DESC LIMIT 1",
                (patient_id, d.get("date"), d.get("time")),
            ).fetchone() or {}).get("id") if True else None
        )

        notes = {}
        if apt_id:
            row = await asyncio.to_thread(
                lambda aid=apt_id: get_db().execute(
                    """SELECT tcm_pattern, treatment_principles, diagnosis_certainty,
                              used_points, completed_at,
                              followup_rating, followup_conversation,
                              manual_feedback_rating, manual_feedback_notes
                       FROM treatment_notes WHERE appointment_id=?""",
                    (aid,),
                ).fetchone()
            )
            if row:
                import json as _json
                r = dict(row)
                try:
                    r["used_points"] = _json.loads(r["used_points"]) if r.get("used_points") else []
                except Exception:
                    r["used_points"] = []
                try:
                    r["followup_conversation"] = _json.loads(r["followup_conversation"]) if r.get("followup_conversation") else None
                except Exception:
                    r["followup_conversation"] = None
                notes = r

        appointments.append({
            "date": d.get("date"),
            "time": d.get("time"),
            "summary": d.get("summary", ""),
            "intake_history": d["intake_history"],
            "status": d.get("status"),
            "appointment_id": apt_id,
            "tcm_pattern": notes.get("tcm_pattern"),
            "treatment_principles": notes.get("treatment_principles"),
            "diagnosis_certainty": notes.get("diagnosis_certainty"),
            "used_points": notes.get("used_points", []),
            "completed_at": notes.get("completed_at"),
            "followup_rating": notes.get("followup_rating"),
            "followup_conversation": notes.get("followup_conversation"),
            "manual_feedback_rating": notes.get("manual_feedback_rating"),
            "manual_feedback_notes": notes.get("manual_feedback_notes"),
        })

    return JSONResponse({"id": patient_id, "name": name, "appointments": appointments})


@router.get("/appointment/{patient_id}/{apt_date}/{apt_time}")
async def get_appointment_detail(patient_id: int, apt_date: str, apt_time: str):
    record = appointment_service.get_by_patient_date_time(patient_id, apt_date, apt_time)
    if not record:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return JSONResponse(record)
