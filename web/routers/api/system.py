"""
web/routers/api/system.py
──────────────────────────
System health, activation, and therapist status endpoints.
"""
import asyncio
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


async def _check_bot(token: str, label: str) -> dict:
    """Return a status dict for a Telegram bot token."""
    if not token:
        return {"ok": False, "label": label, "detail": "Token not configured"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            data = (await client.get(f"https://api.telegram.org/bot{token}/getMe")).json()
        if data.get("ok"):
            return {"ok": True, "label": label, "detail": f"@{data['result']['username']}"}
        return {"ok": False, "label": label, "detail": data.get("description", "Invalid token")}
    except Exception:
        return {"ok": False, "label": label, "detail": "Unreachable"}


@router.get("/status")
async def get_system_status(request: Request):
    """Health snapshot of all services."""
    out: dict = {}

    # Redis
    try:
        from bot.redis_client import get_async_redis
        await get_async_redis().ping()
        out["redis"] = {"ok": True, "label": "Redis", "detail": "Connected"}
    except Exception as e:
        out["redis"] = {"ok": False, "label": "Redis", "detail": str(e)[:80]}

    # Ollama
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            models = [m["name"] for m in (await client.get(f"{OLLAMA_HOST}/api/tags")).json().get("models", [])]
        model_ok = any(OLLAMA_MODEL in m for m in models)
        out["ollama"] = {
            "ok": model_ok, "label": "Ollama",
            "detail": f"Model '{OLLAMA_MODEL}' ready" if model_ok else f"Model '{OLLAMA_MODEL}' not found",
        }
    except Exception:
        out["ollama"] = {"ok": False, "label": "Ollama", "detail": "Not running"}

    # Bots
    out["patient_bot"], out["therapist_bot"] = await asyncio.gather(
        _check_bot(TELEGRAM_TOKEN, "Patient Bot"),
        _check_bot(THERAPIST_BOT_TOKEN, "Therapist Bot"),
    )

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


@router.get("/smtp-status")
async def get_smtp_status():
    """Return whether SMTP is configured (no credentials exposed)."""
    from web.services.email_service import is_configured, _config
    cfg = _config()
    configured = is_configured()
    return JSONResponse({
        "configured": configured,
        "host": cfg["host"] if configured else "",
        "port": cfg["port"] if configured else 587,
        "user": cfg["user"] if configured else "",
        "from": cfg["from"] if configured else "",
    })


@router.get("/my/alerts")
async def get_my_alerts(request: Request):
    """Return pending therapist alerts (e.g. manual patients needing follow-up)."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    therapist_id = therapist["id"]
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        alert_key = f"zenflow:alerts:{therapist_id}"
        raw_list = await r.lrange(alert_key, 0, 49)
        alerts = []
        for raw in raw_list:
            try:
                alerts.append(json.loads(raw))
            except Exception:
                pass
        return JSONResponse({"alerts": alerts, "count": len(alerts)})
    except Exception as e:
        logger.error(f"get_my_alerts error: {e}")
        return JSONResponse({"alerts": [], "count": 0})


@router.delete("/my/alerts")
async def clear_my_alerts(request: Request):
    """Dismiss all alerts for the current therapist."""
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        await r.delete(f"zenflow:alerts:{therapist['id']}")
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.get("/my/language")
async def get_my_language(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    from bot.db import get_db
    row = await asyncio.to_thread(
        lambda: get_db().execute(
            "SELECT language FROM therapists WHERE id=?", (therapist["id"],)
        ).fetchone()
    )
    lang = (dict(row).get("language") if row else None) or "en"
    return JSONResponse({"language": lang})


@router.post("/my/language")
async def set_my_language(request: Request):
    therapist, redirect = _active_therapist_or_redirect(request)
    if redirect:
        raise HTTPException(status_code=401, detail="Not authenticated")
    body = await request.json()
    lang = (body.get("language") or "en").strip()
    if lang not in ("en", "he"):
        raise HTTPException(status_code=400, detail="Supported languages: en, he")
    from bot.db import get_db
    await asyncio.to_thread(
        lambda: get_db().execute(
            "UPDATE therapists SET language=? WHERE id=?", (lang, therapist["id"])
        )
    )
    # Refresh in-memory therapist list
    try:
        from bot.config import reload_therapists
        reload_therapists()
    except Exception:
        pass
    return JSONResponse({"ok": True, "language": lang})


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
