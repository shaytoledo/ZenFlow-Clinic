"""
web/routers/api/appointments.py
─────────────────────────────────
REST endpoints for appointments and patient data.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from web.services import appointment_service

router = APIRouter(prefix="/api")


@router.get("/appointments/today")
async def get_today_appointments():
    apts = appointment_service.list_today()
    apts.sort(key=lambda x: x.get("time", ""))
    return JSONResponse([{
        "patient_id": a["patient_id"],
        "patient_name": a.get("patient_name", ""),
        "date": a.get("date"),
        "time": a.get("time"),
        "summary": (a.get("summary") or "")[:200],
        "intake_history": a.get("intake_history", []),
    } for a in apts])


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
    appointments = []
    for d in records:
        if d.get("patient_name") and name == f"Patient {patient_id}":
            name = d["patient_name"]
        appointments.append({
            "date": d.get("date"),
            "time": d.get("time"),
            "summary": d.get("summary", ""),
            "intake_history": d["intake_history"],
            "status": d.get("status"),
        })
    return JSONResponse({"id": patient_id, "name": name, "appointments": appointments})


@router.get("/appointment/{patient_id}/{apt_date}/{apt_time}")
async def get_appointment_detail(patient_id: int, apt_date: str, apt_time: str):
    record = appointment_service.get_by_patient_date_time(patient_id, apt_date, apt_time)
    if not record:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return JSONResponse(record)
