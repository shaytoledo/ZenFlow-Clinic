"""Shared dependencies for web routes: templates, session helpers, data helpers."""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import string
import threading
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from bot.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from zenflow.clock import SQL_NOW

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Stored instants are UTC (ADR-19); templates render them in the clinic's zone.
from zenflow import clock as _clock  # noqa: E402

templates.env.filters["clinic_date"] = _clock.format_clinic
templates.env.filters["clinic_datetime"] = lambda v: _clock.format_clinic(v, "%Y-%m-%d %H:%M")

# ── Auth / session helpers ─────────────────────────────────────────────────────

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


def _load_therapists_fresh() -> list[dict]:
    """Read therapists from SQLite so bot-process writes are visible immediately."""
    from bot.db import get_db

    conn = get_db()
    rows = conn.execute("SELECT * FROM therapists").fetchall()
    result = [dict(row) for row in rows]
    for t in result:
        t["active"] = bool(t.get("active"))
    return result


def _load_therapist(request: Request, tid: str) -> dict | None:
    """One keyed read per request: cached on request.state (review fix — was a full-table scan
    repeated by every auth helper on every request)."""
    cached = getattr(request.state, "therapist", None)
    if isinstance(cached, dict) and cached.get("id") == tid:
        return cached
    from web.repositories import therapist_repo

    therapist = therapist_repo.get_by_id(tid)
    if therapist:
        therapist["active"] = bool(therapist.get("active"))
        request.state.therapist = therapist
    return therapist


def _active_therapist_or_redirect(request: Request):
    """Return (therapist, None) if signed-in + active, or (None, redirect_url)."""
    tid = request.session.get("therapist_id")
    if not tid:
        return None, "/register"
    therapist = _load_therapist(request, tid)
    if not therapist:
        return None, "/register"
    if not therapist.get("active"):
        return None, "/onboarding"
    return therapist, None


def _get_session_therapist(request: Request) -> dict | None:
    tid = _get_session_therapist_id(request)
    if not tid:
        return None
    return _load_therapist(request, tid)


def _set_session(request: Request, therapist_id: str) -> None:
    request.session["therapist_id"] = therapist_id


def _find_by_email(email: str) -> dict | None:
    from bot.db import get_db

    email_lower = (email or "").lower().strip()
    if not email_lower:
        return None
    row = (
        get_db().execute("SELECT * FROM therapists WHERE lower(email)=?", (email_lower,)).fetchone()
    )
    if row:
        t = dict(row)
        t["active"] = bool(t.get("active"))
        return t
    return None


def _find_by_google_id(google_id: str) -> dict | None:
    from bot.db import get_db

    if not google_id:
        return None
    row = get_db().execute("SELECT * FROM therapists WHERE google_id=?", (google_id,)).fetchone()
    if row:
        t = dict(row)
        t["active"] = bool(t.get("active"))
        return t
    return None


def _register_web_therapist(name: str, email: str, password: str = "", google_id: str = "") -> dict:
    """Add a new web-registered therapist (telegram_id=0, active=False until bot activation)."""
    from bot import config as _cfg
    from bot.db import get_db

    conn = get_db()
    with _web_reg_lock:
        existing_ids = {r[0] for r in conn.execute("SELECT id FROM therapists").fetchall()}
        n = 1
        while f"t{n}" in existing_ids:
            n += 1
        new_id = f"t{n}"
        password_hash = _hash_password(password) if password else None
        conn.execute(
            f"""INSERT INTO therapists
               (id, name, telegram_id, email, password_hash, google_id, calendar_name, active,
                created_at)
               VALUES (?, ?, 0, ?, ?, ?, 'ZenFlow Availability', 0, {SQL_NOW})""",
            (new_id, name, email or None, password_hash, google_id or None),
        )
        conn.commit()
        entry: dict = {
            "id": new_id,
            "name": name,
            "telegram_id": 0,
            "calendar_name": "ZenFlow Availability",
            "active": False,
        }
        if email:
            entry["email"] = email
        if google_id:
            entry["google_id"] = google_id
        if password_hash:
            entry["password_hash"] = password_hash
        _cfg.THERAPISTS.append(entry)
    return entry


# ── Data helpers ───────────────────────────────────────────────────────────────


