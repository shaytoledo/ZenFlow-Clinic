"""
web/routers/pages.py
─────────────────────
All HTML page routes for the ZenFlow therapist web app.
"""
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from web.deps import _active_therapist_or_redirect, templates
from web.services.cache_service import prefetch_calendar

router = APIRouter()


def _page(request: Request, template: str, active: str, **extra) -> HTMLResponse | RedirectResponse:
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse(template, {"request": request, "active": active, "therapist": therapist, **extra})


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, background_tasks: BackgroundTasks):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    background_tasks.add_task(prefetch_calendar, therapist["id"])
    return templates.TemplateResponse("dashboard.html", {"request": request, "active": "dashboard", "therapist": therapist})


@router.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request, background_tasks: BackgroundTasks):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    background_tasks.add_task(prefetch_calendar, therapist["id"])
    return templates.TemplateResponse("schedule.html", {"request": request, "active": "schedule", "therapist": therapist})


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


@router.get("/treatment/{patient_id}/{apt_date}/{apt_time}", response_class=HTMLResponse)
async def treatment_page(request: Request, patient_id: int, apt_date: str, apt_time: str):
    return _page(request, "treatment.html", "patients")
