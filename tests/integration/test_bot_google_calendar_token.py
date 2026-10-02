"""The booking bot reads the therapist's Google Calendar with the token the dashboard stored.

Since Phase 0.5 the dashboard keeps Google tokens Fernet-encrypted in the `google_tokens` table and
moves any legacy `data/google_tokens/{id}.json` file into it, deleting the file. The bot's
availability code still looked for that file — so once a therapist connected Google (or the web
migrated their old file) the bot silently stopped seeing their calendar and offered the local
fallback slots instead. It now loads the same database token through `web.gcal`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bot.patient_bot.services import availability as avail

pytestmark = pytest.mark.integration

TOKEN_INFO = {
    "token": "ya29.test-access",
    "refresh_token": "1//test-refresh",
    "client_id": "client.apps.googleusercontent.com",
    "client_secret": "test-secret",
    "token_uri": "https://oauth2.googleapis.com/token",
    "expiry": "2099-01-01T00:00:00Z",  # google-auth treats a token without expiry as expired
}


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture the Calendar client the bot builds instead of calling Google."""
    calls: list[dict[str, Any]] = []

    def _build(api: str, version: str, credentials: Any = None, **kw: Any) -> str:
        calls.append({"api": api, "version": version, "credentials": credentials})
        return "calendar-client"

    import googleapiclient.discovery
    from google.oauth2.credentials import Credentials

    def _no_network(self: Any, request: Any) -> None:
        raise AssertionError("a test must never refresh a token against Google")

    monkeypatch.setattr(googleapiclient.discovery, "build", _build)
    monkeypatch.setattr(Credentials, "refresh", _no_network)
    return calls


def _store_db_token(therapist_id: str) -> None:
    from google.oauth2.credentials import Credentials

    from web import gcal

    gcal._save_token_db(therapist_id, Credentials.from_authorized_user_info(TOKEN_INFO))


def test_a_token_in_the_database_gives_the_bot_the_calendar(built, make_therapist) -> None:
    therapist = make_therapist()
    _store_db_token(therapist["id"])

    assert avail._gcal_service(therapist["id"]) == "calendar-client"
    assert built and built[0]["api"] == "calendar"
    assert built[0]["credentials"].token == TOKEN_INFO["token"]


def test_no_token_means_the_local_fallback(built, make_therapist) -> None:
    therapist = make_therapist()
    assert avail._gcal_service(therapist["id"]) is None
    assert avail._gcal_service(None) is None
    assert built == []


def test_a_legacy_token_file_is_moved_into_the_database_and_used(
    built, make_therapist, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web import gcal

    therapist = make_therapist()
    monkeypatch.setattr(gcal, "_TOKENS_DIR", tmp_path)
    legacy = tmp_path / f"{therapist['id']}.json"
    legacy.write_text(json.dumps(TOKEN_INFO), encoding="utf-8")

    assert avail._gcal_service(therapist["id"]) == "calendar-client"
    assert not legacy.exists(), "the plaintext token file is gone once it is in the database"
    assert gcal.is_authenticated(therapist["id"])


def test_an_unreadable_token_degrades_to_the_local_fallback(
    built, make_therapist, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web import gcal

    therapist = make_therapist()
    _store_db_token(therapist["id"])

    def _broken(_tid: str) -> Any:
        raise RuntimeError("refresh failed")

    monkeypatch.setattr(gcal, "load_credentials", _broken)
    assert avail._gcal_service(therapist["id"]) is None
