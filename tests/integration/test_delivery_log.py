"""Phase 6.6 — the recommendation delivery chain is idempotent and logged (`message_log`, plan 8.3).

Plan test: complete → advance N hours → one outbound message, one success notification, one
message_log row, and a second run sends nothing. Plus: follow-ups and "Send Now" are logged,
failures are logged with redacted errors, and the therapist sees the list on the session pages.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import message_log_repo
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"
PW = "pw-Test-123"


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _log(kind: str | None = None) -> list[dict[str, Any]]:
    rows = [dict(r) for r in dbmod.get_db().execute("SELECT * FROM message_log ORDER BY id")]
    return [r for r in rows if kind is None or r["kind"] == kind]


def _slug(apt: dict[str, Any]) -> str:
    return f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"


@pytest.fixture
def telegram_session(authenticated_client, make_appointment, make_treatment_notes):
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    return authenticated_client, apt, tid


async def _complete(client: Any, apt: dict[str, Any]) -> None:
    resp = await client.post(f"/api/treatment-notes/{_slug(apt)}/complete", json={})
    assert resp.status_code == 200, resp.text


# ── the plan's chain test ──
async def test_complete_then_n_hours_later_exactly_one_logged_delivery(
    telegram_session, fake_telegram
) -> None:
    from bot.services.followup_scheduler import reconcile

    client, apt, tid = telegram_session
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()

        recs = [c for c in fake_telegram.calls if "recommendations" in c["text"].lower()]
        assert len(recs) == 1, "one outbound message"
        sent_alerts = (
            dbmod.get_db()
            .execute(
                "SELECT COUNT(*) FROM notifications WHERE kind='recommendations_sent' "
                "AND appointment_id=?",
                (apt["id"],),
            )
            .fetchone()[0]
        )
        assert sent_alerts == 1, "one success notification"
        (row,) = _log("recommendations")
        assert (row["channel"], row["status"], row["direction"]) == ("telegram", "sent", "out")
        assert (row["therapist_id"], row["patient_id"], row["appointment_id"]) == (
            tid,
            apt["patient_id"],
            apt["id"],
        )
        assert row["provider_message_id"] and row["error"] is None
        assert row["ts"] == "2026-03-02T12:01:00Z"

        frozen.tick(timedelta(hours=1))
        reconcile()
        await worker.run_once()  # a second run
    assert len([c for c in fake_telegram.calls if "recommendations" in c["text"].lower()]) == 1
    assert len(_log("recommendations")) == 1, "a second run sends — and logs — nothing"


async def test_the_followup_step1_is_logged_too(telegram_session, fake_telegram) -> None:
    client, apt, _tid = telegram_session
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await _worker().run_once()
    (row,) = _log("followup")
    assert (row["channel"], row["status"]) == ("telegram", "sent")
    assert row["provider_message_id"]


async def test_a_failed_telegram_delivery_is_logged_per_attempt(
    telegram_session, fake_telegram
) -> None:
    # every attempt of both jobs fails
    fake_telegram.fail_next("Forbidden: bot was blocked by the user", status=403, times=10)
    client, apt, _tid = telegram_session
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        worker = _worker()
        await worker.run_once()
        frozen.tick(timedelta(minutes=5))
        await worker.run_once()
    failed = _log("recommendations")
    assert len(failed) == 2 and {r["status"] for r in failed} == {"failed"}
    assert failed[0]["error"] == "Forbidden: bot was blocked by the user"
    assert failed[0]["provider_message_id"] is None


async def test_a_queued_email_is_logged_with_the_gmail_id(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, monkeypatch
) -> None:
    import web.gcal as gcal

    class Gmail:
        def users(self) -> Gmail:
            return self

        def messages(self) -> Gmail:
            return self

        def send(self, userId: str, body: dict[str, Any]) -> Gmail:  # noqa: N803
            return self

        def execute(self) -> dict[str, str]:
            return {"id": "18c2f0a1b2"}

    monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: Gmail())
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    dbmod.get_db().execute(
        "INSERT INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at) "
        "VALUES (?, 'x', '', '2026-01-01T00:00:00Z')",
        (tid,),
    )
    apt = make_appointment(
        therapist={"id": tid}, patient=make_patient("M", manual=True), apt_date="2026-03-01"
    )
    dbmod.get_db().execute(
        "UPDATE appointments SET patient_email='m@example.com' WHERE id=?", (apt["id"],)
    )
    make_treatment_notes(apt)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await _worker().run_once()
    (row,) = _log("recommendations")
    assert (row["channel"], row["status"], row["provider_message_id"]) == (
        "email",
        "sent",
        "18c2f0a1b2",
    )
    assert _log("followup") == [], "no step 1 for a patient without a channel"


async def test_send_now_is_logged(telegram_session, fake_telegram) -> None:
    client, apt, _tid = telegram_session
    items = [{"enabled": True, "category": "Diet", "text": "Warm food"}]
    url = f"/api/treatment-notes/{_slug(apt)}/send-recommendations"
    assert (await client.post(url, json={"items": items})).status_code == 200
    resp = await client.post(url, json={"items": items, "email": "p@example.com"})
    assert resp.status_code == 409  # Google is not connected
    rows = _log("recommendations")
    assert [(r["channel"], r["status"]) for r in rows] == [
        ("telegram", "sent"),
        ("email", "failed"),
    ]
    assert rows[1]["error"] == "Google not connected (not_connected)"


# ── the table ──
def test_the_log_guards_its_values_and_redacts_errors(db) -> None:
    conn = dbmod.get_db()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO message_log (ts, channel, kind, status) "
            "VALUES ('x', 'sms', 'followup', 'sent')"
        )
    row_id = message_log_repo.record(
        channel="telegram",
        kind="followup",
        status="failed",
        therapist_id="t1",
        patient_id=1,
        appointment_id=2,
        error="POST https://api.telegram.org/bot1234567890:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx/x "
        + "y" * 400,
    )
    (stored,) = (r for r in _log() if r["id"] == row_id)
    assert "AAHxxx" not in stored["error"] and "<redacted:telegram-token>" in stored["error"]
    assert len(stored["error"]) == message_log_repo.MAX_ERROR_LEN
    assert message_log_repo.for_appointment("t1", 2)[0]["id"] == row_id
    assert message_log_repo.for_appointment("t2", 2) == [], "another therapist sees nothing"
    assert message_log_repo.provider_id({"ok": True, "result": {"message_id": 7}}) == "7"
    assert message_log_repo.provider_id({"ok": True}) is None
    assert message_log_repo.provider_id("abc") == "abc"
    assert message_log_repo.provider_id(None) is None


# ── what the therapist sees ──
def _section(html: str) -> str:
    match = re.search(r'<section id="delivery-log".*?</section>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


async def test_the_session_pages_list_the_messages(telegram_session) -> None:
    client, apt, tid = telegram_session
    page_url = f"/treatment/{_slug(apt)}"
    assert "hidden" in _section((await client.get(page_url)).text), "nothing sent yet"
    with freeze_time("2026-03-02T12:01:00Z"):
        message_log_repo.record(
            channel="telegram",
            kind="recommendations",
            status="sent",
            therapist_id=tid,
            patient_id=apt["patient_id"],
            appointment_id=apt["id"],
            provider_message_id=101,
        )
        message_log_repo.record(
            channel="email",
            kind="followup",
            status="failed",
            therapist_id=tid,
            patient_id=apt["patient_id"],
            appointment_id=apt["id"],
            error="<b>boom</b>",
        )
    section = _section((await client.get(page_url)).text)
    assert "Messages" in section
    assert '<time class="fu-log-time">2026-03-02 14:01</time>' in section
    assert "Recommendations · Telegram" in section and "24h check-in · Email" in section
    assert "fu-status-sent" in section and "fu-status-failed" in section
    assert "&lt;b&gt;boom&lt;/b&gt;" in section and "<b>boom" not in section
    archive = await client.get(f"/patients/{apt['patient_id']}/session/{apt['id']}")
    assert "Recommendations · Telegram" in _section(archive.text)


def test_hebrew_labels() -> None:
    from web.services.followup_view import delivery_view

    assert delivery_view([], "he") is None
    view = delivery_view(
        [{"ts": "2026-03-02T12:00:00Z", "kind": "followup", "channel": "email", "status": "sent"}],
        "he",
    )
    assert view is not None and view["title"] == "הודעות"
    assert view["items"][0] | {} == {
        "time": "2026-03-02 14:00",
        "what": "מעקב 24 שעות",
        "channel": "אימייל",
        "direction": "out",
        "status": "sent",
        "status_label": "נשלח",
        "error": "",
        "count": 1,
        "repeat": "",
    }
