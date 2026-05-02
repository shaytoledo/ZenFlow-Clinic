"""
Google OAuth2 wrapper for ZenFlow — Calendar + Gmail (unified flow).

Token lifecycle
───────────────
1. get_auth_url()       → redirect therapist to Google consent
2. exchange_code(code, therapist_id) → fetch token, encrypt, save to DB
3. load_credentials(therapist_id)   → decrypt from DB, auto-refresh if expired
4. is_authenticated(therapist_id)   → True when a valid token row exists in DB

Encryption
──────────
Tokens are Fernet-encrypted at rest using a key derived from SESSION_SECRET.
The raw credentials JSON never touches the filesystem (old JSON files are
migrated to DB on first load and then deleted).

Scopes
──────
One OAuth consent covers both Calendar and Gmail — therapists approve everything
in a single flow.  The scopes are:
    https://www.googleapis.com/auth/calendar
    https://www.googleapis.com/auth/gmail.send
    https://www.googleapis.com/auth/gmail.readonly
"""
import base64
import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from bot.config import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_REDIRECT_URI,
    SESSION_SECRET,
)

logger = logging.getLogger(__name__)

# ── Scopes ────────────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]

# Kept for backward-compat references elsewhere in the codebase
GMAIL_SCOPES = SCOPES

# ── Legacy file paths (kept only for one-time migration) ──────────────────────

_TOKENS_DIR      = Path(__file__).parent.parent / "data" / "google_tokens"
_GMAIL_TOKENS_DIR = Path(__file__).parent.parent / "data" / "gmail_tokens"

AVAILABILITY_CAL_NAME = "ZenFlow Availability"
AVAILABILITY_TITLE    = "✅ Available"


# ── Encryption ────────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    """Derive a stable Fernet key from SESSION_SECRET (sha-256 → 32 bytes → b64url)."""
    key = base64.urlsafe_b64encode(
        hashlib.sha256(SESSION_SECRET.encode("utf-8")).digest()
    )
    return Fernet(key)


def _encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")


# ── DB token CRUD ─────────────────────────────────────────────────────────────

def _save_token_db(therapist_id: str, creds: Credentials) -> None:
    from bot.db import get_db
    encrypted = _encrypt(creds.to_json())
    scopes_str = " ".join(creds.scopes or SCOPES)
    get_db().execute(
        """INSERT INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(therapist_id) DO UPDATE SET
               encrypted_token = excluded.encrypted_token,
               scopes          = excluded.scopes,
               updated_at      = excluded.updated_at""",
        (therapist_id, encrypted, scopes_str),
    )
    logger.debug(f"Google token saved to DB for therapist {therapist_id!r}")


def _load_token_db(therapist_id: str) -> Credentials | None:
    from bot.db import get_db
    row = get_db().execute(
        "SELECT encrypted_token FROM google_tokens WHERE therapist_id = ?",
        (therapist_id,),
    ).fetchone()
    if not row:
        return None
    try:
        plaintext = _decrypt(row["encrypted_token"])
        return Credentials.from_authorized_user_info(json.loads(plaintext), SCOPES)
    except Exception as e:
        logger.warning(f"Could not decrypt token for {therapist_id!r}: {e}")
        return None


def delete_token_db(therapist_id: str) -> None:
    from bot.db import get_db
    get_db().execute(
        "DELETE FROM google_tokens WHERE therapist_id = ?", (therapist_id,)
    )


# ── Legacy file migration ─────────────────────────────────────────────────────

def _migrate_legacy_file(therapist_id: str) -> bool:
    """If a legacy JSON token file exists, move it to the DB and delete the file.
    Returns True when a migration happened."""
    for legacy_dir in (_TOKENS_DIR, _GMAIL_TOKENS_DIR):
        tf = legacy_dir / f"{therapist_id}.json"
        if tf.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(tf))
                _save_token_db(therapist_id, creds)
                tf.unlink()
                logger.info(f"Migrated legacy token file → DB for {therapist_id!r}")
                return True
            except Exception as e:
                logger.warning(f"Legacy token migration failed for {therapist_id!r}: {e}")
    return False


# ── Public OAuth helpers ───────────────────────────────────────────────────────

def get_auth_url() -> str:
    """Return the Google consent URL (Calendar + Gmail scopes, offline access)."""
    flow = _make_flow()
    url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    logger.info(f"[OAuth] redirect_uri → {GOOGLE_REDIRECT_URI}")
    return url


def exchange_code(code: str, therapist_id: str) -> None:
    """Exchange an auth code for credentials and persist them (encrypted) to DB."""
    flow = _make_flow()
    flow.fetch_token(code=code)
    _save_token_db(therapist_id, flow.credentials)


def is_authenticated(therapist_id: str) -> bool:
    """True when a (possibly expired) token exists for this therapist."""
    _migrate_legacy_file(therapist_id)   # one-time migration
    from bot.db import get_db
    row = get_db().execute(
        "SELECT 1 FROM google_tokens WHERE therapist_id = ?", (therapist_id,)
    ).fetchone()
    return row is not None


def is_gmail_authenticated(therapist_id: str) -> bool:
    """Same as is_authenticated — single flow covers both Calendar and Gmail."""
    return is_authenticated(therapist_id)


