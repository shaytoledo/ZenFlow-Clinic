"""
Relay session manager — therapist-bot side.

Reads from the same Redis keys written by patient_bot/services/relay.py.
  zenflow:relay:msg:{forwarded_msg_id}  →  JSON {"patient_id": int, "therapist_id": str}
"""
import json
import logging

logger = logging.getLogger(__name__)


def _redis():
    from bot.redis_client import get_sync_redis
    return get_sync_redis()


def get_patient_for_msg(forwarded_msg_id: int) -> dict | None:
    """Return {"patient_id": int, "therapist_id": str} for a therapist message ID, or None."""
    raw = _redis().get(f"zenflow:relay:msg:{forwarded_msg_id}")
    return json.loads(raw) if raw else None
