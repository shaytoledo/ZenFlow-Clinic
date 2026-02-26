"""
Relay session manager — tracks which patient is behind each forwarded message,
so therapist replies can be routed back to the right patient.

Storage: data/relay_sessions.json
  {
    "msg_to_patient": { "<forwarded_msg_id>": <patient_id>, ... },
    "active_patients": { "<patient_id>": true, ... }
  }
"""
import json
import logging
from pathlib import Path

from bot.config import DATA_DIR

logger = logging.getLogger(__name__)


def _path() -> Path:
    return Path(DATA_DIR).parent / "relay_sessions.json"


def _load() -> dict:
    p = _path()
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"msg_to_patient": {}, "active_patients": {}}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_relay_mapping(forwarded_msg_id: int, patient_id: int) -> None:
    """Record that `forwarded_msg_id` (in therapist chat) came from `patient_id`."""
    data = _load()
    data["msg_to_patient"][str(forwarded_msg_id)] = patient_id
    data["active_patients"][str(patient_id)] = True
    _save(data)
    logger.info(f"Relay mapped: therapist msg {forwarded_msg_id} -> patient {patient_id}")


def get_patient_for_msg(forwarded_msg_id: int) -> int | None:
    """Return patient_id for a therapist message ID (from reply_to context)."""
    return _load()["msg_to_patient"].get(str(forwarded_msg_id))



def end_relay(patient_id: int) -> None:
    """Mark patient as no longer in active relay (therapist can still reply though)."""
    data = _load()
    data["active_patients"].pop(str(patient_id), None)
    _save(data)
    logger.info(f"Relay ended for patient {patient_id}")
