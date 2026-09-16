"""
Relay session manager — therapist-bot side.

Reads from the same Redis keys written by patient_bot/services/relay.py.
  zenflow:relay:msg:{therapist_id}:{msg_id}  →  JSON {"patient_id": int, "therapist_id": str}
"""

import logging

logger = logging.getLogger(__name__)


def _redis():
    from bot.redis_client import get_sync_redis

    return get_sync_redis()


def get_patient_for_msg(forwarded_msg_id: int, therapist_id: str) -> dict | None:
    """{"patient_id", "therapist_id"} for a message in this therapist's own chat, or None.

    Telegram message ids are unique per chat only, so the lookup is always scoped to the
    therapist replying (Phase 2.4).
    """
    from bot.patient_bot.services.relay import get_patient_for_msg as _lookup

    return _lookup(forwarded_msg_id, therapist_id)


def get_current_patient(therapist_id: str) -> int | None:
    """The therapist's most recent patient chat, or None.

    Informational only: routing must not use it, because "most recent" is not "the patient this
    reply is meant for" (BOT_AUDIT B1). Use `list_active_patients()` to decide where a message
    may go.
    """
    raw = _redis().get(f"zenflow:relay:current:{therapist_id}")
    return int(raw) if raw else None


def list_active_patients(therapist_id: str) -> list[int]:
    """Patient ids with an open relay session for this therapist (BOT_AUDIT B1)."""
    from bot.patient_bot.services.relay import list_active_patients as _list

    return _list(therapist_id)
