"""
availability.py — returns available days/hours for patient booking,
and manages Google Calendar slot booking/restoration.

When Google Calendar is configured (data/google_token.json exists), reads
"✅ Available" events from the "ZenFlow Availability" calendar and
creates/removes appointment events in the primary calendar.

Falls back to a hardcoded stub when Google Calendar is not set up.
"""
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_TOKEN_FILE = None  # lazy-loaded to avoid import-time side effects
_AVAILABILITY_CAL_NAME = "ZenFlow Availability"
_CLINIC_TZ_NAME = "Asia/Jerusalem"  # passed as string to Google Calendar API

# Stub config (used when Google Calendar is not connected)
_WORK_DAYS = {0, 1, 2, 3, 4, 6}  # Mon–Fri + Sun
_STUB_SLOTS = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]


def _token_file():
    global _TOKEN_FILE
    if _TOKEN_FILE is None:
        from pathlib import Path
        _TOKEN_FILE = Path(__file__).parent.parent.parent.parent / "data" / "google_token.json"
    return _TOKEN_FILE


# ── Google Calendar service ───────────────────────────────────────────────────

def _gcal_service():
    tf = _token_file()
    if not tf.exists():
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


def _find_availability_cal(service) -> str | None:
    for cal in service.calendarList().list().execute().get("items", []):
        if cal.get("summary") == _AVAILABILITY_CAL_NAME:
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
    """Naive local datetime for a given day + HH:MM slot.
    Use alongside timeZone=_CLINIC_TZ_NAME in Google Calendar API calls."""
    h, m = map(int, time_slot.split(":"))
    return datetime(day.year, day.month, day.day, h, m)


def _hhmm_min(hhmm: str) -> int:
    h, m = map(int, hhmm.split(":"))
    return h * 60 + m


# ── Public API — availability queries ─────────────────────────────────────────

def get_available_days(week_offset: int = 0) -> list[date]:
    """Dates with at least one available slot in the target week."""
    range_start, range_end = _week_range(week_offset)
    if range_start > range_end:
        return []

    service = _gcal_service()
    if service is None:
        return _stub_days_range(range_start, range_end)

    cal_id = _find_availability_cal(service)
    if cal_id is None:
        logger.info("ZenFlow Availability calendar not found — using stub")
        return _stub_days_range(range_start, range_end)

    try:
        time_min = datetime(range_start.year, range_start.month, range_start.day,
                            0, 0, 0, tzinfo=timezone.utc).isoformat()
        time_max = datetime(range_end.year, range_end.month, range_end.day,
                            23, 59, 59, tzinfo=timezone.utc).isoformat()
        events = service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        days = sorted({
            datetime.fromisoformat(e["start"]["dateTime"]).date()
            for e in events.get("items", [])
            if e.get("status") != "cancelled" and "dateTime" in e.get("start", {})
        })
        return days
    except Exception as e:
        logger.warning(f"get_available_days error: {e} — using stub")
        return _stub_days_range(range_start, range_end)


