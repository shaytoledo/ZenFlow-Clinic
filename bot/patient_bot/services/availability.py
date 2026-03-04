"""
availability.py — returns available days/hours for patient booking,
and manages Google Calendar slot booking/restoration.

When Google Calendar is configured (data/google_token.json exists), reads
"✅ Available" events from the therapist's calendar (per therapists.json)
and creates/removes appointment events in the primary calendar.

All Google Calendar calls are wrapped in asyncio.to_thread() to avoid
blocking the asyncio event loop.

Falls back to local availability file when Google Calendar is not connected.
Returns empty results when neither Google nor local availability is configured.
"""
import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_TOKEN_FILE = None  # lazy-loaded to avoid import-time side effects
_AVAILABILITY_CAL_NAME = "ZenFlow Availability"
_CLINIC_TZ_NAME = "Asia/Jerusalem"  # passed as string to Google Calendar API


def _token_file():
    global _TOKEN_FILE
    if _TOKEN_FILE is None:
        from pathlib import Path
        _TOKEN_FILE = Path(__file__).parent.parent.parent.parent / "data" / "google_token.json"
    return _TOKEN_FILE


def _resolve_token_file(therapist_id: str | None = None):
    """Return the token file for the given therapist, or None if not connected.
    No cross-therapist fallback — each therapist only sees their own token.
    """
    from pathlib import Path
    base = Path(__file__).parent.parent.parent.parent / "data"
    if therapist_id:
        tf = base / f"google_token_{therapist_id}.json"
        return tf if tf.exists() else None
    # No specific therapist — use the legacy default token
    tf = _token_file()
    return tf if tf.exists() else None


# ── Google Calendar service ───────────────────────────────────────────────────

