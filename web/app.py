"""
Therapist frontend — FastAPI web app.

Run with: python run_web.py
Opens at: http://localhost:8000
"""
import json
import logging
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from bot.config import DATA_DIR, GOOGLE_CLIENT_ID
from bot.patient_bot.services.appointments import find_patient_dir
from web.gcal import GCalClient, exchange_code, get_auth_url, is_authenticated

logger = logging.getLogger(__name__)

app = FastAPI(title="ZenFlow Therapist")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login():
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(
            "<h2>GOOGLE_CLIENT_ID not set in .env — see START.md for setup.</h2>",
            status_code=500,
        )
    return RedirectResponse(get_auth_url())


@app.get("/auth/callback")
async def auth_callback(code: str):
    try:
        exchange_code(code)
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse("/")


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_all_appointments() -> list[dict]:
    """Read all appointment JSON files from data/appointments/."""
    base = Path(DATA_DIR)
    results = []
    if not base.exists():
        return results
    for f in base.glob("*/*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_filepath"] = str(f)
            results.append(data)
        except Exception as e:
            logger.warning(f"Could not read {f}: {e}")
    return results


def _aggregate_patients(appointments: list[dict]) -> list[dict]:
    """Aggregate appointment records into per-patient summaries."""
    patients: dict[int, dict] = {}
    for apt in appointments:
        pid = apt.get("patient_id")
        if not pid:
            continue
        if pid not in patients:
            patients[pid] = {
                "id": pid,
                "name": apt.get("patient_name", f"Patient {pid}"),
                "sessions": 0,
                "intake_count": 0,
                "last_appointment": None,
                "last_time": None,
                "recent": [],
            }
        p = patients[pid]
        p["sessions"] += 1
        if apt.get("intake_history"):
            p["intake_count"] += 1
        apt_date = apt.get("date", "")
        if not p["last_appointment"] or apt_date > p["last_appointment"]:
            p["last_appointment"] = apt_date
            p["last_time"] = apt.get("time", "")
        # Keep last 5 as recent appointments (lightweight)
        p["recent"].append({
            "date": apt.get("date"),
            "time": apt.get("time"),
            "summary": (apt.get("summary") or "")[:120],
            "intake_history": apt.get("intake_history", []),
        })
    # Sort each patient's recent list by date
    for p in patients.values():
        p["recent"].sort(key=lambda x: x.get("date", ""))
        p["recent"] = p["recent"][-5:]  # keep last 5
    return sorted(patients.values(), key=lambda p: p.get("last_appointment") or "", reverse=True)


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("dashboard.html", {"request": request, "active": "dashboard"})


@app.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("schedule.html", {"request": request, "active": "schedule"})


@app.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("patients.html", {"request": request, "active": "patients"})


@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("messages.html", {"request": request, "active": "messages"})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings"})


@app.get("/treatment/{patient_id}/{apt_date}/{apt_time}", response_class=HTMLResponse)
async def treatment_page(request: Request, patient_id: int, apt_date: str, apt_time: str):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("treatment.html", {"request": request, "active": "patients"})


# ── Events API (existing) ─────────────────────────────────────────────────────

@app.get("/api/calendars")
async def get_calendars():
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        return JSONResponse(GCalClient.load().get_calendar_list())
    except Exception as e:
        logger.error(f"get_calendars error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
async def get_events(start: str, end: str):
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        client = GCalClient.load()
        return JSONResponse(client.get_events(start, end))
    except Exception as e:
        logger.error(f"get_events error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SlotIn(BaseModel):
    start: str
    end: str


@app.post("/api/availability")
async def create_slot(slot: SlotIn):
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        client = GCalClient.load()
        cal_id = client.get_or_create_availability_cal()
        event = client.create_availability(cal_id, slot.start, slot.end)
        return JSONResponse(event)
    except Exception as e:
        logger.error(f"create_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/availability/{event_id}")
async def delete_slot(event_id: str, calendarId: str):
    if not is_authenticated():
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        client = GCalClient.load()
        client.delete_availability(calendarId, event_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"delete_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── New data APIs ─────────────────────────────────────────────────────────────

@app.get("/api/appointments/today")
async def get_today_appointments():
    today = date.today().isoformat()
    apts = [a for a in _load_all_appointments() if a.get("date") == today and a.get("status") == "active"]
    apts.sort(key=lambda x: x.get("time", ""))
    # Slim down response (don't send full intake history)
    return JSONResponse([{
        "patient_id": a["patient_id"],
        "patient_name": a.get("patient_name", ""),
        "date": a.get("date"),
        "time": a.get("time"),
        "summary": (a.get("summary") or "")[:200],
        "intake_history": a.get("intake_history", []),
    } for a in apts])


@app.get("/api/patients")
async def get_patients_api():
    appointments = _load_all_appointments()
    patients = _aggregate_patients(appointments)
    return JSONResponse(patients)


@app.get("/api/patients/{patient_id}")
async def get_patient_detail(patient_id: int):
    base = find_patient_dir(patient_id)
    if not base:
        raise HTTPException(status_code=404, detail="Patient not found")
    appointments = []
    for f in sorted(base.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            appointments.append({
                "date": data.get("date"),
                "time": data.get("time"),
                "summary": data.get("summary", ""),
                "intake_history": data.get("intake_history", []),
                "status": data.get("status"),
            })
        except Exception as e:
            logger.warning(f"Could not read {f}: {e}")
    name = appointments[0].get("patient_name", f"Patient {patient_id}") if appointments else f"Patient {patient_id}"
    # Get name from first appointment file we can find
    for f in sorted(base.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("patient_name"):
                name = d["patient_name"]
                break
        except Exception:
            pass
    return JSONResponse({"id": patient_id, "name": name, "appointments": appointments})


@app.get("/api/appointment/{patient_id}/{apt_date}/{apt_time}")
async def get_appointment_detail(patient_id: int, apt_date: str, apt_time: str):
    """Load a specific appointment for the treatment session view."""
    # apt_time comes as HH-MM from URL
    time_str = apt_time.replace("-", ":")
    filename = f"{apt_date}_{apt_time}.json"
    pdir = find_patient_dir(patient_id)
    if not pdir:
        raise HTTPException(status_code=404, detail="Appointment not found")
    filepath = pdir / filename
    if not filepath.exists():
        alt = f"{apt_date}_{apt_time.replace('-', ':')}.json"
        filepath = pdir / alt
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="Appointment not found")
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
        return JSONResponse(data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/messages/active")
async def get_active_messages():
    """Return count of patients currently in an active relay session."""
    try:
        relay_path = Path(DATA_DIR).parent / "relay_sessions.json"
        if relay_path.exists():
            data = json.loads(relay_path.read_text(encoding="utf-8"))
            count = len(data.get("active_patients", {}))
            return JSONResponse({"count": count})
    except Exception:
        pass
    return JSONResponse({"count": 0})
