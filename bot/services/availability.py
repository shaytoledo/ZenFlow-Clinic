"""
availability.py — returns available days/hours for patient booking.

When Google Calendar is configured (data/google_token.json exists), reads
"✅ Available" events from the "ZenFlow Availability" calendar.

Falls back to a hardcoded stub when Google Calendar is not set up.
"""
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_FILE = Path(__file__).parent.parent.parent / "data" / "google_token.json"
_AVAILABILITY_CAL_NAME = "ZenFlow Availability"
_AVAILABILITY_TITLE = "✅ Available"

# Stub config (used when Google Calendar is not connected)
_WORK_DAYS = {0, 1, 2, 3, 4, 6}  # Mon–Fri + Sun
_STUB_SLOTS = ["09:00", "10:00", "11:00", "12:00", "13:00", "14:00"]


# ── Google Calendar helpers ───────────────────────────────────────────────────

def _gcal_service():
    """Return a Google Calendar service object, or None if not configured."""
    if not _TOKEN_FILE.exists():
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        SCOPES = ["https://www.googleapis.com/auth/calendar"]
        creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return build("calendar", "v3", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning(f"Google Calendar unavailable: {e}")
        return None


def _find_availability_cal(service) -> str | None:
    for cal in service.calendarList().list().execute().get("items", []):
        if cal.get("summary") == _AVAILABILITY_CAL_NAME:
            return cal["id"]
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def get_available_days(days_ahead: int = 14) -> list[date]:
    """Return dates that have at least one available slot."""
    service = _gcal_service()
    if service is None:
        return _stub_days(days_ahead)

    cal_id = _find_availability_cal(service)
    if cal_id is None:
        logger.info("ZenFlow Availability calendar not found — using stub")
        return _stub_days(days_ahead)

    try:
        now = datetime.now(timezone.utc)
        future = now + timedelta(days=days_ahead)
        events = service.events().list(
            calendarId=cal_id,
            timeMin=now.isoformat(),
            timeMax=future.isoformat(),
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
        logger.warning(f"get_available_days Google error: {e} — using stub")
        return _stub_days(days_ahead)


def get_available_hours(day: date) -> list[str]:
    """Return HH:MM slots available on the given day, excluding already-booked ones."""
    from bot.services.appointments import get_booked_slots

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
        hours = []
        for e in events.get("items", []):
            if e.get("status") == "cancelled":
                continue
            start_dt = e.get("start", {}).get("dateTime", "")
            if start_dt:
                hhmm = datetime.fromisoformat(start_dt).strftime("%H:%M")
                if hhmm not in booked:
                    hours.append(hhmm)

        return sorted(hours)
    except Exception as e:
        logger.warning(f"get_available_hours Google error: {e} — using stub")
        return _stub_hours(day)


# ── Stubs ─────────────────────────────────────────────────────────────────────

def _stub_days(days_ahead: int) -> list[date]:
    today = date.today()
    return [
        today + timedelta(days=i)
        for i in range(1, days_ahead + 1)
        if (today + timedelta(days=i)).weekday() in _WORK_DAYS
    ]


def _stub_hours(day: date) -> list[str]:
    from bot.services.appointments import get_booked_slots
    booked = get_booked_slots(day)
    return [s for s in _STUB_SLOTS if s not in booked]
