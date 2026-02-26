"""
Google Calendar wrapper for the ZenFlow therapist frontend.

- OAuth2 flow: get_auth_url() / exchange_code()
- Token persisted to data/google_token.json (auto-refreshed)
- Availability events live in a dedicated "ZenFlow Availability" calendar
  (auto-created on first use)
"""
import json
import logging
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from bot.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = Path(__file__).parent.parent / "data" / "google_token.json"
AVAILABILITY_CAL_NAME = "ZenFlow Availability"
AVAILABILITY_TITLE = "✅ Available"


# ── OAuth ─────────────────────────────────────────────────────────────────────

def get_auth_url() -> str:
    flow = _make_flow()
    url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    return url


def exchange_code(code: str) -> None:
    flow = _make_flow()
    flow.fetch_token(code=code)
    _save_token(flow.credentials)


def is_authenticated() -> bool:
    return TOKEN_FILE.exists()


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


def _save_token(creds: Credentials) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")


# ── Client ────────────────────────────────────────────────────────────────────

class GCalClient:
    def __init__(self, service):
        self.service = service

    @classmethod
    def load(cls) -> "GCalClient":
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(creds)
        return cls(build("calendar", "v3", credentials=creds, cache_discovery=False))

    # ── availability calendar ──────────────────────────────────────────────

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

        # Primary calendar — shown as busy (not editable)
        primary = self.service.events().list(
            calendarId="primary",
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
        ).execute()

        for e in primary.get("items", []):
            if e.get("status") == "cancelled":
                continue
            result.append({
                "id": e["id"],
                "title": e.get("summary", "(busy)"),
                "start": e["start"].get("dateTime", e["start"].get("date")),
                "end": e["end"].get("dateTime", e["end"].get("date")),
                "backgroundColor": "#c0392b",
                "borderColor": "#c0392b",
                "editable": False,
                "extendedProps": {"type": "busy"},
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
