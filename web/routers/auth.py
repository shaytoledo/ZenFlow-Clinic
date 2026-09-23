"""
web/routers/auth.py
────────────────────
Authentication routes: Google OAuth, register/sign-in, logout.
"""

import asyncio
import json
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from bot.config import GOOGLE_CLIENT_ID
from bot.services.followup_jobs import resume_after_google_connected
from web.deps import (
    _active_therapist_or_redirect,
    _find_by_email,
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
from web.gcal import (
    delete_token_db,
    exchange_code,
    get_auth_url,
)
from web.services import login_guard, rate_limit
from web.services.cache_service import prefetch_calendar, purge_calendar
from web.services.email_service import google_reconnected

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Google Calendar OAuth ──────────────────────────────────────────────────────


#: session key holding where to go once Google has answered (Phase 5.2)
_NEXT_KEY = "google_next"
_NEXT_MAX_LENGTH = 512


def _safe_next(value: str | None) -> str:
    """`value` if it is a path on this site, else "" — `next` must never be an open redirect.

    Browsers read `\\` as `/` and drop tabs/newlines inside URLs, so `/\\evil.example` or
    `/<tab>/evil.example` would leave the site; anything with them is refused outright.
    """
    path = value or ""
    if not path.startswith("/") or path.startswith("//") or len(path) > _NEXT_MAX_LENGTH:
        return ""
    if "\\" in path or any(ord(ch) < 0x21 or ord(ch) == 0x7F for ch in path):
        return ""
    parts = urlsplit(path)
    return "" if parts.scheme or parts.netloc else path


def _with_param(path: str, name: str, value: str) -> str:
    """`path` with `name=value` set in its query (replacing any old value), fragment kept."""
    parts = urlsplit(path)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != name]
    query.append((name, value))
    return urlunsplit(("", "", parts.path, urlencode(query), parts.fragment))


@router.get("/auth/login")
async def auth_login(request: Request, next: str = ""):  # noqa: A002 - the query parameter's name
    """Start Google consent. `?next=/treatment/…` comes back to that page afterwards.

    A session is required (SF-015, plan 9.1): the callback binds the token to whoever is signed in,
    and this route writes to the session, so a stranger must not be able to start it.
    """
    _therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect, status_code=303)
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(
            "<h2>GOOGLE_CLIENT_ID not set in .env — see START.md for setup.</h2>",
            status_code=500,
        )
    target = _safe_next(next)
    if target:
        request.session[_NEXT_KEY] = target
    else:
        request.session.pop(_NEXT_KEY, None)
    return RedirectResponse(get_auth_url())


@router.post("/auth/disconnect")
async def auth_disconnect(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        return RedirectResponse(redirect, status_code=303)
    tid = therapist["id"]
    await asyncio.to_thread(delete_token_db, tid)
    await purge_calendar(tid)
    return RedirectResponse("/settings", status_code=303)


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", error: str = ""):
    target = _safe_next(request.session.pop(_NEXT_KEY, ""))
    if request.session.pop("reg_google", False):
        return await _handle_reg_google(request, code, error)
    if error or not code:
        if target:
            return RedirectResponse(_with_param(target, "google", "cancelled"))
        return RedirectResponse("/settings?error=Google+auth+cancelled")
    try:
        therapist = _get_session_therapist(request)
        if not therapist:
            return RedirectResponse("/register")
        await asyncio.to_thread(exchange_code, code, therapist["id"])
        await asyncio.to_thread(google_reconnected, therapist["id"])
        await asyncio.to_thread(resume_after_google_connected, therapist["id"])
        asyncio.create_task(prefetch_calendar(therapist["id"]))
    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    if target:
        return RedirectResponse(_with_param(target, "google", "connected"))
    return RedirectResponse("/settings?connected=1")


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
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request,
            "tab": tab,
            "google_enabled": bool(GOOGLE_CLIENT_ID),
            "error": error,
            "name": name,
            "email": email,
        },
    )