def _gcal_service(therapist_id: str | None = None):
    tf = _resolve_token_file(therapist_id)
    if tf is None:
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/calendar"]
        creds = Credentials.from_authorized_user_file(str(tf), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            tf.write_text(creds.to_json(), encoding="utf-8")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning(f"Google Calendar unavailable: {e}")
        return None


def _cal_name_for_therapist(therapist_id: str | None) -> str:
    """Return the Google Calendar name for a given therapist id."""
    if therapist_id:
        from bot.config import THERAPIST_BY_ID
        t = THERAPIST_BY_ID.get(therapist_id)
        if t:
            return t.get("calendar_name", _AVAILABILITY_CAL_NAME)
    return _AVAILABILITY_CAL_NAME


def _find_availability_cal(service, cal_name: str = _AVAILABILITY_CAL_NAME) -> str | None:
    items = service.calendarList().list().execute().get("items", [])
    for cal in items:
        if cal.get("summary") == cal_name:
            return cal["id"]
    return None


# ── Date/time helpers ─────────────────────────────────────────────────────────

def _week_range(week_offset: int) -> tuple[date, date]:
    today = date.today()
    this_monday = today - timedelta(days=today.weekday())
    week_monday = this_monday + timedelta(weeks=week_offset)
    week_sunday = week_monday + timedelta(days=6)
    range_start = max(today + timedelta(days=1), week_monday)
    return range_start, week_sunday


def _slot_dt(day: date, time_slot: str) -> datetime:
    """Naive local datetime for a given day + HH:MM slot."""
    h, m = map(int, time_slot.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def _hhmm_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


# ── Public API — availability queries (async) ─────────────────────────────────

async def get_available_days(week_offset: int = 0, therapist_id: str | None = None) -> list[date]:
    """Dates with at least one available slot in the target week."""
    range_start, range_end = _week_range(week_offset)
    if range_start > range_end:
        return []

    # Check Redis cache first
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        cache_key = f"zenflow:avail:days:{therapist_id or 'default'}:{week_offset}"
        cached = await r.get(cache_key)
        if cached:
            return [date.fromisoformat(d) for d in json.loads(cached)]
    except Exception as e:
        logger.debug(f"Redis unavailable for avail days cache: {e}")
        r = None
        cache_key = None

    service = _gcal_service(therapist_id)
    if service is None:
        local = _read_local_avail(therapist_id)
        if local:
            result = sorted({
                datetime.fromisoformat(s["start"]).date()
                for s in local
                if range_start <= datetime.fromisoformat(s["start"]).date() <= range_end
            })
        else:
            result = []
    else:
        cal_name = _cal_name_for_therapist(therapist_id)
        cal_id = await asyncio.to_thread(_find_availability_cal, service, cal_name)
        if cal_id is None:
            logger.info(f"Calendar '{cal_name}' not found — no availability")
            result = []
        else:
            try:
                time_min = datetime(range_start.year, range_start.month, range_start.day,
                                    0, 0, 0, tzinfo=timezone.utc).isoformat()
                time_max = datetime(range_end.year, range_end.month, range_end.day,
                                    23, 59, 59, tzinfo=timezone.utc).isoformat()
                events = await asyncio.to_thread(
                    service.events().list(
                        calendarId=cal_id,
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        orderBy="startTime",
                    ).execute
                )
                result = sorted({
                    datetime.fromisoformat(e["start"]["dateTime"]).date()
                    for e in events.get("items", [])
                    if e.get("status") != "cancelled" and "dateTime" in e.get("start", {})
                })
            except Exception as e:
                logger.warning(f"get_available_days error: {e}")
                result = []

    # Cache result
    if r and cache_key and result:
        try:
            await r.set(cache_key, json.dumps([d.isoformat() for d in result]), ex=300)
        except Exception:
            pass

    return result


async def get_available_hours(day: date, therapist_id: str | None = None) -> list[str]:
    """All bookable 1-hour slots on the given day."""
    # Check Redis cache first
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        cache_key = f"zenflow:avail:hours:{therapist_id or 'default'}:{day.isoformat()}"
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.debug(f"Redis unavailable for avail hours cache: {e}")
        r = None
        cache_key = None

    service = _gcal_service(therapist_id)
    if service is None:
        local = _read_local_avail(therapist_id)
        if local:
            result = await _local_hours(day, local)
        else:
            result = []
    else:
        cal_name = _cal_name_for_therapist(therapist_id)
        cal_id = await asyncio.to_thread(_find_availability_cal, service, cal_name)
        if cal_id is None:
            result = []
        else:
            try:
                day_start = f"{day.isoformat()}T00:00:00Z"
                day_end   = f"{day.isoformat()}T23:59:59Z"
                events = await asyncio.to_thread(
                    service.events().list(
                        calendarId=cal_id,
                        timeMin=day_start,
                        timeMax=day_end,
                        singleEvents=True,
                        orderBy="startTime",
                    ).execute
                )
                booked = get_booked_slots(day)
                hours: list[str] = []
                for e in events.get("items", []):
                    if e.get("status") == "cancelled":
                        continue
                    start_str = e.get("start", {}).get("dateTime", "")
                    end_str   = e.get("end",   {}).get("dateTime", "")
                    if not start_str or not end_str:
                        continue
                    ev_start = datetime.fromisoformat(start_str)
                    ev_end   = datetime.fromisoformat(end_str)
                    current = ev_start
                    while current + timedelta(hours=1) <= ev_end:
                        hhmm = current.strftime("%H:%M")
                        if hhmm not in booked:
                            hours.append(hhmm)
                        current += timedelta(hours=1)
                result = sorted(set(hours))
            except Exception as e:
                logger.warning(f"get_available_hours error: {e}")
                result = []

    # Cache result
    if r and cache_key:
        try:
            await r.set(cache_key, json.dumps(result), ex=300)
        except Exception:
            pass

    return result


# ── Public API — booking / cancellation (async) ───────────────────────────────

async def book_slot(day: date, time_slot: str, patient_name: str, summary: str,
                    therapist_id: str | None = None) -> str | None:
    """
    1. Removes the 1-hour window from the availability calendar (Google or local).
    2. Creates an appointment event in the primary Google Calendar (if connected).

    Returns the Google Calendar appointment event ID, a "local_{...}" sentinel, or None.
    """
    # Invalidate booked-slots cache for this day
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        await r.delete(f"zenflow:slots:{day.isoformat()}")
    except Exception:
        pass

    service = _gcal_service(therapist_id)
    if service is None:
        # No Google Calendar — update local availability file
        await asyncio.to_thread(_remove_hour_from_local, therapist_id, day, time_slot)
        return f"local_{therapist_id or 'default'}_{day.isoformat()}_{time_slot.replace(':', '')}"

    avail_cal_name = _cal_name_for_therapist(therapist_id)
    avail_cal_id = await asyncio.to_thread(_find_availability_cal, service, avail_cal_name)

    try:
        if avail_cal_id:
            covering = await asyncio.to_thread(
                _find_covering_event, service, avail_cal_id, day, time_slot
            )
            if covering:
                await asyncio.to_thread(
                    _remove_hour_from_event, service, avail_cal_id, covering, day, time_slot
                )

        slot_start = _slot_dt(day, time_slot)
        slot_end   = slot_start + timedelta(hours=1)
        fmt = "%Y-%m-%dT%H:%M:%S"
        apt_event = await asyncio.to_thread(
            service.events().insert(
                calendarId="primary",
                body={
                    "summary":     f"🌿 ZenFlow — {patient_name}",
                    "description": summary or f"Acupuncture appointment with {patient_name}",
                    "start": {"dateTime": slot_start.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                    "end":   {"dateTime": slot_end.strftime(fmt),   "timeZone": _CLINIC_TZ_NAME},
                },
            ).execute
        )
        logger.info(f"GCal appointment created: {apt_event['id']} ({day} {time_slot})")
        return apt_event.get("id")
    except Exception as e:
        logger.warning(f"book_slot error: {e}")
        return None


async def restore_slot(day: date, time_slot: str, gcal_apt_event_id: str | None,
                       therapist_id: str | None = None) -> None:
    """Called on cancellation: deletes appointment event, re-creates availability slot."""
    # Invalidate booked-slots cache
    try:
        from bot.redis_client import get_async_redis
        r = get_async_redis()
        await r.delete(f"zenflow:slots:{day.isoformat()}")
    except Exception:
        pass

    # Local availability — add the hour back
    if gcal_apt_event_id and gcal_apt_event_id.startswith("local_"):
        await asyncio.to_thread(_add_hour_to_local, therapist_id, day, time_slot)
        return

    service = _gcal_service(therapist_id)
    if service is None:
        return

    avail_cal_name = _cal_name_for_therapist(therapist_id)
    avail_cal_id = await asyncio.to_thread(_find_availability_cal, service, avail_cal_name)

    try:
        if gcal_apt_event_id:
            try:
                await asyncio.to_thread(
                    service.events().delete(
                        calendarId="primary", eventId=gcal_apt_event_id
                    ).execute
                )
                logger.info(f"GCal appointment deleted: {gcal_apt_event_id}")
            except Exception as e:
                logger.warning(f"Could not delete appointment event {gcal_apt_event_id}: {e}")

        if avail_cal_id:
            slot_start = _slot_dt(day, time_slot)
            slot_end   = slot_start + timedelta(hours=1)
            fmt = "%Y-%m-%dT%H:%M:%S"
            await asyncio.to_thread(
                service.events().insert(
                    calendarId=avail_cal_id,
                    body={
                        "summary": "✅ Available",
                        "start": {"dateTime": slot_start.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                        "end":   {"dateTime": slot_end.strftime(fmt),   "timeZone": _CLINIC_TZ_NAME},
                        "colorId": "10",
                    },
                ).execute
            )
            logger.info(f"Availability restored: {day} {time_slot}")
    except Exception as e:
        logger.warning(f"restore_slot error: {e}")


# ── Internal helpers (sync — called via asyncio.to_thread) ────────────────────

def _find_covering_event(service, cal_id: str, day: date, time_slot: str):
    """Return the availability event whose window covers the given 1-hour slot."""
    day_start = f"{day.isoformat()}T00:00:00Z"
    day_end   = f"{day.isoformat()}T23:59:59Z"
    events = service.events().list(
        calendarId=cal_id,
        timeMin=day_start,
        timeMax=day_end,
        singleEvents=True,
    ).execute()

    slot_min     = _hhmm_min(time_slot)
    slot_end_min = slot_min + 60

    for e in events.get("items", []):
        if e.get("status") == "cancelled":
            continue
        start_str = e.get("start", {}).get("dateTime", "")
        end_str   = e.get("end",   {}).get("dateTime", "")
        if not start_str or not end_str:
            continue
        ev_start_min = _hhmm_min(datetime.fromisoformat(start_str).strftime("%H:%M"))
        ev_end_min   = _hhmm_min(datetime.fromisoformat(end_str).strftime("%H:%M"))
        if ev_start_min <= slot_min and ev_end_min >= slot_end_min:
            return e
    return None


def _remove_hour_from_event(service, cal_id: str, event: dict,
                             day: date, time_slot: str) -> None:
    """Shrink or split an availability event to remove one booked hour."""
    ev_s = datetime.fromisoformat(event["start"]["dateTime"]).replace(tzinfo=None)
    ev_e = datetime.fromisoformat(event["end"]["dateTime"]).replace(tzinfo=None)
    sl_s = _slot_dt(day, time_slot)
    sl_e = sl_s + timedelta(hours=1)

    fmt = "%Y-%m-%dT%H:%M:%S"

    if ev_s == sl_s and ev_e == sl_e:
        service.events().delete(calendarId=cal_id, eventId=event["id"]).execute()

    elif ev_s == sl_s:
        service.events().patch(
            calendarId=cal_id, eventId=event["id"],
            body={"start": {"dateTime": sl_e.strftime(fmt), "timeZone": _CLINIC_TZ_NAME}},
        ).execute()

    elif ev_e == sl_e:
        service.events().patch(
            calendarId=cal_id, eventId=event["id"],
            body={"end": {"dateTime": sl_s.strftime(fmt), "timeZone": _CLINIC_TZ_NAME}},
        ).execute()

    else:
        service.events().patch(
            calendarId=cal_id, eventId=event["id"],
            body={"end": {"dateTime": sl_s.strftime(fmt), "timeZone": _CLINIC_TZ_NAME}},
        ).execute()
        service.events().insert(
            calendarId=cal_id,
            body={
                "summary": event.get("summary", "✅ Available"),
                "start": {"dateTime": sl_e.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                "end":   {"dateTime": ev_e.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                "colorId": "10",
            },
        ).execute()


# ── Booked-slot helpers ────────────────────────────────────────────────────────

def get_booked_slots(day: date) -> set[str]:
    """Scan all patient dirs for appointments on `day`; return booked time slots.

    Results are cached in Redis for 5 minutes (key: zenflow:slots:{date}).
    """
    import json as _json
    from pathlib import Path
    from bot.config import DATA_DIR

    # Try sync Redis cache
    try:
        from bot.redis_client import get_sync_redis
        r = get_sync_redis()
        key = f"zenflow:slots:{day.isoformat()}"
        cached = r.get(key)
        if cached:
            return set(_json.loads(cached))
    except Exception:
        r = None
        key = None

    booked: set[str] = set()
    base = Path(DATA_DIR)
    if not base.exists():
        return booked
    for apt_file in base.glob(f"*/{day.isoformat()}_*.json"):
        try:
            data = _json.loads(apt_file.read_text(encoding="utf-8"))
            if data.get("status") == "active":
                booked.add(data["time"])
        except Exception as e:
            logger.warning(f"Could not read {apt_file}: {e}")
    logger.debug(f"Booked slots on {day}: {booked}")

    # Cache result
    if r and key is not None:
        try:
            r.set(key, _json.dumps(list(booked)), ex=300)
        except Exception:
            pass

    return booked


# ── Local availability (no Google Calendar) ───────────────────────────────────

def _local_avail_path(therapist_id: str | None) -> "Path":
    from pathlib import Path
    return Path(__file__).parent.parent.parent.parent / "data" / f"local_avail_{therapist_id or 'default'}.json"


def _read_local_avail(therapist_id: str | None) -> list[dict]:
    """Read local availability slots for a therapist."""
    import json as _j
    p = _local_avail_path(therapist_id)
    if not p.exists():
        return []
    try:
        return _j.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_local_avail(therapist_id: str | None, slots: list[dict]) -> None:
    import json as _j
    p = _local_avail_path(therapist_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_j.dumps(slots, indent=2, ensure_ascii=False), encoding="utf-8")


async def _local_hours(day: date, slots: list[dict]) -> list[str]:
    """Extract available 1-hour HH:MM strings from local slots for a given day."""
    booked = get_booked_slots(day)
    hours: list[str] = []
    for s in slots:
        try:
            ev_start = datetime.fromisoformat(s["start"]).replace(tzinfo=None)
            ev_end   = datetime.fromisoformat(s["end"]).replace(tzinfo=None)
        except Exception:
            continue
        if ev_start.date() != day:
            continue
        current = ev_start
        while current + timedelta(hours=1) <= ev_end:
            hhmm = current.strftime("%H:%M")
            if hhmm not in booked:
                hours.append(hhmm)
            current += timedelta(hours=1)
    return sorted(set(hours))


def _remove_hour_from_local(therapist_id: str | None, day: date, time_slot: str) -> None:
    """Shrink or split the local availability slot that covers the booked hour."""
    import json as _j
    slots = _read_local_avail(therapist_id)
    slot_min = _hhmm_min(time_slot)
    slot_end_min = slot_min + 60

    new_slots = []
    for s in slots:
        try:
            ev_start = datetime.fromisoformat(s["start"]).replace(tzinfo=None)
            ev_end   = datetime.fromisoformat(s["end"]).replace(tzinfo=None)
        except Exception:
            new_slots.append(s)
            continue
        if ev_start.date() != day:
            new_slots.append(s)
            continue
        ev_s_min = _hhmm_min(ev_start.strftime("%H:%M"))
        ev_e_min = _hhmm_min(ev_end.strftime("%H:%M"))
        if not (ev_s_min <= slot_min and ev_e_min >= slot_end_min):
            new_slots.append(s)
            continue
        # This slot covers the booked hour — shrink/split
        sl_s = _slot_dt(day, time_slot)
        sl_e = sl_s + timedelta(hours=1)
        fmt = "%Y-%m-%dT%H:%M:%S"
        import uuid as _uuid
        if ev_start == sl_s and ev_end == sl_e:
            pass  # exact match — delete entirely
        elif ev_start == sl_s:
            new_slots.append({"id": s["id"], "start": sl_e.strftime(fmt), "end": ev_end.strftime(fmt)})
        elif ev_end == sl_e:
            new_slots.append({"id": s["id"], "start": ev_start.strftime(fmt), "end": sl_s.strftime(fmt)})
        else:
            # Split into two
            new_slots.append({"id": s["id"], "start": ev_start.strftime(fmt), "end": sl_s.strftime(fmt)})
            new_slots.append({"id": _uuid.uuid4().hex, "start": sl_e.strftime(fmt), "end": ev_end.strftime(fmt)})
    _write_local_avail(therapist_id, new_slots)


def _add_hour_to_local(therapist_id: str | None, day: date, time_slot: str) -> None:
    """Add a 1-hour availability slot back to the local file (used on cancellation)."""
    import uuid as _uuid
    slots = _read_local_avail(therapist_id)
    sl_s = _slot_dt(day, time_slot)
    sl_e = sl_s + timedelta(hours=1)
    fmt = "%Y-%m-%dT%H:%M:%S"
    slots.append({"id": _uuid.uuid4().hex, "start": sl_s.strftime(fmt), "end": sl_e.strftime(fmt)})
    _write_local_avail(therapist_id, slots)