def _load_all_appointments() -> list[dict]:
    """Load all appointments from SQLite (joins intake_sessions for history)."""
    from bot.db import get_db

    conn = get_db()
    rows = conn.execute("""SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id""").fetchall()
    results = []
    for row in rows:
        d = dict(row)
        hj = d.pop("history_json", None)
        d["intake_history"] = json.loads(hj) if hj else []
        results.append(d)
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
        p["recent"].append(
            {
                "date": apt.get("date"),
                "time": apt.get("time"),
                "summary": (apt.get("summary") or "")[:120],
                "intake_history": apt.get("intake_history", []),
            }
        )
    for p in patients.values():
        p["recent"].sort(key=lambda x: x.get("date", ""))
        p["recent"] = p["recent"][-5:]
    return sorted(patients.values(), key=lambda p: p.get("last_appointment") or "", reverse=True)


# ── Local availability helpers ─────────────────────────────────────────────────


def _load_local_avail(therapist_id: str | None) -> list[dict]:
    """Read local availability slots for a therapist from SQLite."""
    from bot.db import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT id, start_dt AS start, end_dt AS end FROM availability WHERE therapist_id=?",
        (therapist_id or "default",),
    ).fetchall()
    return [dict(row) for row in rows]


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


# ── Registration helpers ───────────────────────────────────────────────────────

_REG_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
_REG_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
_therapist_bot_username: str = ""
_patient_bot_username: str = ""


def _generate_reg_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


async def _get_therapist_bot_username() -> str:
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


def _make_reg_flow():
    """Build a Google OAuth flow for sign-in/registration."""
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
    """Handle Google OAuth callback for registration/sign-in."""
    from fastapi.responses import RedirectResponse

    if error or not code:
        return RedirectResponse("/register?error=Google+sign-in+was+cancelled")
    try:
        from googleapiclient.discovery import build as _build

        flow = _make_reg_flow()
        await asyncio.to_thread(flow.fetch_token, code=code)
        creds = flow.credentials
        user_info = await asyncio.to_thread(
            lambda: _build("oauth2", "v2", credentials=creds, cache_discovery=False)
            .userinfo()
            .get()
            .execute()
        )
        name = user_info.get("name") or user_info.get("given_name") or "Unknown"
        email = (user_info.get("email") or "").lower()
        google_id = user_info.get("id") or ""

        existing = _find_by_google_id(google_id) or (email and _find_by_email(email))
        if existing:
            _set_session(request, existing["id"])
            from web.services.cache_service import prefetch_calendar

            asyncio.create_task(prefetch_calendar(existing["id"]))
            return RedirectResponse("/", status_code=303)

        entry = _register_web_therapist(name=name, email=email, google_id=google_id)
        _set_session(request, entry["id"])
        return RedirectResponse("/onboarding", status_code=303)
    except Exception as e:
        logger.error(f"Google registration callback error: {e}")
        return RedirectResponse("/register?error=Google+sign-in+failed")


# ── Authorization dependencies (Phase 0.5 / F6 / SF-005) ──────────────────────────────────────
# `require_signed_in` is attached at router level to EVERY /api router in web/app.py, so it runs
# before body validation: an anonymous caller gets 401, never a 422 that leaks the schema.
# Object-level checks (`require_appointment_access`, `resolve_owned_appointment`) make sure a
# signed-in therapist can only reach their OWN appointments — never another tenant's.


def require_signed_in(request: Request) -> dict:
    """Session carries an existing therapist id (active or not). 401 otherwise."""
    from fastapi import HTTPException

    therapist = _get_session_therapist(request)
    if not therapist:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return therapist


def require_active_therapist(request: Request) -> dict:
    """Signed in AND activated. 401 otherwise (API semantics — no redirects)."""
    from fastapi import HTTPException

    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect or therapist is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return therapist


def require_appointment_access(request: Request, appointment_id: int) -> dict:
    """Load an appointment by row id; 404 if missing, 403 if it belongs to another therapist."""
    from fastapi import HTTPException

    from web.repositories import appointment_repo

    therapist = require_active_therapist(request)
    apt = appointment_repo.get_by_id(appointment_id)
    if not apt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if apt.get("therapist_id") != therapist["id"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    return apt


def resolve_owned_appointment(
    request: Request, patient_id: int, apt_date: str, apt_time: str
) -> dict:
    """Resolve patient/date/time to THIS therapist's appointment; 404 otherwise (no existence leak)."""
    from fastapi import HTTPException

    from web.repositories import appointment_repo

    therapist = require_active_therapist(request)
    apt = appointment_repo.get_by_patient_date_time(
        patient_id, apt_date, apt_time, therapist_id=therapist["id"]
    )
    if not apt:
        raise HTTPException(status_code=404, detail="Appointment not found")
    return apt
