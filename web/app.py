"""
Therapist frontend — FastAPI web app.

Run with: python run_web.py
Opens at: http://localhost:8000
"""
import asyncio
import hashlib
import json
import logging
import re
import secrets
import string
import threading
import uuid
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from bot.config import DATA_DIR, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, SESSION_SECRET
from bot.patient_bot.services.appointments import find_patient_dir
from web.gcal import GCalClient, exchange_code, get_auth_url, is_authenticated, token_file_for

logger = logging.getLogger(__name__)

app = FastAPI(title="ZenFlow Therapist")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="zf_session", max_age=86400 * 30)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ── Session / password helpers ────────────────────────────────────────────────

_web_reg_lock = threading.Lock()


def _hash_password(plain: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260_000)
    return f"{salt}:{h.hex()}"


def _verify_password(plain: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        expected = hashlib.pbkdf2_hmac("sha256", plain.encode(), salt.encode(), 260_000).hex()
        return secrets.compare_digest(expected, h)
    except Exception:
        return False


def _get_session_therapist_id(request: Request) -> str | None:
    return request.session.get("therapist_id")


def _active_therapist_or_redirect(request: Request):
    """Return (therapist, None) if signed-in + active, or (None, redirect_url)."""
    tid = request.session.get("therapist_id")
    if not tid:
        return None, "/register"
    from bot.config import THERAPISTS
    therapist = next((t for t in THERAPISTS if t.get("id") == tid), None)
    if not therapist:
        return None, "/register"
    if not therapist.get("active"):
        return None, "/register/activate"
    return therapist, None


def _get_session_therapist(request: Request) -> dict | None:
    tid = _get_session_therapist_id(request)
    if not tid:
        return None
    from bot.config import THERAPISTS
    return next((t for t in THERAPISTS if t.get("id") == tid), None)


def _set_session(request: Request, therapist_id: str) -> None:
    request.session["therapist_id"] = therapist_id


def _find_by_email(email: str) -> dict | None:
    from bot.config import THERAPISTS
    email_lower = (email or "").lower().strip()
    if not email_lower:
        return None
    return next((t for t in THERAPISTS if (t.get("email") or "").lower() == email_lower), None)


def _find_by_google_id(google_id: str) -> dict | None:
    from bot.config import THERAPISTS
    if not google_id:
        return None
    return next((t for t in THERAPISTS if t.get("google_id") == google_id), None)


def _register_web_therapist(name: str, email: str, password: str = "", google_id: str = "") -> dict:
    """Add a new web-registered therapist (telegram_id=0, active=False until bot activation)."""
    from bot import config as _cfg

    path = Path(_cfg.DATA_DIR).parent / "therapists.json"
    with _web_reg_lock:
        therapists = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        existing_ids = {t["id"] for t in therapists}
        n = 1
        while f"t{n}" in existing_ids:
            n += 1
        entry: dict = {
            "id": f"t{n}",
            "name": name,
            "telegram_id": 0,
            "calendar_name": "ZenFlow Availability",
            "active": False,
        }
        if email:
            entry["email"] = email
        if google_id:
            entry["google_id"] = google_id
        if password:
            entry["password_hash"] = _hash_password(password)
        therapists.append(entry)
        path.write_text(json.dumps(therapists, indent=2, ensure_ascii=False), encoding="utf-8")
        _cfg.THERAPISTS.clear()
        _cfg.THERAPISTS.extend(therapists)
    return entry


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/auth/login")
async def auth_login():
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(
            "<h2>GOOGLE_CLIENT_ID not set in .env — see START.md for setup.</h2>",
            status_code=500,
        )
    return RedirectResponse(get_auth_url())


@app.post("/auth/disconnect")
async def auth_disconnect(request: Request):
    """Delete the Google Calendar token for the signed-in therapist."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect, status_code=303)
    tf = token_file_for(therapist["id"])
    if tf.exists():
        tf.unlink()
    return RedirectResponse("/settings", status_code=303)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    # If this callback is for registration (flag set in session by /register/google)
    if request.session.pop("reg_google", False):
        return await _handle_reg_google(request, code, error)
    # Otherwise it's a Google Calendar auth
    if error or not code:
        return RedirectResponse("/settings?error=Google+auth+cancelled")
    try:
        therapist = _get_session_therapist(request)
        tf = token_file_for(therapist["id"]) if therapist else None
        exchange_code(code, tf)
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


async def _load_all_appointments_cached() -> list[dict]:
    """Return all appointments, cached in Redis for 30 seconds."""
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        cached = await r.get("zenflow:apts:all")
        if cached:
            return json.loads(cached)
        data = await asyncio.to_thread(_load_all_appointments)
        await r.set("zenflow:apts:all", json.dumps(data, default=str), ex=30)
        return data
    except Exception:
        return await asyncio.to_thread(_load_all_appointments)


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


# ── Local availability helpers (used when Google Calendar is not connected) ───

def _local_avail_path(therapist_id: str | None) -> Path:
    return Path(DATA_DIR).parent / f"local_avail_{therapist_id or 'default'}.json"


def _load_local_avail(therapist_id: str | None) -> list[dict]:
    p = _local_avail_path(therapist_id)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_local_avail(therapist_id: str | None, slots: list[dict]) -> None:
    p = _local_avail_path(therapist_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(slots, indent=2, ensure_ascii=False), encoding="utf-8")


def _local_slots_to_fc(slots: list[dict]) -> list[dict]:
    """Convert local availability slots to FullCalendar event dicts."""
    return [
        {
            "id": s["id"],
            "title": "✅ Available",
            "start": s["start"],
            "end": s["end"],
            "backgroundColor": "#27ae60",
            "borderColor": "#1e8449",
            "editable": False,
            "extendedProps": {"type": "available", "calendarId": "local"},
        }
        for s in slots
    ]


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse("dashboard.html", {"request": request, "active": "dashboard", "therapist": therapist})


@app.get("/schedule", response_class=HTMLResponse)
async def schedule(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse("schedule.html", {"request": request, "active": "schedule", "therapist": therapist})


@app.get("/patients", response_class=HTMLResponse)
async def patients_page(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse("patients.html", {"request": request, "active": "patients", "therapist": therapist})


@app.get("/messages", response_class=HTMLResponse)
async def messages_page(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse("messages.html", {"request": request, "active": "messages", "therapist": therapist})


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse("settings.html", {"request": request, "active": "settings", "therapist": therapist})


@app.get("/treatment/{patient_id}/{apt_date}/{apt_time}", response_class=HTMLResponse)
async def treatment_page(request: Request, patient_id: int, apt_date: str, apt_time: str):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect)
    return templates.TemplateResponse("treatment.html", {"request": request, "active": "patients", "therapist": therapist})


# ── Events API (existing) ─────────────────────────────────────────────────────

@app.get("/api/calendars")
async def get_calendars(request: Request):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        return JSONResponse(await asyncio.to_thread(client.get_calendar_list))
    except Exception as e:
        logger.error(f"get_calendars error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/events")
async def get_events(request: Request, start: str, end: str):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        # No Google Calendar — return local availability slots only
        slots = await asyncio.to_thread(_load_local_avail, tid)
        return JSONResponse(_local_slots_to_fc(slots))
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        return JSONResponse(await asyncio.to_thread(client.get_events, start, end))
    except Exception as e:
        logger.error(f"get_events error: {e}")
        return JSONResponse([])


class SlotIn(BaseModel):
    start: str
    end: str


@app.post("/api/availability")
async def create_slot(request: Request, slot: SlotIn):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        # Save to local availability file
        slots = await asyncio.to_thread(_load_local_avail, tid)
        new_slot = {"id": uuid.uuid4().hex, "start": slot.start, "end": slot.end}
        slots.append(new_slot)
        await asyncio.to_thread(_save_local_avail, tid, slots)
        return JSONResponse({
            "id": new_slot["id"],
            "title": "✅ Available",
            "start": slot.start,
            "end": slot.end,
            "backgroundColor": "#27ae60",
            "borderColor": "#1e8449",
            "extendedProps": {"type": "available", "calendarId": "local"},
        })
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        cal_id = await asyncio.to_thread(client.get_or_create_availability_cal)
        event = await asyncio.to_thread(client.create_availability, cal_id, slot.start, slot.end)
        return JSONResponse(event)
    except Exception as e:
        logger.error(f"create_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/availability/{event_id}")
async def delete_slot(request: Request, event_id: str, calendarId: str = "local"):
    tid = _get_session_therapist_id(request)
    if not is_authenticated(tid):
        # Delete from local availability file
        slots = await asyncio.to_thread(_load_local_avail, tid)
        slots = [s for s in slots if s["id"] != event_id]
        await asyncio.to_thread(_save_local_avail, tid, slots)
        return JSONResponse({"ok": True})
    try:
        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        await asyncio.to_thread(client.delete_availability, calendarId, event_id)
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.error(f"delete_slot error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── New data APIs ─────────────────────────────────────────────────────────────

@app.get("/api/appointments/today")
async def get_today_appointments():
    today = date.today().isoformat()
    apts = [a for a in await _load_all_appointments_cached() if a.get("date") == today and a.get("status") == "active"]
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
    appointments = await _load_all_appointments_cached()
    patients = _aggregate_patients(appointments)
    return JSONResponse(patients)


@app.get("/api/patients/{patient_id}")
async def get_patient_detail(patient_id: int):
    base = find_patient_dir(patient_id)
    if not base:
        raise HTTPException(status_code=404, detail="Patient not found")
    appointments = []
    name = f"Patient {patient_id}"
    for f in sorted(base.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("patient_name") and name == f"Patient {patient_id}":
                name = data["patient_name"]
            appointments.append({
                "date": data.get("date"),
                "time": data.get("time"),
                "summary": data.get("summary", ""),
                "intake_history": data.get("intake_history", []),
                "status": data.get("status"),
            })
        except Exception as e:
            logger.warning(f"Could not read {f}: {e}")
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
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = await r.keys("zenflow:relay:active:*")
        return JSONResponse({"count": len(keys)})
    except Exception:
        pass
    return JSONResponse({"count": 0})


# ── System status ─────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_system_status(request: Request):
    """Check every service and return a health snapshot."""
    import httpx
    from bot.config import TELEGRAM_TOKEN, THERAPIST_BOT_TOKEN, OLLAMA_HOST, OLLAMA_MODEL, THERAPISTS

    out: dict[str, dict] = {}

    # Redis
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        await r.ping()
        out["redis"] = {"ok": True, "label": "Redis", "detail": "Connected"}
    except Exception as e:
        out["redis"] = {"ok": False, "label": "Redis", "detail": str(e)[:80]}

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{OLLAMA_HOST}/api/tags")
            models = [m["name"] for m in resp.json().get("models", [])]
            model_ok = any(OLLAMA_MODEL in m for m in models)
            out["ollama"] = {
                "ok": model_ok,
                "label": "Ollama",
                "detail": f"Model '{OLLAMA_MODEL}' ready" if model_ok else f"Model '{OLLAMA_MODEL}' not found",
            }
    except Exception:
        out["ollama"] = {"ok": False, "label": "Ollama", "detail": "Not running"}

    # Patient bot (Telegram API reachable with token)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe")
            data = resp.json()
            if data.get("ok"):
                out["patient_bot"] = {"ok": True, "label": "Patient Bot", "detail": f"@{data['result']['username']}"}
            else:
                out["patient_bot"] = {"ok": False, "label": "Patient Bot", "detail": data.get("description", "Invalid token")}
    except Exception:
        out["patient_bot"] = {"ok": False, "label": "Patient Bot", "detail": "Unreachable"}

    # Therapist bot
    if THERAPIST_BOT_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"https://api.telegram.org/bot{THERAPIST_BOT_TOKEN}/getMe")
                data = resp.json()
                if data.get("ok"):
                    out["therapist_bot"] = {"ok": True, "label": "Therapist Bot", "detail": f"@{data['result']['username']}"}
                else:
                    out["therapist_bot"] = {"ok": False, "label": "Therapist Bot", "detail": data.get("description", "Invalid token")}
        except Exception:
            out["therapist_bot"] = {"ok": False, "label": "Therapist Bot", "detail": "Unreachable"}
    else:
        out["therapist_bot"] = {"ok": False, "label": "Therapist Bot", "detail": "Token not configured"}

    # Google Calendar (check the requesting therapist's token)
    _tid = request.session.get("therapist_id") if hasattr(request, "session") else None
    _gcal_ok = is_authenticated(_tid)
    out["google_calendar"] = {
        "ok": _gcal_ok,
        "label": "Google Calendar",
        "detail": "Authenticated" if _gcal_ok else "Not connected — go to Settings",
    }

    # Active relay sessions
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = await r.keys("zenflow:relay:active:*")
        n = len(keys)
        out["relay"] = {"ok": True, "label": "Active Chats", "detail": f"{n} patient{'s' if n != 1 else ''} in relay"}
    except Exception:
        out["relay"] = {"ok": False, "label": "Active Chats", "detail": "Redis unavailable"}

    # Therapist registry
    active = [t for t in THERAPISTS if t.get("active")]
    out["therapists"] = {
        "ok": len(active) > 0,
        "label": "Therapists",
        "detail": f"{len(active)} active" if active else "No therapists registered",
    }

    return JSONResponse(out)


# ── Therapist self-registration ───────────────────────────────────────────────

_REG_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
_therapist_bot_username: str = ""


async def _get_therapist_bot_username() -> str:
    """Fetch and cache the therapist bot's Telegram username."""
    global _therapist_bot_username
    if _therapist_bot_username:
        return _therapist_bot_username
    try:
        import httpx
        from bot.config import THERAPIST_BOT_TOKEN
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{THERAPIST_BOT_TOKEN}/getMe")
            data = resp.json()
            if data.get("ok"):
                _therapist_bot_username = data["result"]["username"]
    except Exception:
        pass
    return _therapist_bot_username
_patient_bot_username: str = ""


async def _get_patient_bot_username() -> str:
    global _patient_bot_username
    if _patient_bot_username:
        return _patient_bot_username
    try:
        import httpx
        from bot.config import TELEGRAM_TOKEN
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getMe")
            data = resp.json()
            if data.get("ok"):
                _patient_bot_username = data["result"]["username"]
    except Exception:
        pass
    return _patient_bot_username


_REG_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _generate_reg_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _make_reg_flow():
    """Build a Google OAuth flow for sign-in/registration using the already-configured redirect URI."""
    from google_auth_oauthlib.flow import Flow
    from bot.config import GOOGLE_REDIRECT_URI as _REDIR
    config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [_REDIR],
        }
    }
    return Flow.from_client_config(config, scopes=_REG_SCOPES, redirect_uri=_REDIR)


