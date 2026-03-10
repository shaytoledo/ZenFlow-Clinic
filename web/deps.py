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

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

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


def _active_therapist_or_redirect(request: Request):
    """Return (therapist, None) if signed-in + active, or (None, redirect_url)."""
    tid = request.session.get("therapist_id")
    if not tid:
        return None, "/register"
    therapist = next((t for t in _load_therapists_fresh() if t.get("id") == tid), None)
    if not therapist:
        return None, "/register"
    if not therapist.get("active"):
        return None, "/register/activate"
    return therapist, None


def _get_session_therapist(request: Request) -> dict | None:
    tid = _get_session_therapist_id(request)
    if not tid:
        return None
    return next((t for t in _load_therapists_fresh() if t.get("id") == tid), None)


def _set_session(request: Request, therapist_id: str) -> None:
    request.session["therapist_id"] = therapist_id


def _find_by_email(email: str) -> dict | None:
    from bot.db import get_db
    email_lower = (email or "").lower().strip()
    if not email_lower:
        return None
    row = get_db().execute(
        "SELECT * FROM therapists WHERE lower(email)=?", (email_lower,)
    ).fetchone()
    if row:
        t = dict(row)
        t["active"] = bool(t.get("active"))
        return t
    return None


def _find_by_google_id(google_id: str) -> dict | None:
    from bot.db import get_db
    if not google_id:
        return None
    row = get_db().execute(
        "SELECT * FROM therapists WHERE google_id=?", (google_id,)
    ).fetchone()
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
            """INSERT INTO therapists
               (id, name, telegram_id, email, password_hash, google_id, calendar_name, active)
               VALUES (?, ?, 0, ?, ?, ?, 'ZenFlow Availability', 0)""",
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
    rows = conn.execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id"""
    ).fetchall()
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
        p["recent"].append({
            "date": apt.get("date"),
            "time": apt.get("time"),
            "summary": (apt.get("summary") or "")[:120],
            "intake_history": apt.get("intake_history", []),
        })
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


# ── Calendar pre-fetch ─────────────────────────────────────────────────────────

async def _prefetch_calendar_events(tid: str) -> None:
    """Pre-fetch the next 2 weeks of Google Calendar events into Redis (10-min TTL)."""
    from web.gcal import GCalClient, is_authenticated, token_file_for
    if not is_authenticated(tid):
        return
    try:
        from datetime import date as _date, timedelta as _td
        today = _date.today()
        start = today.isoformat() + "T00:00:00Z"
        end   = (today + _td(weeks=2)).isoformat() + "T23:59:59Z"
        cache_key = f"zenflow:gcal:events:{tid}:{start}:{end}"

        from bot.redis_client import get_async_redis
        r = get_async_redis()
        if await r.exists(cache_key):
            return

        client = await asyncio.to_thread(GCalClient.load, token_file_for(tid))
        events = await asyncio.to_thread(client.get_events, start, end)
        await r.set(cache_key, json.dumps(events, default=str), ex=600)
        logger.info(f"[{tid}] Calendar events pre-fetched into Redis ({len(events)} events)")
    except Exception as e:
        logger.debug(f"Calendar pre-fetch skipped: {e}")


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
        _set_session(request, entry["id"])
        return RedirectResponse("/register/activate", status_code=303)
    except Exception as e:
        logger.error(f"Google registration callback error: {e}")
        return RedirectResponse("/register?error=Google+sign-in+failed")