def get_available_hours(day: date) -> list[str]:
    """All bookable 1-hour slots on the given day (multi-hour windows are expanded)."""
    from bot.patient_bot.services.appointments import get_booked_slots

    service = _gcal_service()
    if service is None:
        return _stub_hours(day)

    cal_id = _find_availability_cal(service)
    if cal_id is None:
        return _stub_hours(day)

    try:
        day_start = f"{day.isoformat()}T00:00:00Z"
        day_end   = f"{day.isoformat()}T23:59:59Z"
        events = service.events().list(
            calendarId=cal_id,
            timeMin=day_start,
            timeMax=day_end,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

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

            # Expand the window into individual 1-hour slots
            current = ev_start
            while current + timedelta(hours=1) <= ev_end:
                hhmm = current.strftime("%H:%M")
                if hhmm not in booked:
                    hours.append(hhmm)
                current += timedelta(hours=1)

        return sorted(set(hours))
    except Exception as e:
        logger.warning(f"get_available_hours error: {e} — using stub")
        return _stub_hours(day)


# ── Public API — booking / cancellation ───────────────────────────────────────

def book_slot(day: date, time_slot: str, patient_name: str, summary: str) -> str | None:
    """
    1. Removes the 1-hour window from the ZenFlow Availability calendar
       (shrinks or splits the covering event as needed).
    2. Creates an appointment event in the primary calendar with patient details.

    Returns the Google Calendar appointment event ID (stored in the appointment
    JSON so it can be deleted on cancellation), or None when GCal is not set up.
    """
    service = _gcal_service()
    if service is None:
        return None

    avail_cal_id = _find_availability_cal(service)

    try:
        # Remove the booked hour from availability
        if avail_cal_id:
            covering = _find_covering_event(service, avail_cal_id, day, time_slot)
            if covering:
                _remove_hour_from_event(service, avail_cal_id, covering, day, time_slot)

        # Create the appointment event in the primary calendar
        slot_start = _slot_dt(day, time_slot)
        slot_end   = slot_start + timedelta(hours=1)
        fmt = "%Y-%m-%dT%H:%M:%S"
        apt_event = service.events().insert(
            calendarId="primary",
            body={
                "summary":     f"🌿 ZenFlow — {patient_name}",
                "description": summary or f"Acupuncture appointment with {patient_name}",
                "start": {"dateTime": slot_start.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                "end":   {"dateTime": slot_end.strftime(fmt),   "timeZone": _CLINIC_TZ_NAME},
            },
        ).execute()
        logger.info(f"GCal appointment created: {apt_event['id']} ({day} {time_slot})")
        return apt_event.get("id")
    except Exception as e:
        logger.warning(f"book_slot error: {e}")
        return None


def restore_slot(day: date, time_slot: str, gcal_apt_event_id: str | None) -> None:
    """
    Called on cancellation:
    1. Deletes the appointment event from the primary calendar.
    2. Re-creates a 1-hour availability slot in ZenFlow Availability.
    """
    service = _gcal_service()
    if service is None:
        return

    avail_cal_id = _find_availability_cal(service)

    try:
        # Delete the appointment event
        if gcal_apt_event_id:
            try:
                service.events().delete(calendarId="primary",
                                        eventId=gcal_apt_event_id).execute()
                logger.info(f"GCal appointment deleted: {gcal_apt_event_id}")
            except Exception as e:
                logger.warning(f"Could not delete appointment event {gcal_apt_event_id}: {e}")

        # Restore the availability slot
        if avail_cal_id:
            slot_start = _slot_dt(day, time_slot)
            slot_end   = slot_start + timedelta(hours=1)
            fmt = "%Y-%m-%dT%H:%M:%S"
            service.events().insert(
                calendarId=avail_cal_id,
                body={
                    "summary": "✅ Available",
                    "start": {"dateTime": slot_start.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                    "end":   {"dateTime": slot_end.strftime(fmt),   "timeZone": _CLINIC_TZ_NAME},
                    "colorId": "10",
                },
            ).execute()
            logger.info(f"Availability restored: {day} {time_slot}")
    except Exception as e:
        logger.warning(f"restore_slot error: {e}")


# ── Internal helpers ──────────────────────────────────────────────────────────

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
    # Strip timezone from Google's response to get naive local times for comparison
    ev_s = datetime.fromisoformat(event["start"]["dateTime"]).replace(tzinfo=None)
    ev_e = datetime.fromisoformat(event["end"]["dateTime"]).replace(tzinfo=None)
    sl_s = _slot_dt(day, time_slot)           # naive local time
    sl_e = sl_s + timedelta(hours=1)

    fmt = "%Y-%m-%dT%H:%M:%S"

    if ev_s == sl_s and ev_e == sl_e:
        # Exact 1-hour event — delete it entirely
        service.events().delete(calendarId=cal_id, eventId=event["id"]).execute()

    elif ev_s == sl_s:
        # Booked slot is at the start — move start forward one hour
        service.events().patch(
            calendarId=cal_id, eventId=event["id"],
            body={"start": {"dateTime": sl_e.strftime(fmt), "timeZone": _CLINIC_TZ_NAME}},
        ).execute()

    elif ev_e == sl_e:
        # Booked slot is at the end — move end backward one hour
        service.events().patch(
            calendarId=cal_id, eventId=event["id"],
            body={"end": {"dateTime": sl_s.strftime(fmt), "timeZone": _CLINIC_TZ_NAME}},
        ).execute()

    else:
        # Booked slot is in the middle — split into two events
        # Shrink original: [ev_start … slot_start]
        service.events().patch(
            calendarId=cal_id, eventId=event["id"],
            body={"end": {"dateTime": sl_s.strftime(fmt), "timeZone": _CLINIC_TZ_NAME}},
        ).execute()
        # Tail event: [slot_end … ev_end]
        service.events().insert(
            calendarId=cal_id,
            body={
                "summary": event.get("summary", "✅ Available"),
                "start": {"dateTime": sl_e.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                "end":   {"dateTime": ev_e.strftime(fmt), "timeZone": _CLINIC_TZ_NAME},
                "colorId": "10",
            },
        ).execute()


# ── Stubs ─────────────────────────────────────────────────────────────────────

def _stub_days_range(start: date, end: date) -> list[date]:
    result, d = [], start
    while d <= end:
        if d.weekday() in _WORK_DAYS:
            result.append(d)
        d += timedelta(days=1)
    return result


def _stub_hours(day: date) -> list[str]:
    from bot.patient_bot.services.appointments import get_booked_slots
    booked = get_booked_slots(day)
    return [s for s in _STUB_SLOTS if s not in booked]