async def _handle_reg_google(request: Request, code: str, error: str = ""):
    """Handle Google OAuth callback for registration/sign-in (shared with /auth/callback)."""
    if error or not code:
        return RedirectResponse("/register?error=Google+sign-in+was+cancelled")
    try:
        from googleapiclient.discovery import build as _build
        from bot.redis_client import get_async_redis
        flow = _make_reg_flow()
        await asyncio.to_thread(flow.fetch_token, code=code)
        creds = flow.credentials
        user_info = await asyncio.to_thread(
            lambda: _build("oauth2", "v2", credentials=creds, cache_discovery=False)
                        .userinfo().get().execute()
        )
        name = user_info.get("name") or user_info.get("given_name") or "Unknown"
        email = (user_info.get("email") or "").lower()
        google_id = user_info.get("id") or ""

        existing = _find_by_google_id(google_id) or (email and _find_by_email(email))
        if existing:
            _set_session(request, existing["id"])
            return RedirectResponse("/", status_code=303)

        entry = _register_web_therapist(name=name, email=email, google_id=google_id)
        reg_code = _generate_reg_code()
        r = get_async_redis()
        await r.set(
            f"zenflow:reg:{reg_code}",
            json.dumps({"name": name, "email": email, "google_id": google_id}),
            ex=600,
        )
        _set_session(request, entry["id"])
        return RedirectResponse(f"/register/done?code={reg_code}", status_code=303)
    except Exception as e:
        logger.error(f"Google registration callback error: {e}")
        return RedirectResponse("/register?error=Google+sign-in+failed")


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, tab: str = "register", error: str = "", name: str = "", email: str = ""):
    # Only redirect fully active users away from the login page
    # Inactive users must be able to reach the login form to sign in as a different account
    tid = request.session.get("therapist_id")
    if tid:
        from bot.config import THERAPISTS as _T
        t = next((x for x in _T if x.get("id") == tid), None)
        if t and t.get("active"):
            return RedirectResponse("/")
    return templates.TemplateResponse("register.html", {
        "request": request,
        "tab": tab,
        "google_enabled": bool(GOOGLE_CLIENT_ID),
        "error": error,
        "name": name,
        "email": email,
    })


