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

from web.deps import require_active_therapist
from web.repositories import appointment_repo
from web.services import appointment_service
from zenflow import clock

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


class ManualAppointmentIn(BaseModel):
    patient_name: str
    date: str  # YYYY-MM-DD
    time: str  # HH:MM
    patient_phone: str = ""
    patient_email: str = ""
    notes: str = ""  # free-text — saved into appointments.summary
    existing_patient_id: int | None = None  # when picking an existing patient


@router.get("/appointments/today")
async def get_today_appointments(request: Request):

    therapist = require_active_therapist(request)
    all_apts = await asyncio.to_thread(appointment_repo.list_all)
    all_apts = [a for a in all_apts if a.get("therapist_id") == therapist["id"]]
    today_str = clock.today().isoformat()

    today_apts = sorted(
        [a for a in all_apts if a.get("date") == today_str and a.get("status") == "active"],
        key=lambda x: x.get("time", ""),
    )
    all_active = [a for a in all_apts if a.get("status") == "active"]
    patient_count = len({a["patient_id"] for a in all_active if a.get("patient_id")})
    session_count = len(all_active)
    intake_count = sum(1 for a in today_apts if a.get("intake_history"))
    recent = sorted(all_active, key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)[
        :10
    ]

    def _fmt(a: dict) -> dict:
        return {
            "patient_id": a["patient_id"],
            "patient_name": a.get("patient_name", ""),
            "date": a.get("date"),
            "time": a.get("time"),
            "summary": (a.get("summary") or "")[:200],
            "has_intake": bool(a.get("intake_history")),
        }

    today_count = len(today_apts)
    today_label = f"{today_count} appointment{'s' if today_count != 1 else ''} today"

    return JSONResponse(
        {
            "today_count": today_count,
            "today_label": today_label,
            "intake_count": intake_count,
            "patient_count": patient_count,
            "session_count": session_count,
            "today": [_fmt(a) for a in today_apts],
            "intake_alerts": [],
            "recent": [_fmt(a) for a in recent],
        }
    )


