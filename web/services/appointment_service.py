"""
web/services/appointment_service.py
─────────────────────────────────────
CRUD abstraction for appointments and intake_sessions tables.
"""
import asyncio
import json
import logging
from datetime import date

logger = logging.getLogger(__name__)


def _parse_row(row) -> dict:
    d = dict(row)
    hj = d.pop("history_json", None)
    d["intake_history"] = json.loads(hj) if hj else []
    return d


# ── Read operations ────────────────────────────────────────────────────────────

def list_all() -> list[dict]:
    """Load all appointments with intake history from SQLite."""
    from bot.db import get_db
    rows = get_db().execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id"""
    ).fetchall()
    return [_parse_row(r) for r in rows]


async def list_all_cached() -> list[dict]:
    """Return all appointments, Redis-cached for 30 seconds."""
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        cached = await r.get("zenflow:apts:all")
        if cached:
            return json.loads(cached)
        data = await asyncio.to_thread(list_all)
        await r.set("zenflow:apts:all", json.dumps(data, default=str), ex=30)
        return data
    except Exception:
        return await asyncio.to_thread(list_all)


def list_today() -> list[dict]:
    today = date.today().isoformat()
    return [
        a for a in list_all()
        if a.get("date") == today and a.get("status") == "active"
    ]


def get_by_patient_date_time(patient_id: int, apt_date: str, apt_time: str) -> dict | None:
    """Fetch a specific appointment record (time accepts HH:MM or HH-MM)."""
    time_str = apt_time.replace("-", ":")
    from bot.db import get_db
    row = get_db().execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id=? AND a.date=? AND a.time=?
           ORDER BY a.created_at DESC LIMIT 1""",
        (patient_id, apt_date, time_str),
    ).fetchone()
    return _parse_row(row) if row else None


def list_by_patient(patient_id: int) -> list[dict]:
    from bot.db import get_db
    rows = get_db().execute(
        """SELECT a.*, i.history_json
           FROM appointments a
           LEFT JOIN intake_sessions i ON i.appointment_id = a.id
           WHERE a.patient_id=?
           ORDER BY a.date, a.time""",
        (patient_id,),
    ).fetchall()
    return [_parse_row(r) for r in rows]


def aggregate_patients(appointments: list[dict]) -> list[dict]:
    """Aggregate appointment rows into per-patient summary dicts."""
    patients: dict[int, dict] = {}
    for apt in appointments:
        pid = apt.get("patient_id")
        if not pid:
            continue
        if pid not in patients:
            patients[pid] = {
                "id": pid,
                "name": apt.get("patient_name", f"Patient {pid}"),
                "sessions": 0,
                "intake_count": 0,
                "last_appointment": None,
                "last_time": None,
                "recent": [],
            }
        p = patients[pid]
        p["sessions"] += 1
        if apt.get("intake_history"):
            p["intake_count"] += 1
        apt_date = apt.get("date", "")
        if not p["last_appointment"] or apt_date > p["last_appointment"]:
            p["last_appointment"] = apt_date
            p["last_time"] = apt.get("time", "")
        p["recent"].append({
            "date": apt.get("date"),
            "time": apt.get("time"),
            "summary": (apt.get("summary") or "")[:120],
            "intake_history": apt.get("intake_history", []),
        })
    for p in patients.values():
        p["recent"].sort(key=lambda x: x.get("date", ""))
        p["recent"] = p["recent"][-5:]
    return sorted(patients.values(), key=lambda p: p.get("last_appointment") or "", reverse=True)
