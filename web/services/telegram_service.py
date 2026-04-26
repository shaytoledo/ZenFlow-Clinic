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


async def _send(token: str, chat_id: int, text: str, parse_mode: str) -> dict:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram sendMessage failed"))
        return data


async def send_to_patient(patient_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to a patient via the patient bot token."""
    from bot.config import TELEGRAM_TOKEN
    return await _send(TELEGRAM_TOKEN, patient_id, text, parse_mode)


async def send_via_therapist_bot(patient_id: int, text: str, parse_mode: str = "Markdown") -> dict:
    """Send a message to a patient via the therapist bot (relay channel)."""
    from bot.config import THERAPIST_BOT_TOKEN
    if not THERAPIST_BOT_TOKEN:
        raise RuntimeError("THERAPIST_BOT_TOKEN not configured")
    return await _send(THERAPIST_BOT_TOKEN, patient_id, text, parse_mode)


async def echo_to_therapist_chat(
    therapist_telegram_id: int,
    text: str,
    reply_to_msg_id: int | None = None,
    parse_mode: str = "Markdown",
) -> dict | None:
    """Echo a web-sent reply into the therapist's own bot chat.

    When a therapist sends a message from the web, this surfaces it in their
    Telegram chat as a Telegram reply to the patient's last forwarded message,
    so the conversation context is visible in both places. Returns None on failure
    (errors are logged but never raised — echoing is best-effort).
    """
    from bot.config import THERAPIST_BOT_TOKEN
    if not THERAPIST_BOT_TOKEN or not therapist_telegram_id:
        return None
    payload: dict[str, Any] = {
        "chat_id": therapist_telegram_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_to_msg_id:
        payload["reply_to_message_id"] = reply_to_msg_id
        payload["allow_sending_without_reply"] = True
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{THERAPIST_BOT_TOKEN}/sendMessage",
                json=payload,
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning(f"echo_to_therapist_chat not ok: {data.get('description')}")
                return None
            return data
    except Exception as e:
        logger.warning(f"echo_to_therapist_chat failed: {e}")
        return None


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

    Defensive: the active key MUST be a dict with `patient_id`. Anything else
    (a stray scalar from a manual SET, a half-written entry, malformed JSON)
    is treated as an orphan and deleted on sight so it stops surfacing as
    "Patient undefined" in the UI.
    """
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        keys = await r.keys("zenflow:relay:active:*")
        sessions = []
        for key in keys:
            raw = await r.get(key)
            valid = False
            if raw:
                try:
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("patient_id"):
                        sessions.append(data)
                        valid = True
                except Exception:
                    pass
            if not valid:
                try:
                    await r.delete(key)
                    logger.info(f"Cleaned orphan relay-active key: {key}")
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
    """Append a message to the relay history and keep last 100 entries (24h TTL)."""
    import time
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        key = f"zenflow:relay:history:{patient_id}"
        raw = await r.get(key)
        messages: list[dict[str, Any]] = json.loads(raw) if raw else []
        messages.append({"role": role, "text": text, "ts": time.time()})
        messages = messages[-100:]
        await r.set(key, json.dumps(messages), ex=86400)
    except Exception as e:
        logger.debug(f"append_relay_message error: {e}")


async def get_total_unread_count() -> int:
    """Sum unread patient messages across all active relay sessions.

    A message is unread when its timestamp is greater than the therapist's
    last-seen timestamp for that patient (zenflow:relay:lastseen:{patient_id}).
    Returns 0 on any error.
    """
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        history_keys = await r.keys("zenflow:relay:history:*")
        total = 0
        for key in history_keys:
            patient_id = key.rsplit(":", 1)[-1]
            raw = await r.get(key)
            if not raw:
                continue
            try:
                messages = json.loads(raw)
            except Exception:
                continue
            lastseen_raw = await r.get(f"zenflow:relay:lastseen:{patient_id}")
            lastseen = float(lastseen_raw) if lastseen_raw else 0.0
            total += sum(
                1 for m in messages
                if m.get("role") == "patient" and float(m.get("ts", 0)) > lastseen
            )
        return total
    except Exception as e:
        logger.debug(f"get_total_unread_count error: {e}")
        return 0


async def mark_conversation_read(patient_id: int) -> None:
    """Reset the unread counter for one patient by stamping last-seen=now."""
    import time
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        await r.set(f"zenflow:relay:lastseen:{patient_id}", str(time.time()), ex=86400)
    except Exception as e:
        logger.debug(f"mark_conversation_read error: {e}")


async def delete_conversation(patient_id: int) -> int:
    """Delete the relay chat for `patient_id` from Redis. Returns # of keys removed.

    Removes:
      - zenflow:relay:history:{pid}    — the chat log shown in the web UI
      - zenflow:relay:lastseen:{pid}   — the unread-tracking marker
      - zenflow:relay:active:{pid}     — the live session presence
    The `zenflow:relay:msg:{msg_id}` routing keys are left to expire on their
    own 24 h TTL — they are keyed by msg_id, not patient_id, so cannot be
    enumerated cheaply.
    """
    keys = [
        f"zenflow:relay:history:{patient_id}",
        f"zenflow:relay:lastseen:{patient_id}",
        f"zenflow:relay:active:{patient_id}",
    ]
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        return int(await r.delete(*keys))
    except Exception as e:
        logger.warning(f"delete_conversation({patient_id}) failed: {e}")
        return 0
