"""
web/routers/api/system.py
──────────────────────────
System health, activation, and therapist status endpoints.
"""
import json
import logging
import re
import secrets
import string

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from bot.config import OLLAMA_HOST, OLLAMA_MODEL, THERAPIST_BOT_TOKEN, THERAPISTS, TELEGRAM_TOKEN
from web.deps import (
    _active_therapist_or_redirect,
    _generate_reg_code,
    _get_patient_bot_username,
    _get_session_therapist,
    _get_therapist_bot_username,
    _load_therapists_fresh,
)
from web.gcal import is_authenticated

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)


@router.get("/status")
async def get_system_status(request: Request):
    """Health snapshot of all services."""
    out: dict = {}

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

    # Patient bot
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

    # Google Calendar
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

    # Therapists
    active = [t for t in THERAPISTS if t.get("active")]
    out["therapists"] = {
        "ok": len(active) > 0,
        "label": "Therapists",
        "detail": f"{len(active)} active" if active else "No therapists registered",
    }

    return JSONResponse(out)


@router.get("/my/status")
async def get_my_status(request: Request):
    tid = request.session.get("therapist_id")
    if not tid:
        raise HTTPException(status_code=401, detail="Not signed in")
    therapist = next((t for t in _load_therapists_fresh() if t.get("id") == tid), None)
    if not therapist:
        raise HTTPException(status_code=404, detail="Therapist not found")
    return JSONResponse({"active": bool(therapist.get("active")), "name": therapist.get("name", "")})


@router.get("/my/activation-code")
async def get_my_activation_code(request: Request):
    therapist = _get_session_therapist(request)
    if not therapist:
        raise HTTPException(status_code=401, detail="Not signed in")
    from bot.redis_client import get_async_redis
    code = _generate_reg_code()
    r = get_async_redis()
    await r.set(
        f"zenflow:reg:{code}",
        json.dumps({
            "name": therapist["name"],
            "email": therapist.get("email", ""),
            "google_id": therapist.get("google_id", ""),
        }),
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
