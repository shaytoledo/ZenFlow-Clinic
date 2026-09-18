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

    canonical = await asyncio.to_thread(patient_repo.canonical_id, patient_id)
    history = await asyncio.to_thread(
        patient_repo.get_full_history, canonical, therapist["id"] if therapist else None
    )
    if not history:
        return RedirectResponse("/patients")
    if canonical != patient_id:  # a pre-7.2 link (one release of compatibility)
        return RedirectResponse(f"/patients/{canonical}", status_code=308)

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

    canonical = await asyncio.to_thread(patient_repo.canonical_id, patient_id)
    history = await asyncio.to_thread(
        patient_repo.get_full_history, canonical, therapist["id"] if therapist else None
    )
    if not history:
        return RedirectResponse("/patients")
    if canonical != patient_id:  # a pre-7.2 link (one release of compatibility)
        return RedirectResponse(
            f"/patients/{canonical}/session/{int(appointment_id)}", status_code=308
        )

    session = next(
        (a for a in history["appointments"] if a["appointment_id"] == appointment_id),
        None,
    )
    if not session:
        return RedirectResponse(f"/patients/{patient_id}")

    t = get_t(therapist.get("language") if therapist else None)
    from web.repositories import followup_repo, message_log_repo
    from web.services import ai_calls, audit
    from web.services.followup_view import delivery_view, followup_view
    from web.services.history_view import history_view

    checkin = await asyncio.to_thread(followup_repo.get, appointment_id)
    sent = await asyncio.to_thread(
        message_log_repo.for_appointment, therapist["id"] if therapist else "", appointment_id
    )
    trail = await asyncio.to_thread(audit.for_appointment, appointment_id)
    model_calls = await asyncio.to_thread(ai_calls.history, appointment_id)
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
            "history": history_view(
                trail,
                model_calls,
                therapist.get("language") if therapist else None,
                therapist_id=therapist["id"] if therapist else "",
            ),
        },
    )
