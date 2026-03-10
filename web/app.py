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

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from bot.config import SESSION_SECRET
from starlette.middleware.sessions import SessionMiddleware

# ── Routers ────────────────────────────────────────────────────────────────────
from web.routers.pages import router as pages_router
from web.routers.auth import router as auth_router
from web.routers.api.appointments import router as apts_router
from web.routers.api.treatment import router as treatment_router
from web.routers.api.availability import router as avail_router
from web.routers.api.messages import router as messages_router
from web.routers.api.system import router as system_router

app = FastAPI(title="ZenFlow Therapist")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="zf_session",
    max_age=86400 * 30,
)

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
app.include_router(apts_router)
app.include_router(treatment_router)
app.include_router(avail_router)
app.include_router(messages_router)
app.include_router(system_router)
