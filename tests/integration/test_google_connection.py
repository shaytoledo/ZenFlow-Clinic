"""Phase 5.2 — sending email without a working Google account is a typed 409, never a 200/500.

Contract (docs/GOOGLE_CONNECTION_UX.md §2):
409 {ok: false, code: "google_not_connected", reason, title, message, action_url, connect_url[, text]}
"""

from __future__ import annotations

import base64
import email
import json
import re
from typing import Any

import pytest

from bot import db as dbmod

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
ITEMS = [{"enabled": True, "category": "Diet", "text": "Warm, cooked food"}]


def _page(apt: dict[str, Any]) -> str:
    return f"/treatment/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"


def _url(apt: dict[str, Any]) -> str:
    return "/api/treatment-notes" + _page(apt).removeprefix("/treatment") + "/send-recommendations"


def _connect(therapist_id: str) -> None:
    """A stored Google token (its content is never read by these tests)."""
    dbmod.get_db().execute(
        """INSERT INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at)
           VALUES (?, 'x', '', '2026-01-01T00:00:00Z')""",
        (therapist_id,),
    )


def _reconnect_alerts(therapist_id: str, *, active_only: bool = True) -> int:
    clause = " AND resolved_at IS NULL" if active_only else ""
    return int(
        dbmod.get_db()
        .execute(
            "SELECT COUNT(*) FROM notifications WHERE therapist_id=? "
            f"AND kind='gmail_token_expired'{clause}",
            (therapist_id,),
        )
        .fetchone()[0]
    )


def _jobs() -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute("SELECT name, status FROM jobs")]


