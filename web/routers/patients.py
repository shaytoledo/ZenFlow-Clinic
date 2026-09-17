"""
web/routers/patients.py
────────────────────────
Dedicated patient EHR pages:
  GET /patients/{patient_id}                          — full patient profile
  GET /patients/{patient_id}/session/{appointment_id} — read-only session archive
"""

import asyncio

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.requests import Request

from web.deps import _active_therapist_or_redirect, templates
from web.i18n import get_t

router = APIRouter()


@router.get("/patients/{patient_id}", response_class=HTMLResponse)
async def patient_profile(request: Request, patient_id: int):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)

    from web.repositories import patient_repo

    history = await asyncio.to_thread(
        patient_repo.get_full_history, patient_id, therapist["id"] if therapist else None
    )
    if not history:
        return RedirectResponse("/patients")

    t = get_t(therapist.get("language") if therapist else None)
    return templates.TemplateResponse(
        "patient_profile.html",
        {
            "request": request,
            "active": "patients",
            "therapist": therapist,
            "patient": history,
            "t": t,
        },
    )


@router.get("/patients/{patient_id}/session/{appointment_id}", response_class=HTMLResponse)
async def session_archive(request: Request, patient_id: int, appointment_id: int):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)

    from web.repositories import patient_repo

    history = await asyncio.to_thread(
        patient_repo.get_full_history, patient_id, therapist["id"] if therapist else None
    )
    if not history:
        return RedirectResponse("/patients")

    session = next(
        (a for a in history["appointments"] if a["appointment_id"] == appointment_id),
        None,
    )
    if not session:
        return RedirectResponse(f"/patients/{patient_id}")

    t = get_t(therapist.get("language") if therapist else None)
    from web.repositories import followup_repo, message_log_repo
    from web.services.followup_view import delivery_view, followup_view

    checkin = await asyncio.to_thread(followup_repo.get, appointment_id)
    sent = await asyncio.to_thread(
        message_log_repo.for_appointment, therapist["id"] if therapist else "", appointment_id
    )
    return templates.TemplateResponse(
        "session_archive.html",
        {
            "request": request,
            "active": "patients",
            "therapist": therapist,
            "patient": history,
            "session": session,
            "t": t,
            "fu": followup_view(checkin, therapist.get("language") if therapist else None),
            "deliveries": delivery_view(sent, therapist.get("language") if therapist else None),
        },
    )
