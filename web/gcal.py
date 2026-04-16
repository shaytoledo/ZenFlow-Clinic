"""
Google Calendar wrapper for the ZenFlow therapist frontend.

- OAuth2 flow: get_auth_url() / exchange_code()
- Token persisted to data/google_tokens/{therapist_id}.json (auto-refreshed)
- Availability events live in a dedicated "ZenFlow Availability" calendar
  (auto-created on first use)
"""
import logging
from datetime import datetime
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from bot.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _to_utc(dt_str: str) -> str:
    """Normalize any ISO datetime string to UTC RFC3339 (Z suffix) for Google API."""
    from datetime import timezone
    dt_str = dt_str.replace(" ", "+")  # URL decodes + as space; restore it
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
_TOKENS_DIR = Path(__file__).parent.parent / "data" / "google_tokens"
AVAILABILITY_CAL_NAME = "ZenFlow Availability"
AVAILABILITY_TITLE = "✅ Available"


def token_file_for(therapist_id: str) -> Path:
    """Return the token file path for a given therapist."""
    return _TOKENS_DIR / f"{therapist_id}.json"


# ── OAuth ─────────────────────────────────────────────────────────────────────

def get_auth_url() -> str:
    flow = _make_flow()
    url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return url


def exchange_code(code: str, token_file: Path | None = None) -> None:
    flow = _make_flow()
    flow.fetch_token(code=code)
    _save_token(flow.credentials, token_file)


def is_authenticated(therapist_id: str) -> bool:
    return token_file_for(therapist_id).exists()


def _make_flow() -> Flow:
    config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)


def _save_token(creds: Credentials, token_file: Path) -> None:
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json(), encoding="utf-8")


# ── Client ────────────────────────────────────────────────────────────────────

class GCalClient:
    def __init__(self, service):
        self.service = service

    @classmethod
    def load(cls, token_file: Path) -> "GCalClient":
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(creds, token_file)
        return cls(build("calendar", "v3", credentials=creds, cache_discovery=False))

    # ── availability calendar ──────────────────────────────────────────────

    def get_calendar_list(self) -> list[dict]:
        """Return all user calendars with id, name, color."""
        items = self.service.calendarList().list().execute().get("items", [])
        return [
            {
                "id": c["id"],
                "name": c.get("summary", "Untitled"),
                "color": c.get("backgroundColor", "#4285f4"),
            }
            for c in items
        ]

    def get_or_create_availability_cal(self) -> str:
        """Return the ZenFlow Availability calendar ID, creating it if needed."""
        for cal in self.service.calendarList().list().execute().get("items", []):
            if cal.get("summary") == AVAILABILITY_CAL_NAME:
                return cal["id"]

        new_cal = self.service.calendars().insert(body={
            "summary": AVAILABILITY_CAL_NAME,
            "description": "ZenFlow Clinic — therapist available slots for patient booking",
            "timeZone": "Asia/Jerusalem",
        }).execute()
        self.service.calendarList().insert(body={"id": new_cal["id"]}).execute()
        logger.info(f"Created availability calendar: {new_cal['id']}")
        return new_cal["id"]

    # ── events ────────────────────────────────────────────────────────────

    def get_events(self, time_min: str, time_max: str) -> list[dict]:
        """Return all events in FullCalendar format for the given range."""
        result = []
        time_min = _to_utc(time_min)
        time_max = _to_utc(time_max)

        # Fetch all calendars the user has access to
        all_cals = self.service.calendarList().list().execute().get("items", [])
        avail_cal_id = next(
            (c["id"] for c in all_cals if c.get("summary") == AVAILABILITY_CAL_NAME),
            None,
        )

        # All calendars except the ZenFlow Availability one — shown as busy
        for cal in all_cals:
            if cal["id"] == avail_cal_id:
                continue
            cal_color = cal.get("backgroundColor", "#4285f4")
            try:
                resp = self.service.events().list(
                    calendarId=cal["id"],
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                ).execute()
            except Exception as e:
                logger.warning(f"Could not fetch calendar {cal.get('summary')}: {e}")
                continue

            for e in resp.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                result.append({
                    "id": f"{cal['id']}_{e['id']}",
                    "title": e.get("summary", "(busy)"),
                    "start": e["start"].get("dateTime", e["start"].get("date")),
                    "end": e["end"].get("dateTime", e["end"].get("date")),
                    "backgroundColor": cal_color,
                    "borderColor": cal_color,
                    "editable": False,
                    "extendedProps": {"type": "busy", "calendarId": cal["id"], "calendarName": cal.get("summary", "")},
                })

        # Availability calendar — shown as green (editable/deletable)
        cal_id = self.get_or_create_availability_cal()
        avail = self.service.events().list(
            calendarId=cal_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for e in avail.get("items", []):
            if e.get("status") == "cancelled":
                continue
            result.append({
                "id": e["id"],
                "title": "✅ Available",
                "start": e["start"].get("dateTime", e["start"].get("date")),
                "end": e["end"].get("dateTime", e["end"].get("date")),
                "backgroundColor": "#27ae60",
                "borderColor": "#1e8449",
                "editable": False,
                "extendedProps": {"type": "available", "calendarId": cal_id},
            })

        return result

    def create_availability(self, cal_id: str, start: str, end: str) -> dict:
        event = self.service.events().insert(
            calendarId=cal_id,
            body={
                "summary": AVAILABILITY_TITLE,
                "start": {"dateTime": start},
                "end": {"dateTime": end},
                "colorId": "10",
            },
        ).execute()
        return {
            "id": event["id"],
            "title": "✅ Available",
            "start": start,
            "end": end,
            "backgroundColor": "#27ae60",
            "borderColor": "#1e8449",
            "extendedProps": {"type": "available", "calendarId": cal_id},
        }

    def delete_availability(self, cal_id: str, event_id: str) -> None:
        self.service.events().delete(calendarId=cal_id, eventId=event_id).execute()
