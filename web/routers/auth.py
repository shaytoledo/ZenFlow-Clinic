"""
web/routers/auth.py
────────────────────
Authentication routes: Google OAuth, register/sign-in, logout.
"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.config import GOOGLE_CLIENT_ID
from web.deps import (
    _active_therapist_or_redirect,
    _find_by_email,
    _find_by_google_id,
    _generate_reg_code,
    _get_patient_bot_username,
    _get_session_therapist,
    _get_therapist_bot_username,
    _handle_reg_google,
    _load_therapists_fresh,
    _make_reg_flow,
    _register_web_therapist,
    _set_session,
    _verify_password,
    templates,
)
from web.gcal import exchange_code, get_auth_url, is_authenticated, token_file_for
from web.services.cache_service import prefetch_calendar, purge_calendar

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Google Calendar OAuth ──────────────────────────────────────────────────────

@router.get("/auth/login")
async def auth_login():
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(
            "<h2>GOOGLE_CLIENT_ID not set in .env — see START.md for setup.</h2>",
            status_code=500,
        )
    return RedirectResponse(get_auth_url())


@router.post("/auth/disconnect")
async def auth_disconnect(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect, status_code=303)
    tid = therapist["id"]
    tf = token_file_for(tid)
    if tf.exists():
        tf.unlink()
    await purge_calendar(tid)
    return RedirectResponse("/settings", status_code=303)


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    if request.session.pop("reg_google", False):
        return await _handle_reg_google(request, code, error)
    if error or not code:
        return RedirectResponse("/settings?error=Google+auth+cancelled")
    try:
        therapist = _get_session_therapist(request)
        tf = token_file_for(therapist["id"]) if therapist else None
        exchange_code(code, tf)
        # Pre-warm calendar cache after connecting
        if therapist:
            asyncio.create_task(prefetch_calendar(therapist["id"]))
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return RedirectResponse("/")


# ── Registration ───────────────────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
async def register_page(
    request: Request,
    tab: str = "register",
    error: str = "",
    name: str = "",
    email: str = "",
):
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


@router.post("/register/signup", response_class=HTMLResponse)
async def register_signup(request: Request):
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
    _set_session(request, entry["id"])
    return RedirectResponse("/register/activate", status_code=303)


@router.post("/register/signin", response_class=HTMLResponse)
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
    dest = "/" if therapist.get("active") else "/register/activate"
    return RedirectResponse(dest, status_code=303)


@router.get("/register/activate", response_class=HTMLResponse)
async def register_activate(request: Request):
    tid = request.session.get("therapist_id")
    if not tid:
        return RedirectResponse("/register")
    therapist = next((t for t in _load_therapists_fresh() if t.get("id") == tid), None)
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


@router.get("/register/google")
async def register_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse("/register?error=Google+sign-in+is+not+configured")
    request.session["reg_google"] = True
    flow = _make_reg_flow()
    url, _ = flow.authorization_url(prompt="select_account", access_type="offline")
    return RedirectResponse(url)


@router.get("/register/google/callback")
async def register_google_callback(request: Request, code: str = "", error: str = ""):
    return await _handle_reg_google(request, code, error)


@router.get("/register/done", response_class=HTMLResponse)
async def register_done(request: Request, code: str = ""):
    import re
    _REG_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
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


# ── Logout ─────────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout(request: Request):
    tid = request.session.get("therapist_id")
    if tid:
        # Purge this therapist's calendar cache on logout
        asyncio.create_task(purge_calendar(tid))
    request.session.clear()
    return RedirectResponse("/register", status_code=303)
