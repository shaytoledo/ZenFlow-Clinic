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

from bot.patient_bot.services.relay import history_key, lastseen_key

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)


async def _send(token: str, chat_id: int, text: str, parse_mode: str | None) -> dict:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if parse_mode:  # None / "" = plain text: user-typed words must not be parsed (B2)
        payload["parse_mode"] = parse_mode
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(data.get("description", "Telegram sendMessage failed"))
        return data


async def send_to_patient(patient_id: int, text: str, parse_mode: str | None = "Markdown") -> dict:
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
    parse_mode: str | None = "Markdown",
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
    payload: dict[str, Any] = {"chat_id": therapist_telegram_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
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
                        pid = data["patient_id"]
                        tid = data.get("therapist_id") or ""
                        # Unread count for this therapist's conversation with the patient
                        hist_raw = await r.get(history_key(tid, pid))
                        lastseen_raw = await r.get(lastseen_key(tid, pid))
                        lastseen = float(lastseen_raw) if lastseen_raw else 0.0
                        unread = 0
                        if hist_raw:
                            try:
                                msgs = json.loads(hist_raw)
                                unread = sum(
                                    1
                                    for m in msgs
                                    if m.get("role") == "patient"
                                    and float(m.get("ts", 0)) > lastseen
                                )
                            except Exception:
                                pass
                        data["unread_count"] = unread
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


async def get_relay_messages(therapist_id: str, patient_id: int) -> list[dict]:
    """This therapist's conversation with the patient (never another therapist's)."""
    try:
        from bot.redis_client import get_async_redis

        raw = await get_async_redis().get(history_key(therapist_id, patient_id))
        if raw:
            messages = json.loads(raw)
            return messages if isinstance(messages, list) else []
    except Exception as e:
        logger.debug(f"get_relay_messages error: {e}")
    return []


async def has_relay_history(therapist_id: str, patient_id: int) -> bool:
    """Whether this therapist has a stored conversation with the patient."""
    try:
        from bot.redis_client import get_async_redis

        return bool(await get_async_redis().exists(history_key(therapist_id, patient_id)))
    except Exception as e:
        logger.warning(f"relay history lookup failed for patient {patient_id}: {e}")
        return False


async def append_relay_message(therapist_id: str, patient_id: int, role: str, text: str) -> None:
    """Append to this therapist's conversation and keep the last 100 entries (24h TTL)."""
    import time

    try:
        from bot.redis_client import get_async_redis

        r = get_async_redis()
        key = history_key(therapist_id, patient_id)
        raw = await r.get(key)
        messages: list[dict[str, Any]] = json.loads(raw) if raw else []
        messages.append({"role": role, "text": text, "ts": time.time()})
        messages = messages[-100:]
        await r.set(key, json.dumps(messages), ex=86400)
    except Exception as e:
        logger.debug(f"append_relay_message error: {e}")


async def mark_conversation_read(therapist_id: str, patient_id: int) -> None:
    """Reset this therapist's unread counter for the patient by stamping last-seen=now."""
    import time

    try:
        from bot.redis_client import get_async_redis

        await get_async_redis().set(
            lastseen_key(therapist_id, patient_id), str(time.time()), ex=86400
        )
    except Exception as e:
        logger.debug(f"mark_conversation_read error: {e}")


async def mark_conversation_unread(therapist_id: str, patient_id: int) -> None:
    """Force unread state by deleting this therapist's last-seen marker."""
    try:
        from bot.redis_client import get_async_redis

        await get_async_redis().delete(lastseen_key(therapist_id, patient_id))
    except Exception as e:
        logger.debug(f"mark_conversation_unread error: {e}")


async def delete_conversation(therapist_id: str, patient_id: int) -> int:
    """Delete this therapist's relay chat with the patient. Returns # of keys removed.

    Removes the therapist's history and unread marker, and the live session only when that session
    is theirs. The per-message routing keys expire on their own 24 h TTL.
    """
    try:
        from bot.redis_client import get_async_redis

        r = get_async_redis()
        keys = [history_key(therapist_id, patient_id), lastseen_key(therapist_id, patient_id)]
        active_key = f"zenflow:relay:active:{patient_id}"
        raw = await r.get(active_key)
        try:
            session = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            session = None
        if isinstance(session, dict) and session.get("therapist_id") == therapist_id:
            keys.append(active_key)
        return int(await r.delete(*keys))
    except Exception as e:
        logger.warning(f"delete_conversation({patient_id}) failed: {e}")
        return 0
