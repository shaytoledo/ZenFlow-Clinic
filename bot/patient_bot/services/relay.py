"""
Relay session manager — tracks which patient is behind each forwarded message,
so therapist replies can be routed back to the right patient.

Storage: Redis
  zenflow:relay:msg:{forwarded_msg_id}  →  JSON {"patient_id": int, "therapist_id": str}  TTL 24h
  zenflow:relay:active:{patient_id}     →  "1"  (no TTL; deleted on end_relay)
"""
import json
import logging

logger = logging.getLogger(__name__)


def _redis():
    from bot.redis_client import get_sync_redis
    return get_sync_redis()


def save_relay_mapping(forwarded_msg_id: int, patient_id: int, therapist_id: str = "") -> None:
    """Record that `forwarded_msg_id` (in therapist chat) came from `patient_id`."""
    r = _redis()
    r.set(
        f"zenflow:relay:msg:{forwarded_msg_id}",
        json.dumps({"patient_id": patient_id, "therapist_id": therapist_id}),
        ex=86400,  # 24 h TTL
    )
    r.set(f"zenflow:relay:active:{patient_id}", "1")
    logger.info(f"Relay mapped: therapist msg {forwarded_msg_id} -> patient {patient_id} (therapist {therapist_id})")


def get_patient_for_msg(forwarded_msg_id: int) -> dict | None:
    """Return {"patient_id": int, "therapist_id": str} for a therapist message ID, or None."""
    raw = _redis().get(f"zenflow:relay:msg:{forwarded_msg_id}")
    return json.loads(raw) if raw else None


def end_relay(patient_id: int) -> None:
    """Mark patient as no longer in active relay."""
    _redis().delete(f"zenflow:relay:active:{patient_id}")
    logger.info(f"Relay ended for patient {patient_id}")