@router.post("/register/signup", response_class=HTMLResponse)
async def register_signup(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = (form.get("password") or "").strip()

    def _err(msg: str, status: int = 200, retry_after: int | None = None):
        resp = templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "tab": "register",
                "google_enabled": bool(GOOGLE_CLIENT_ID),
                "error": msg,
                "name": name,
                "email": email,
            },
            status_code=status,
        )
        if retry_after is not None:
            resp.headers["Retry-After"] = str(retry_after)
        return resp

    # Per-IP flood limit (9.5): stop a script mass-creating accounts from one address.
    wait = await rate_limit.hit(
        f"signup:{login_guard.client_ip(request)}", rate_limit.signup_per_minute()
    )
    if wait:
        minutes = max(1, round(wait / 60))
        return _err(
            f"Too many sign-up attempts. Please wait about {minutes} minute(s) and try again.",
            status=429,
            retry_after=wait,
        )

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
    return RedirectResponse("/onboarding", status_code=303)


@router.post("/register/signin", response_class=HTMLResponse)
async def register_signin(request: Request):
    form = await request.form()
    email = (form.get("email") or "").strip().lower()
    password = (form.get("password") or "").strip()

    def _err(msg: str, status: int = 200, retry_after: int | None = None):
        resp = templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "tab": "signin",
                "google_enabled": bool(GOOGLE_CLIENT_ID),
                "error": msg,
                "name": "",
                "email": email,
            },
            status_code=status,
        )
        if retry_after is not None:
            resp.headers["Retry-After"] = str(retry_after)
        return resp

    if not email or not password:
        return _err("Email and password are required.")

    # Brute-force lockout (9.5): refuse before touching the password if this account or the source
    # IP has failed too many times in a row.
    wait = await login_guard.check_locked(request, email)
    if wait:
        minutes = max(1, round(wait / 60))
        return _err(
            f"Too many sign-in attempts. Please wait about {minutes} minute(s) and try again.",
            status=429,
            retry_after=wait,
        )

    therapist = _find_by_email(email)
    # A Google-only account is a legitimate hint, not a failed password — do not count it.
    if therapist and not therapist.get("password_hash"):
        return _err(
            "This account uses Google sign-in. Please click 'Continue with Google' instead."
        )
    if not therapist or not _verify_password(password, therapist["password_hash"]):
        await login_guard.on_failure(
            request, email, account_id=(therapist["id"] if therapist else None)
        )
        return _err("Invalid email or password.")

    await login_guard.on_success(request, email)
    _set_session(request, therapist["id"])
    # Pre-warm the next 2 weeks of Google Calendar events so the schedule page
    # loads instantly. Fire-and-forget — never blocks the redirect.
    asyncio.create_task(prefetch_calendar(therapist["id"]))
    dest = "/" if therapist.get("active") else "/onboarding"
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
    return templates.TemplateResponse(
        "register_activate.html",
        {
            "request": request,
            "therapist": therapist,
            "therapist_bot_username": therapist_username,
            "therapist_bot_link": (
                f"https://t.me/{therapist_username}" if therapist_username else ""
            ),
        },
    )


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
        return templates.TemplateResponse(
            "register_done.html",
            {
                "request": request,
                "error": "Invalid or missing registration code.",
                "code": "",
                "name": "",
            },
        )
    r = get_async_redis()
    raw = await r.get(f"zenflow:reg:{code}")
    if not raw:
        return templates.TemplateResponse(
            "register_done.html",
            {
                "request": request,
                "error": "This code has expired or was already used.",
                "code": "",
                "name": "",
            },
        )
    info = json.loads(raw)
    therapist_username = await _get_therapist_bot_username()
    patient_username = await _get_patient_bot_username()
    return templates.TemplateResponse(
        "register_done.html",
        {
            "request": request,
            "error": "",
            "code": code,
            "name": info.get("name", "Therapist"),
            "therapist_bot_username": therapist_username,
            "therapist_bot_link": (
                f"https://t.me/{therapist_username}" if therapist_username else ""
            ),
            "patient_bot_username": patient_username,
            "patient_bot_link": f"https://t.me/{patient_username}" if patient_username else "",
        },
    )


# ── Logout ─────────────────────────────────────────────────────────────────────


@router.get("/logout")
async def logout(request: Request):
    from web import session_policy

    tid = request.session.get("therapist_id")
    if tid:
        # Purge this therapist's calendar cache on logout
        asyncio.create_task(purge_calendar(tid))
    # Clears the cookie *and* remembers the session id: a copy taken earlier still verifies
    # (9.2, SF-016).
    session_policy.revoke(request.session)
    return RedirectResponse("/register", status_code=303)