class FakeGmail:
    """users().messages().send(userId=…, body=…).execute() — records or raises."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[dict[str, Any]] = []

    def users(self) -> FakeGmail:
        return self

    def messages(self) -> FakeGmail:
        return self

    def send(self, userId: str, body: dict[str, Any]) -> FakeGmail:  # noqa: N803 - Google's name
        self.sent.append({"userId": userId, **body})
        return self

    def execute(self) -> dict[str, Any]:
        if self.error:
            raise self.error
        return {"id": "m1"}


@pytest.fixture
async def clinic(make_therapist, make_appointment, make_patient, login_as):
    """A signed-in therapist with one Telegram and one manual (email-only) session."""

    async def _make(language: str = "en") -> dict[str, Any]:
        therapist = make_therapist(
            email=f"g-{language}@example.com", password=PW, language=language
        )
        return {
            "therapist": therapist,
            "client": await login_as(therapist),
            "telegram": make_appointment(therapist=therapist),
            "manual": make_appointment(
                therapist=therapist, patient=make_patient("Manual M", manual=True), apt_time="11:00"
            ),
        }

    return _make


# ── immediate email send ──
async def test_no_google_account_is_a_typed_409_with_the_text_to_copy(clinic) -> None:
    c = await clinic()
    apt = c["telegram"]
    resp = await c["client"].post(
        _url(apt), json={"items": ITEMS, "schedule_hours": 2, "email": "p@example.com"}
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body == {
        "ok": False,
        "code": "google_not_connected",
        "reason": "not_connected",
        "title": "Not connected to Google",
        "message": body["message"],
        "action_url": "/settings#google",
        "connect_url": f"/auth/login?next={_page(apt)}",
        "text": body["text"],
    }
    assert "cannot send emails on your behalf" in body["message"]
    assert "recommendations" in body["text"].lower() and "Warm, cooked food" in body["text"]


async def test_the_message_follows_the_therapist_language(clinic) -> None:
    c = await clinic("he")
    resp = await c["client"].post(
        _url(c["telegram"]), json={"items": ITEMS, "schedule_hours": 2, "email": "p@example.com"}
    )
    assert resp.status_code == 409
    assert resp.json()["title"] == "אין חיבור לחשבון Google"


async def test_a_revoked_token_asks_to_reconnect_and_alerts_once(clinic, monkeypatch) -> None:
    from google.auth.exceptions import RefreshError

    import web.gcal as gcal

    c = await clinic()
    tid = c["therapist"]["id"]
    _connect(tid)

    def _revoked(_tid: str) -> Any:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(gcal, "get_gmail_service", _revoked)
    for _ in range(2):
        resp = await c["client"].post(
            _url(c["telegram"]),
            json={"items": ITEMS, "schedule_hours": 2, "email": "p@example.com"},
        )
        assert resp.status_code == 409
        assert resp.json()["reason"] == "token_invalid"
        assert resp.json()["title"] == "Google connection expired"
    assert _reconnect_alerts(tid) == 1, "one reconnect alert while it is unresolved"


async def test_gmail_refusing_the_credentials_is_token_invalid_too(clinic, monkeypatch) -> None:
    import web.gcal as gcal

    c = await clinic()
    tid = c["therapist"]["id"]
    _connect(tid)
    fake = FakeGmail(Exception("<HttpError 401 'Invalid Credentials'>: unauthorized"))
    monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: fake)
    resp = await c["client"].post(
        _url(c["telegram"]), json={"items": ITEMS, "schedule_hours": 2, "email": "p@example.com"}
    )
    assert resp.status_code == 409 and resp.json()["reason"] == "token_invalid"
    assert _reconnect_alerts(tid) == 1


@pytest.mark.parametrize("kind", ["offline", "google-5xx"])
async def test_a_transient_failure_is_not_a_reconnect(clinic, monkeypatch, kind: str) -> None:
    from google.auth.exceptions import RefreshError, TransportError

    import web.gcal as gcal

    c = await clinic()
    tid = c["therapist"]["id"]
    _connect(tid)

    def _unavailable(_tid: str) -> Any:
        if kind == "offline":
            raise TransportError("connection reset")
        raise RefreshError("internal_failure", retryable=True)

    monkeypatch.setattr(gcal, "get_gmail_service", _unavailable)
    resp = await c["client"].post(
        _url(c["telegram"]), json={"items": ITEMS, "schedule_hours": 2, "email": "p@example.com"}
    )
    assert resp.status_code == 502
    assert "internal_failure" not in resp.text and "connection reset" not in resp.text
    assert _reconnect_alerts(tid) == 0


async def test_happy_path_sends_base64_mime_and_clears_a_stale_alert(clinic, monkeypatch) -> None:
    import web.gcal as gcal
    from web.services import email_service

    c = await clinic()
    tid = c["therapist"]["id"]
    _connect(tid)
    email_service._notify_reconnect(tid)  # left over from an earlier failure
    fake = FakeGmail()
    monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: fake)
    resp = await c["client"].post(
        _url(c["telegram"]), json={"items": ITEMS, "schedule_hours": 2, "email": " p@example.com "}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["sent_via"] == "email"
    assert len(fake.sent) == 1 and fake.sent[0]["userId"] == "me"
    mime = email.message_from_bytes(base64.urlsafe_b64decode(fake.sent[0]["raw"]))
    assert mime["To"] == "p@example.com"
    assert "recommendations" in str(mime["Subject"]).lower()
    payload = mime.get_payload(decode=True)
    assert isinstance(payload, bytes) and "Warm, cooked food" in payload.decode("utf-8")
    assert _reconnect_alerts(tid) == 0, "a working token resolves the reconnect alert"


# ── the 24h queue ──
async def test_an_email_only_patient_is_not_queued_while_google_is_disconnected(clinic) -> None:
    c = await clinic()
    resp = await c["client"].post(_url(c["manual"]), json={"items": ITEMS, "schedule_hours": 24})
    assert resp.status_code == 409
    body = resp.json()
    assert body["code"] == "google_not_connected" and body["reason"] == "not_connected"
    assert "only be reached by email" in body["message"]
    assert "text" not in body, "nothing to copy yet — the send is only being scheduled"
    assert _jobs() == []

    _connect(c["therapist"]["id"])
    resp = await c["client"].post(_url(c["manual"]), json={"items": ITEMS, "schedule_hours": 24})
    assert resp.status_code == 200 and resp.json()["queued"] is True


async def test_a_telegram_patient_is_queued_without_google(clinic) -> None:
    c = await clinic()
    resp = await c["client"].post(_url(c["telegram"]), json={"items": ITEMS, "schedule_hours": 24})
    assert resp.status_code == 200 and resp.json()["queued"] is True


# ── knowing before the click ──
async def test_gmail_status_reports_the_reason(clinic) -> None:
    from web.services import email_service

    c = await clinic()
    tid = c["therapist"]["id"]
    status = (await c["client"].get("/api/gmail-status")).json()
    assert status["connected"] is False and status["reason"] == "not_connected"
    _connect(tid)
    status = (await c["client"].get("/api/gmail-status")).json()
    assert status["connected"] is True and status["reason"] is None
    email_service._notify_reconnect(tid)
    status = (await c["client"].get("/api/gmail-status")).json()
    assert status["connected"] is False and status["reason"] == "token_invalid"


async def test_a_failing_check_is_unknown_not_disconnected(monkeypatch) -> None:
    import web.gcal as gcal
    from web.services import email_service

    def _broken(_tid: str) -> bool:
        raise RuntimeError("db is locked")

    monkeypatch.setattr(gcal, "is_gmail_authenticated", _broken)
    state = email_service.google_connection("t1")
    assert state.as_dict() == {"connected": None, "reason": None}


async def test_the_treatment_page_bootstrap_carries_the_google_state(clinic) -> None:
    c = await clinic()
    apt = c["telegram"]
    page = await c["client"].get(_page(apt))
    island = re.search(
        r'<script type="application/json" id="treatment-config">(.*?)</script>', page.text
    )
    assert island is not None
    assert json.loads(island.group(1))["google"] == {
        "connected": False,
        "reason": "not_connected",
    }


async def test_the_settings_page_has_the_google_anchor(clinic) -> None:
    c = await clinic()
    page = await c["client"].get("/settings")
    assert 'id="google"' in page.text


def test_every_new_string_exists_in_both_languages() -> None:
    from web.i18n import _LOCALES_DIR

    keys = {
        "email_not_connected_title",
        "email_not_connected_body",
        "email_token_expired_title",
        "email_token_expired_body",
        "email_queue_needs_google",
    }
    for lang in ("en", "he"):
        data = json.loads((_LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert keys <= {k for k, v in data.items() if v}, lang