@router.post("/appointments")
async def create_manual_appointment(body: ManualAppointmentIn, request: Request):
    """Create an appointment from the dashboard (no Telegram intake).

    A new patient gets an internal id and no messaging channel (Phase 7.2); an existing one must
    be the therapist's own. Source is marked 'manual' so we know to skip intake-history lookups
    when rendering the treatment screen for it.
    """
    therapist = require_active_therapist(request)

    name = (body.patient_name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Patient name is required")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body.date):
        raise HTTPException(status_code=400, detail="Date must be YYYY-MM-DD")
    if not re.fullmatch(r"\d{2}:\d{2}", body.time):
        raise HTTPException(status_code=400, detail="Time must be HH:MM")

    therapist_id = therapist["id"]
    if body.existing_patient_id is not None:
        # A guessed id must not attach a booking — and its follow-up messages — to another
        # therapist's patient (ADR-17): unknown and foreign ids are the same 404.
        from web.repositories import patient_repo

        owned = await asyncio.to_thread(
            patient_repo.belongs_to_therapist, body.existing_patient_id, therapist_id
        )
        if not owned:
            raise HTTPException(status_code=404, detail="Patient not found")
    # One booking implementation (ADR-29): the service inserts the row, takes the hour out of
    # the availability calendar and creates the event. A therapist may book any hour of their own
    # calendar, published or not, and the patient hears from them, not from a queued message.
    from web.services import booking_service as booking

    try:
        appointment = await booking.create(
            booking.BookingRequest(
                therapist_id=therapist_id,
                start_at=booking.clock.parse_iso(
                    f"{body.date}T{body.time}", naive_tz=booking.clock.clinic_tz()
                ),
                patient=booking.PatientSpec(
                    name=name,
                    patient_id=body.existing_patient_id,
                    phone=body.patient_phone.strip(),
                    email=body.patient_email.strip(),
                ),
                summary=body.notes.strip(),
                calendar_note=body.notes.strip() or f"Manual booking for {name}",
                source="manual",
                send_confirmation=False,
                enforce_availability=False,
            )
        )
        appt_id, patient_id = appointment["id"], appointment["patient_id"]

        # Bust caches — appointments list + rolling Google-events cache
        try:
            from web.services.cache_service import invalidate_appointments, purge_calendar

            await invalidate_appointments()
            await purge_calendar(therapist_id)
        except Exception:
            pass

        return JSONResponse(
            {
                "ok": True,
                "appointment_id": appt_id,
                "patient_id": patient_id,
                "treatment_url": f"/treatment/{patient_id}/{body.date}/{body.time.replace(':','-')}",
            }
        )
    except booking.BookingError as e:
        if e.code == "slot_taken":
            logger.info(
                f"create_manual_appointment refused, slot taken: "
                f"{therapist_id} {body.date} {body.time}"
            )
            raise HTTPException(
                status_code=409, detail="That time is already booked for you. Pick another time."
            ) from e
        raise HTTPException(status_code=e.status, detail=e.detail) from e
    except Exception as e:
        logger.error(f"create_manual_appointment failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/patients/search")
async def search_patients(request: Request, q: str = ""):
    """Return matching patients for autocomplete with full contact info (own patients only)."""
    therapist = require_active_therapist(request)
    rows = await asyncio.to_thread(appointment_repo.search_patients, q, 10, therapist["id"])
    return JSONResponse(
        {
            "results": [
                {
                    "patient_id": r["patient_id"],
                    "patient_name": r["patient_name"],
                    "patient_phone": r.get("patient_phone") or "",
                    "patient_email": r.get("patient_email") or "",
                    "source": r.get("source") or "telegram",
                }
                for r in rows
            ]
        }
    )


@router.get("/patients")
async def get_patients(request: Request):
    therapist = require_active_therapist(request)
    appointments = await appointment_service.list_all_cached()
    appointments = [a for a in appointments if a.get("therapist_id") == therapist["id"]]
    patients = appointment_service.aggregate_patients(appointments)
    return JSONResponse(patients)


@router.get("/patients/{patient_id}")
async def get_patient_detail(patient_id: int, request: Request):
    therapist = require_active_therapist(request)
    records = appointment_service.list_by_patient(patient_id, therapist["id"])
    if not records:
        raise HTTPException(status_code=404, detail="Patient not found")
    name = f"Patient {patient_id}"

    # Fetch treatment notes for each appointment to enrich EHR view
    from bot.db import get_db

    appointments = []
    for d in records:
        if d.get("patient_name") and name == f"Patient {patient_id}":
            name = d["patient_name"]

        # Rows come from the tenant-scoped list_by_patient and always carry the row id.
        apt_id = d.get("id")

        notes = {}
        if apt_id:
            row = await asyncio.to_thread(
                lambda aid=apt_id: get_db()
                .execute(
                    """SELECT tcm_pattern, treatment_principles, diagnosis_certainty,
                              used_points, completed_at,
                              followup_rating, followup_conversation,
                              manual_feedback_rating, manual_feedback_notes
                       FROM treatment_notes WHERE appointment_id=?""",
                    (aid,),
                )
                .fetchone()
            )
            if row:
                import json as _json

                r = dict(row)
                try:
                    r["used_points"] = _json.loads(r["used_points"]) if r.get("used_points") else []
                except Exception:
                    r["used_points"] = []
                try:
                    r["followup_conversation"] = (
                        _json.loads(r["followup_conversation"])
                        if r.get("followup_conversation")
                        else None
                    )
                except Exception:
                    r["followup_conversation"] = None
                notes = r

        appointments.append(
            {
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
            }
        )

    return JSONResponse({"id": patient_id, "name": name, "appointments": appointments})


@router.get("/appointment/{patient_id}/{apt_date}/{apt_time}")
async def get_appointment_detail(patient_id: int, apt_date: str, apt_time: str, request: Request):
    therapist = require_active_therapist(request)
    record = appointment_service.get_by_patient_date_time(
        patient_id, apt_date, apt_time, therapist["id"]
    )
    if not record:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return JSONResponse(record)