@app.post("/register/signup", response_class=HTMLResponse)
async def register_signup(request: Request):
    from bot.redis_client import get_async_redis
    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = (form.get("password") or "").strip()

    def _err(msg: str):
        return templates.TemplateResponse("register.html", {
            "request": request, "tab": "register",
            "google_enabled": bool(GOOGLE_CLIENT_ID),
            "error": msg, "name": name, "email": email,
        })

    if not name:
        return _err("Name is required.")
    if not email:
        return _err("Email is required.")
    if not password:
        return _err("Password is required.")
    if _find_by_email(email):
        return _err("This email is already registered. Please sign in instead.")

    entry = _register_web_therapist(name=name, email=email, password=password)
    code = _generate_reg_code()
    r = get_async_redis()
    await r.set(
        f"zenflow:reg:{code}",
        json.dumps({"name": entry["name"], "email": entry.get("email", ""), "google_id": ""}),
        ex=600,
    )
    _set_session(request, entry["id"])
    return RedirectResponse(f"/register/done?code={code}", status_code=303)


@app.post("/register/signin", response_class=HTMLResponse)
async def register_signin(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = (form.get("password") or "").strip()

    def _err(msg: str):
        return templates.TemplateResponse("register.html", {
            "request": request, "tab": "signin",
            "google_enabled": bool(GOOGLE_CLIENT_ID),
            "error": msg, "name": "", "email": email,
        })

    if not email or not password:
        return _err("Email and password are required.")
    therapist = _find_by_email(email)
    if not therapist:
        return _err("Invalid email or password.")
    if not therapist.get("password_hash"):
        return _err("This account uses Google sign-in. Please click 'Continue with Google' instead.")
    if not _verify_password(password, therapist["password_hash"]):
        return _err("Invalid email or password.")

    _set_session(request, therapist["id"])
    # Active → dashboard; inactive → activation page
    dest = "/" if therapist.get("active") else "/register/activate"
    return RedirectResponse(dest, status_code=303)


@app.get("/register/activate", response_class=HTMLResponse)
async def register_activate(request: Request):
    """Shown to registered-but-not-yet-activated therapists."""
    tid = request.session.get("therapist_id")
    if not tid:
        return RedirectResponse("/register")
    from bot.config import THERAPISTS as _T
    therapist = next((x for x in _T if x.get("id") == tid), None)
    if not therapist:
        return RedirectResponse("/register")
    if therapist.get("active"):
        return RedirectResponse("/")
    therapist_username = await _get_therapist_bot_username()
    return templates.TemplateResponse("register_activate.html", {
        "request": request,
        "therapist": therapist,
        "therapist_bot_username": therapist_username,
        "therapist_bot_link": f"https://t.me/{therapist_username}" if therapist_username else "",
    })


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/register", status_code=303)


@app.get("/register/google")
async def register_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse("/register?error=Google+sign-in+is+not+configured")
    # Flag the session so /auth/callback knows this is a registration flow, not calendar auth
    request.session["reg_google"] = True
    flow = _make_reg_flow()
    url, _ = flow.authorization_url(prompt="select_account", access_type="offline")
    return RedirectResponse(url)


@app.get("/register/google/callback")
async def register_google_callback(request: Request, code: str = "", error: str = ""):
    """Fallback handler — main flow now goes through /auth/callback."""
    return await _handle_reg_google(request, code, error)


@app.get("/register/done", response_class=HTMLResponse)
async def register_done(request: Request, code: str = ""):
    from bot.redis_client import get_async_redis
    if not code or not _REG_CODE_RE.match(code):
        return templates.TemplateResponse("register_done.html", {
            "request": request, "error": "Invalid or missing registration code.",
            "code": "", "name": "",
        })
    r = get_async_redis()
    raw = await r.get(f"zenflow:reg:{code}")
    if not raw:
        return templates.TemplateResponse("register_done.html", {
            "request": request, "error": "This code has expired or was already used.",
            "code": "", "name": "",
        })
    info = json.loads(raw)
    therapist_username = await _get_therapist_bot_username()
    patient_username = await _get_patient_bot_username()
    return templates.TemplateResponse("register_done.html", {
        "request": request,
        "error": "",
        "code": code,
        "name": info.get("name", "Therapist"),
        "therapist_bot_username": therapist_username,
        "therapist_bot_link": f"https://t.me/{therapist_username}" if therapist_username else "",
        "patient_bot_username": patient_username,
        "patient_bot_link": f"https://t.me/{patient_username}" if patient_username else "",
    })


@app.get("/api/my/status")
async def get_my_status(request: Request):
    """Return activation status for the signed-in therapist (used by activation page poller)."""
    tid = request.session.get("therapist_id")
    if not tid:
        raise HTTPException(status_code=401, detail="Not signed in")
    from bot.config import THERAPISTS
    therapist = next((t for t in THERAPISTS if t.get("id") == tid), None)
    if not therapist:
        raise HTTPException(status_code=404, detail="Therapist not found")
    return JSONResponse({"active": bool(therapist.get("active")), "name": therapist.get("name", "")})


@app.get("/api/my/activation-code")
async def get_my_activation_code(request: Request):
    """Generate a fresh bot activation code for the signed-in therapist."""
    therapist = _get_session_therapist(request)
    if not therapist:
        raise HTTPException(status_code=401, detail="Not signed in")
    from bot.redis_client import get_async_redis
    code = _generate_reg_code()
    r = get_async_redis()
    await r.set(
        f"zenflow:reg:{code}",
        json.dumps({"name": therapist["name"], "email": therapist.get("email", ""), "google_id": therapist.get("google_id", "")}),
        ex=600,
    )
    therapist_username = await _get_therapist_bot_username()
    patient_username = await _get_patient_bot_username()
    return JSONResponse({
        "code": code,
        "therapist_bot_link": f"https://t.me/{therapist_username}" if therapist_username else "",
        "patient_bot_link": f"https://t.me/{patient_username}" if patient_username else "",
        "therapist_bot_username": therapist_username,
        "patient_bot_username": patient_username,
    })
