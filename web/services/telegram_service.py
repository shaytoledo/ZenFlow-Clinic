"""
web/services/telegram_service.py
──────────────────────────────────
Telegram helpers for the web layer.

Sending goes through the channel adapters (`bot.interfaces`, plan 7.1) — this module never
talks to the Bot API itself. What is left here:
- echoing a web-sent reply into the therapist's own bot chat;
- bot identity for pages and the status check;
- reading and managing the relay conversations kept in Redis.
"""

import json
import logging
from typing import Any

from bot.interfaces import SentMessage, TelegramChannel, get_staff_channel
from bot.patient_bot.services.relay import history_key, lastseen_key

logger = logging.getLogger(__name__)


async def echo_to_therapist_chat(
    therapist_telegram_id: int | str | None,
    text: str,
    reply_to_msg_id: int | str | None = None,
) -> SentMessage | None:
    """Echo a web-sent reply into the therapist's own bot chat.

    When a therapist sends a message from the web, this surfaces it in their Telegram chat as a
    reply to the patient's last forwarded message, so the conversation reads the same in both
    places. Best effort: failures are logged, never raised, and None is returned.
    """
    if not therapist_telegram_id:
        return None
    try:
        return await get_staff_channel().send_text(
            therapist_telegram_id, text, reply_to=reply_to_msg_id
        )
    except Exception as e:
        logger.warning(f"echo_to_therapist_chat failed: {e}")
        return None


async def get_bot_info(token: str) -> dict[str, Any] | None:
    """getMe for the given token, or None when it cannot be reached or is refused."""
    if not token:
        return None
    return await TelegramChannel(token=token).bot_info()


async def check_bot(token: str) -> tuple[bool, str]:
    """(ok, "@username" or the reason) for the status page."""
    if not token:
        return False, "Token not configured"
    return await TelegramChannel(token=token).check()


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
