"""
Therapist frontend — FastAPI web app.

Run with: python run_web.py
Opens at: http://localhost:8000
"""
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from bot.config import GOOGLE_CLIENT_ID
from web.gcal import GCalClient, exchange_code, get_auth_url, is_authenticated

logger = logging.getLogger(__name__)

app = FastAPI(title="ZenFlow Therapist")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not is_authenticated():
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse("index.html", {"request": request})


# ── Events API ────────────────────────────────────────────────────────────────

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
