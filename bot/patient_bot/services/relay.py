"""
Relay session manager — tracks which patient is behind each forwarded message,
so therapist replies can be routed back to the right patient.

Storage: Redis
  zenflow:relay:msg:{therapist_id}:{msg_id}        →  JSON {"patient_id", "therapist_id"}  TTL 24h
  zenflow:relay:active:{patient_id}                →  JSON {"patient_id", "patient_name",
                                                       "therapist_id", "started_at",
                                                       "last_msg_id"}                     TTL 24h
  zenflow:relay:history:{therapist_id}:{patient_id}  →  JSON list[{role, text, ts}]       TTL 24h
  zenflow:relay:lastseen:{therapist_id}:{patient_id} →  unix time (str), web unread marker TTL 24h
  zenflow:relay:current:{therapist_id}             →  patient_id (str), informational     TTL 24h

Everything a therapist can read or reply to is keyed by that therapist (Phase 2.4, SF-008):
Telegram numbers messages per chat, so a message id is only unique within one therapist's chat,
and a patient who moves between therapists must not carry one conversation into the other.
"""

import contextlib
import json
import logging
import time

logger = logging.getLogger(__name__)

_HISTORY_TTL = 86400  # 24h so the web Messages tab can replay recent chats
_HISTORY_MAX = 100


def _redis():
    from bot.redis_client import get_sync_redis

    return get_sync_redis()


def msg_key(therapist_id: str, forwarded_msg_id: int) -> str:
    """Routing key for one message in one therapist's chat."""
    return f"zenflow:relay:msg:{therapist_id}:{forwarded_msg_id}"


def history_key(therapist_id: str, patient_id: int) -> str:
    """The conversation between one therapist and one patient."""
    return f"zenflow:relay:history:{therapist_id}:{patient_id}"


def lastseen_key(therapist_id: str, patient_id: int) -> str:
    """When this therapist last read this conversation on the dashboard."""
    return f"zenflow:relay:lastseen:{therapist_id}:{patient_id}"


def _load_session(raw: str | bytes | None) -> dict:
    """Parse a stored relay session; a missing or corrupt entry reads as no session."""
    if not raw:
        return {}
    with contextlib.suppress(TypeError, ValueError):
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    return {}


def save_relay_mapping(
    forwarded_msg_id: int,
    patient_id: int,
    therapist_id: str = "",
    patient_name: str = "",
) -> None:
    """Record that `forwarded_msg_id` (in therapist chat) came from `patient_id`."""
    r = _redis()
    r.set(
        msg_key(therapist_id, forwarded_msg_id),
        json.dumps({"patient_id": patient_id, "therapist_id": therapist_id}),
        ex=86400,
    )

    active_key = f"zenflow:relay:active:{patient_id}"
    session = _load_session(r.get(active_key))
    session.update(
        {
            "patient_id": patient_id,
            "patient_name": patient_name or session.get("patient_name", ""),
            "therapist_id": therapist_id or session.get("therapist_id", ""),
            "started_at": session.get("started_at") or time.time(),
            "last_msg_id": forwarded_msg_id,
        }
    )
    r.set(active_key, json.dumps(session), ex=86400)

    if therapist_id:
        r.set(f"zenflow:relay:current:{therapist_id}", str(patient_id), ex=86400)
    logger.info(
        f"Relay mapped: therapist msg {forwarded_msg_id} -> patient {patient_id} (therapist {therapist_id})"
    )


def append_history(patient_id: int, role: str, text: str, therapist_id: str) -> None:
    """Append a message to this therapist's conversation with the patient (web Messages tab)."""
    r = _redis()
    key = history_key(therapist_id, patient_id)
    raw = r.get(key)
    try:
        messages = json.loads(raw) if raw else []
    except Exception:
        messages = []
    messages.append({"role": role, "text": text, "ts": time.time()})
    messages = messages[-_HISTORY_MAX:]
    r.set(key, json.dumps(messages), ex=_HISTORY_TTL)


def get_patient_for_msg(forwarded_msg_id: int, therapist_id: str) -> dict | None:
    """{"patient_id", "therapist_id"} for a message in this therapist's chat, or None."""
    raw = _redis().get(msg_key(therapist_id, forwarded_msg_id))
    return json.loads(raw) if raw else None


def list_active_patients(therapist_id: str) -> list[int]:
    """Patient ids with an open relay session belonging to `therapist_id`."""
    r = _redis()
    out: list[int] = []
    for key in r.scan_iter("zenflow:relay:active:*"):
        data = _load_session(r.get(key))
        if data.get("therapist_id") != therapist_id:
            continue
        pid = data.get("patient_id")
        if isinstance(pid, int | str):
            with contextlib.suppress(ValueError):
                out.append(int(pid))
    return sorted(out)


def end_relay(patient_id: int) -> None:
    """Mark patient as no longer in active relay.

    Also releases the therapist's "current patient" pointer, but only when it still points at
    this patient: a newer chat with someone else must not be clobbered (BOT_AUDIT B1). The read
    and the delete are not atomic, which is safe because that key is informational only — routing
    reads `list_active_patients()`.
    """
    r = _redis()
    active_key = f"zenflow:relay:active:{patient_id}"
    therapist_id = _load_session(r.get(active_key)).get("therapist_id", "") or ""
    r.delete(active_key)
    if therapist_id:
        current_key = f"zenflow:relay:current:{therapist_id}"
        if (r.get(current_key) or "") == str(patient_id):
            r.delete(current_key)
    logger.info(f"Relay ended for patient {patient_id} (therapist {therapist_id or 'unknown'})")
