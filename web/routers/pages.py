"""
web/routers/pages.py
─────────────────────
All HTML page routes for the ZenFlow therapist web app.
"""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web.deps import _active_therapist_or_redirect, resolve_owned_appointment, templates
from web.i18n import get_t
from web.services.cache_service import prefetch_calendar

router = APIRouter()


def _page(request: Request, template: str, active: str, **extra) -> HTMLResponse | RedirectResponse:
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    t = get_t(therapist.get("language") if therapist else None)
    return templates.TemplateResponse(
        template, {"request": request, "active": active, "therapist": therapist, "t": t, **extra}
    )


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, background_tasks: BackgroundTasks):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    background_tasks.add_task(prefetch_calendar, therapist["id"])
    t = get_t(therapist.get("language"))
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "active": "dashboard", "therapist": therapist, "t": t},
    )


@router.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request, background_tasks: BackgroundTasks):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    background_tasks.add_task(prefetch_calendar, therapist["id"])
    t = get_t(therapist.get("language"))
    return templates.TemplateResponse(
        "schedule.html", {"request": request, "active": "schedule", "therapist": therapist, "t": t}
    )


@router.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request):
    return _page(request, "patients.html", "patients")


@router.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request):
    return _page(request, "messages.html", "messages")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return _page(request, "settings.html", "settings")


@router.get("/sessions", response_class=HTMLResponse)
async def sessions_history_page(request: Request):
    return _page(request, "sessions.html", "sessions")


#: strings the treatment page's email dialog shows (static/js/treatment/email-dialog.js, Phase 5.3)
EMAIL_DIALOG_KEYS = (
    "btn_cancel",
    "btn_close",
    "email_not_connected_title",
    "email_not_connected_body",
    "email_token_expired_title",
    "email_token_expired_body",
    "email_connect_google_btn",
    "email_reconnect_google_btn",
    "email_copy_instead_btn",
    "email_google_hint",
    "email_only_patient_hint",
    "email_google_connected_banner",
    "email_google_cancelled_banner",
    "email_dialog_no_telegram_title",
    "email_dialog_ask_text",
    "email_dialog_address_label",
    "email_dialog_send",
    "email_dialog_sending",
    "email_dialog_invalid",
    "email_dialog_failed",
    "email_dialog_copy_title",
    "email_dialog_copy_hint",
    "email_dialog_copy_btn",
    "email_dialog_copied",
    "email_dialog_copy_failed",
)


@router.get("/treatment/{patient_id}/{apt_date}/{apt_time}", response_class=HTMLResponse)
async def treatment_page(request: Request, patient_id: int, apt_date: str, apt_time: str):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    try:
        resolve_owned_appointment(request, patient_id, apt_date, apt_time)
    except HTTPException:  # not found / not this therapist's → back to the list, like other pages
        return RedirectResponse("/patients")
    from web.repositories import therapist_repo
    from web.services.email_service import google_connection
    from zenflow.settings import get_settings

    prefs = await asyncio.to_thread(therapist_repo.get_ui_prefs, therapist["id"])
    google = await asyncio.to_thread(google_connection, therapist["id"])
    t = get_t(therapist.get("language"))
    return _page(
        request,
        "treatment.html",
        "patients",
        sse_updates=get_settings().flags.sse_updates,
        point_density=prefs["point_density"],
        google=google.as_dict(),
        email_text={key: t[key] for key in EMAIL_DIALOG_KEYS},
    )


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    """Welcome page shown to newly-registered therapists before bot activation."""
    from web.deps import _get_session_therapist

    therapist = _get_session_therapist(request)
    if not therapist:
        return RedirectResponse("/register")
    # If already active, skip onboarding
    if therapist.get("active"):
        return RedirectResponse("/")
    return templates.TemplateResponse(
        "onboarding.html",
        {"request": request, "therapist_name": therapist.get("name", "")},
    )