def load_credentials(therapist_id: str) -> Credentials:
    """Load, auto-refresh, and return Credentials.  Raises if not authenticated."""
    _migrate_legacy_file(therapist_id)
    creds = _load_token_db(therapist_id)
    if creds is None:
        raise FileNotFoundError(f"No Google token in DB for therapist {therapist_id!r}")
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token_db(therapist_id, creds)
    return creds


# ── Deprecated path helpers (kept so old call-sites don't break) ──────────────

def token_file_for(therapist_id: str) -> Path:
    """Deprecated — returns legacy path.  Use DB functions instead."""
    return _TOKENS_DIR / f"{therapist_id}.json"


def gmail_token_file_for(therapist_id: str) -> Path:
    return _GMAIL_TOKENS_DIR / f"{therapist_id}.json"


# ── Service builders ──────────────────────────────────────────────────────────

def get_gmail_service(therapist_id: str):
    """Return an authenticated Gmail API service object."""
    creds = load_credentials(therapist_id)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── Internal ──────────────────────────────────────────────────────────────────

def _make_flow() -> Flow:
    config = {
        "web": {
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri":      "https://accounts.google.com/o/oauth2/auth",
            "token_uri":     "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }
    return Flow.from_client_config(config, scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)


def _to_utc(dt_str: str) -> str:
    from datetime import timezone
    dt_str = dt_str.replace(" ", "+")
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Calendar client ───────────────────────────────────────────────────────────

class GCalClient:
    def __init__(self, service):
        self.service = service

    @classmethod
    def load(cls, therapist_id: str) -> "GCalClient":
        creds = load_credentials(therapist_id)
        return cls(build("calendar", "v3", credentials=creds, cache_discovery=False))

    # ── availability calendar ──────────────────────────────────────────────

    def get_calendar_list(self) -> list[dict]:
        items = self.service.calendarList().list().execute().get("items", [])
        return [
            {
                "id":    c["id"],
                "name":  c.get("summary", "Untitled"),
                "color": c.get("backgroundColor", "#4285f4"),
            }
            for c in items
        ]

    def get_or_create_availability_cal(self) -> str:
        for cal in self.service.calendarList().list().execute().get("items", []):
            if cal.get("summary") == AVAILABILITY_CAL_NAME:
                return cal["id"]
        new_cal = self.service.calendars().insert(body={
            "summary":     AVAILABILITY_CAL_NAME,
            "description": "ZenFlow Clinic — therapist available slots for patient booking",
            "timeZone":    "Asia/Jerusalem",
        }).execute()
        self.service.calendarList().insert(body={"id": new_cal["id"]}).execute()
        logger.info(f"Created availability calendar: {new_cal['id']}")
        return new_cal["id"]

    # ── events ────────────────────────────────────────────────────────────

    def get_events(self, time_min: str, time_max: str) -> list[dict]:
        result    = []
        time_min  = _to_utc(time_min)
        time_max  = _to_utc(time_max)
        all_cals  = self.service.calendarList().list().execute().get("items", [])
        avail_cal_id = next(
            (c["id"] for c in all_cals if c.get("summary") == AVAILABILITY_CAL_NAME),
            None,
        )

        for cal in all_cals:
            if cal["id"] == avail_cal_id:
                continue
            cal_color = cal.get("backgroundColor", "#4285f4")
            try:
                resp = self.service.events().list(
                    calendarId=cal["id"],
                    timeMin=time_min, timeMax=time_max,
                    singleEvents=True, orderBy="startTime",
                ).execute()
            except Exception as e:
                logger.warning(f"Could not fetch calendar {cal.get('summary')}: {e}")
                continue
            for e in resp.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                result.append({
                    "id":              f"{cal['id']}_{e['id']}",
                    "title":           e.get("summary", "(busy)"),
                    "start":           e["start"].get("dateTime", e["start"].get("date")),
                    "end":             e["end"].get("dateTime", e["end"].get("date")),
                    "backgroundColor": cal_color,
                    "borderColor":     cal_color,
                    "editable":        False,
                    "extendedProps":   {"type": "busy", "calendarId": cal["id"], "calendarName": cal.get("summary", "")},
                })

        cal_id = self.get_or_create_availability_cal()
        avail  = self.service.events().list(
            calendarId=cal_id,
            timeMin=time_min, timeMax=time_max,
            singleEvents=True, orderBy="startTime",
        ).execute()
        for e in avail.get("items", []):
            if e.get("status") == "cancelled":
                continue
            result.append({
                "id":              e["id"],
                "title":           "✅ Available",
                "start":           e["start"].get("dateTime", e["start"].get("date")),
                "end":             e["end"].get("dateTime", e["end"].get("date")),
                "backgroundColor": "#27ae60",
                "borderColor":     "#1e8449",
                "editable":        False,
                "extendedProps":   {"type": "available", "calendarId": cal_id},
            })
        return result

    def create_availability(self, cal_id: str, start: str, end: str) -> dict:
        event = self.service.events().insert(
            calendarId=cal_id,
            body={
                "summary": AVAILABILITY_TITLE,
                "start":   {"dateTime": start},
                "end":     {"dateTime": end},
                "colorId": "10",
            },
        ).execute()
        return {
            "id":              event["id"],
            "title":           "✅ Available",
            "start":           start,
            "end":             end,
            "backgroundColor": "#27ae60",
            "borderColor":     "#1e8449",
            "extendedProps":   {"type": "available", "calendarId": cal_id},
        }

    def delete_availability(self, cal_id: str, event_id: str) -> None:
        self.service.events().delete(calendarId=cal_id, eventId=event_id).execute()
