"""
Relay session manager — tracks which patient is behind each forwarded message,
so therapist replies can be routed back to the right patient.

Storage: Redis
  zenflow:relay:msg:{forwarded_msg_id}  →  JSON {"patient_id", "therapist_id"}    TTL 24h
  zenflow:relay:active:{patient_id}     →  JSON {"patient_id", "patient_name",
                                                   "therapist_id", "started_at"}  TTL 24h
  zenflow:relay:history:{patient_id}    →  JSON list[{role, text, ts}]            TTL 30 min
  zenflow:relay:current:{therapist_id}  →  patient_id (str)                       TTL 24h
"""
import json
import logging
import time

logger = logging.getLogger(__name__)

_HISTORY_TTL = 86400  # 24h so the web Messages tab can replay recent chats
_HISTORY_MAX = 100


def _redis():
    from bot.redis_client import get_sync_redis
    return get_sync_redis()


def save_relay_mapping(
    forwarded_msg_id: int,
    patient_id: int,
    therapist_id: str = "",
    patient_name: str = "",
) -> None:
    """Record that `forwarded_msg_id` (in therapist chat) came from `patient_id`."""
    r = _redis()
    r.set(
        f"zenflow:relay:msg:{forwarded_msg_id}",
        json.dumps({"patient_id": patient_id, "therapist_id": therapist_id}),
        ex=86400,
    )

    active_key = f"zenflow:relay:active:{patient_id}"
    existing = r.get(active_key)
    if existing:
        try:
            session = json.loads(existing)
        except Exception:
            session = {}
    else:
        session = {}
    session.update({
        "patient_id": patient_id,
        "patient_name": patient_name or session.get("patient_name", ""),
        "therapist_id": therapist_id or session.get("therapist_id", ""),
        "started_at": session.get("started_at") or time.time(),
        "last_msg_id": forwarded_msg_id,
    })
    r.set(active_key, json.dumps(session), ex=86400)

    if therapist_id:
        r.set(f"zenflow:relay:current:{therapist_id}", str(patient_id), ex=86400)
    logger.info(
        f"Relay mapped: therapist msg {forwarded_msg_id} -> patient {patient_id} (therapist {therapist_id})"
    )


def append_history(patient_id: int, role: str, text: str) -> None:
    """Append a message to relay history so the web Messages tab can render it."""
    r = _redis()
    key = f"zenflow:relay:history:{patient_id}"
    raw = r.get(key)
    try:
        messages = json.loads(raw) if raw else []
    except Exception:
        messages = []
    messages.append({"role": role, "text": text, "ts": time.time()})
    messages = messages[-_HISTORY_MAX:]
    r.set(key, json.dumps(messages), ex=_HISTORY_TTL)


def get_patient_for_msg(forwarded_msg_id: int) -> dict | None:
    """Return {"patient_id": int, "therapist_id": str} for a therapist message ID, or None."""
    raw = _redis().get(f"zenflow:relay:msg:{forwarded_msg_id}")
    return json.loads(raw) if raw else None


def end_relay(patient_id: int) -> None:
    """Mark patient as no longer in active relay."""
    _redis().delete(f"zenflow:relay:active:{patient_id}")
    logger.info(f"Relay ended for patient {patient_id}")
