"""
Therapist frontend — FastAPI web app.

Run with: python run_web.py
Opens at: http://localhost:8000

Architecture:
  web/services/   — domain service layer (CRUD, caching, Telegram helpers)
  web/routers/    — FastAPI APIRouter modules (pages, auth, api/*)
  web/deps.py     — shared session helpers and data helpers (backward compat)
  web/app.py      — FastAPI app factory: middleware + router registration
"""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from bot.config import SESSION_SECRET
from web.deps import require_signed_in
from web.routers.api.admin import router as admin_router
from web.routers.api.appointments import router as apts_router
from web.routers.api.availability import router as avail_router
from web.routers.api.messages import router as messages_router
from web.routers.api.notifications import router as notifications_router
from web.routers.api.system import router as system_router
from web.routers.api.treatment import router as treatment_router
from web.routers.auth import router as auth_router

# ── Routers────────────────────────────────────────────────────────────────────
from web.routers.pages import router as pages_router
from web.routers.patients import router as patients_router
from zenflow.settings import get_settings

app = FastAPI(title="ZenFlow Therapist")


def session_cookie_kwargs(is_dev: bool) -> dict:
    """Session-cookie hardening (Phase 0.5): Secure outside dev, SameSite=lax, explicit max_age.
    (HttpOnly is always set by SessionMiddleware.)"""
    return {
        "session_cookie": "zf_session",
        "max_age": 86400 * 30,
        "same_site": "lax",
        "https_only": not is_dev,
    }


app.add_middleware(
    SessionMiddleware, secret_key=SESSION_SECRET, **session_cookie_kwargs(get_settings().is_dev)
)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> JSONResponse:
    """Public liveness probe (F11). Returns nothing but {"ok": true} — no topology, no names."""
    return JSONResponse({"ok": True})


app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ── Register routers ───────────────────────────────────────────────────────────
app.include_router(pages_router)
app.include_router(auth_router)
app.include_router(patients_router)

# Every /api/* router requires a session at ROUTER level so the check runs before body
# validation (SF-005 / F11). Endpoints add object-level checks on top (F6).
_API_AUTH = [Depends(require_signed_in)]
app.include_router(apts_router, dependencies=_API_AUTH)
app.include_router(treatment_router, dependencies=_API_AUTH)
app.include_router(avail_router, dependencies=_API_AUTH)
app.include_router(messages_router, dependencies=_API_AUTH)
app.include_router(system_router, dependencies=_API_AUTH)
app.include_router(notifications_router, dependencies=_API_AUTH)
app.include_router(admin_router, dependencies=_API_AUTH)
