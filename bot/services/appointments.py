import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from bot.config import DATA_DIR

logger = logging.getLogger(__name__)

# ── path helpers ────────────────────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    """Convert a patient name to a safe folder-name component."""
    return re.sub(r"[^\w\-]", "_", name).strip("_") or "Unknown"


def _patient_dir(patient_id: int, patient_name: str = "") -> Path:
    """data/appointments/{Name}_{patient_id}/  — creates if missing."""
    safe = _sanitize_name(patient_name) if patient_name else "Unknown"
    p = Path(DATA_DIR) / f"{safe}_{patient_id}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def find_patient_dir(patient_id: int) -> Path | None:
    """Find an existing patient folder by scanning for *_{patient_id} suffix."""
    base = Path(DATA_DIR)
    if not base.exists():
        return None
    for d in base.iterdir():
        if d.is_dir() and d.name.endswith(f"_{patient_id}"):
            return d
    return None


def _apt_filename(day: date, time_slot: str) -> str:
    """e.g. 2026-03-01_09-00.json"""
    return f"{day.isoformat()}_{time_slot.replace(':', '-')}.json"


# ── public API ───────────────────────────────────────────────────────────────

def get_booked_slots(day: date) -> set[str]:
    """Scan all patient dirs for appointments on `day`; return booked time slots."""
    booked: set[str] = set()
    base = Path(DATA_DIR)
    if not base.exists():
        return booked
    prefix = day.isoformat() + "_"
    for apt_file in base.glob(f"*/{day.isoformat()}_*.json"):
        try:
            data = json.loads(apt_file.read_text(encoding="utf-8"))
            if data.get("status") == "active":
                booked.add(data["time"])
        except Exception as e:
            logger.warning(f"Could not read {apt_file}: {e}")
    logger.debug(f"Booked slots on {day}: {booked}")
    return booked


def save_appointment(
    patient_id: int,
    patient_name: str,
    day: date,
    time_slot: str,
    intake_history: list[dict],
    summary: str,
    gcal_apt_event_id: str | None = None,
) -> Path:
    """Save appointment JSON. Returns the file path."""
    filepath = _patient_dir(patient_id, patient_name) / _apt_filename(day, time_slot)
    data = {
        "patient_id": patient_id,
        "patient_name": patient_name,
        "date": day.isoformat(),
        "time": time_slot,
        "created_at": datetime.now().isoformat(),
        "status": "active",
        "intake_history": intake_history,
        "summary": summary,
        "gcal_apt_event_id": gcal_apt_event_id,
    }
    filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Appointment saved: {filepath}")
    return filepath


def get_patient_appointments(patient_id: int) -> list[dict]:
    """Return all active appointments for a patient, sorted by date/time."""
    pdir = find_patient_dir(patient_id)
    if not pdir:
        logger.info(f"No appointment directory for patient {patient_id}")
        return []
    appointments = []
    for f in sorted(pdir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("status") == "active":
                data["_filepath"] = str(f)
                appointments.append(data)
        except Exception as e:
            logger.warning(f"Could not read {f}: {e}")
    logger.info(f"Found {len(appointments)} active appointments for patient {patient_id}")
    return appointments


def cancel_appointment(filepath: str) -> bool:
    """Delete an appointment file."""
    try:
        p = Path(filepath)
        p.unlink()
        logger.info(f"Appointment deleted: {filepath}")
        return True
    except Exception as e:
        logger.error(f"Could not delete appointment {filepath}: {e}")
        return False
