"""
web/services/telegram_service.py
──────────────────────────────────
Telegram Bot API helpers for the web layer.

Supports:
- Sending messages to patients via the patient bot
- Sending messages to patients via the therapist bot
- Reading active relay conversations from Redis
- Fetching recent Telegram updates (for live chat view)
"""
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


async def send_to_patient(patient_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to a patient via the patient bot token."""
    from bot.config import TELEGRAM_TOKEN
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": patient_id, "text": text, "parse_mode": parse_mode},
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram sendMessage failed"))
        return data


async def send_via_therapist_bot(patient_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to a patient via the therapist bot (relay channel)."""
    from bot.config import THERAPIST_BOT_TOKEN
    if not THERAPIST_BOT_TOKEN:
        raise RuntimeError("THERAPIST_BOT_TOKEN not configured")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{THERAPIST_BOT_TOKEN}/sendMessage",
            json={"chat_id": patient_id, "text": text, "parse_mode": parse_mode},
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram sendMessage via therapist bot failed"))
        return data


async def get_bot_info(token: str) -> dict | None:
    """Call getMe for the given token; return result dict or None on failure."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            return data.get("result") if data.get("ok") else None
    except Exception:
        return None


async def get_active_relay_conversations() -> list[dict]:
    """Return a list of active relay sessions from Redis.

    Each item: {"patient_id": int, "therapist_id": str, "messages": list}
    """
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = await r.keys("zenflow:relay:active:*")
        sessions = []
        for key in keys:
            raw = await r.get(key)
            if raw:
                try:
                    data = json.loads(raw)
                    sessions.append(data)
                except Exception:
                    pass
        return sessions
    except Exception as e:
        logger.debug(f"get_active_relay_conversations error: {e}")
        return []


async def get_relay_messages(patient_id: int) -> list[dict]:
    """Return the relay message history for a patient from Redis.

    Key: zenflow:relay:history:{patient_id}
    Format: JSON list of {role: "patient"|"therapist", text: str, ts: float}
    """
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        raw = await r.get(f"zenflow:relay:history:{patient_id}")
        if raw:
            return json.loads(raw)
    except Exception as e:
        logger.debug(f"get_relay_messages error: {e}")
    return []


async def append_relay_message(patient_id: int, role: str, text: str) -> None:
    """Append a message to the relay history and keep last 100 entries (30-min TTL)."""
    import time
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        key = f"zenflow:relay:history:{patient_id}"
        raw = await r.get(key)
        messages: list[dict[str, Any]] = json.loads(raw) if raw else []
        messages.append({"role": role, "text": text, "ts": time.time()})
        messages = messages[-100:]  # keep last 100
        await r.set(key, json.dumps(messages), ex=1800)
    except Exception as e:
        logger.debug(f"append_relay_message error: {e}")
