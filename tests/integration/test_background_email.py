"""Phase 5.4 — queued email recommendations and the therapist's Google account.

* Google not connected → ONE "waiting for Google" alert, the job stays queued (deferred, no
  attempt charged) and runs as soon as the therapist connects Google.
* A token that Google refuses mid-flight → ONE reconnect alert, the job retries and dead-letters
  after its attempts with ONE "send failed" alert.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import treatment_repo
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"
PW = "pw-Test-123"


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _rec_job() -> dict[str, Any]:
    row = (
        dbmod.get_db()
        .execute("SELECT * FROM jobs WHERE name='recommendations.dispatch'")
        .fetchone()
    )
    assert row is not None
    return dict(row)


def _alerts(kind: str, *, open_only: bool = True) -> int:
    clause = " AND resolved_at IS NULL" if open_only else ""
    return int(
        dbmod.get_db()
        .execute(f"SELECT COUNT(*) FROM notifications WHERE kind=?{clause}", (kind,))
        .fetchone()[0]
    )


def _connect(therapist_id: str) -> None:
    dbmod.get_db().execute(
        """INSERT OR REPLACE INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at)
           VALUES (?, 'x', '', '2026-01-01T00:00:00Z')""",
        (therapist_id,),
    )


class FakeGmail:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[dict[str, Any]] = []

    def users(self) -> FakeGmail:
        return self

    def messages(self) -> FakeGmail:
        return self

    def send(self, userId: str, body: dict[str, Any]) -> FakeGmail:  # noqa: N803 - Google's name
        self.sent.append(body)
        return self

    def execute(self) -> dict[str, str]:
        if self.error:
            raise self.error
        return {"id": "m1"}


@pytest.fixture
def email_patient(authenticated_client, make_appointment, make_patient, make_treatment_notes):
    """A completed-able session of a manual patient with an email address, owned by the client."""
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid},
        patient=make_patient("Dana Levi", manual=True),
        apt_date="2026-03-01",
        apt_time="10:00",
    )
    dbmod.get_db().execute(
        "UPDATE appointments SET patient_email='dana@example.com' WHERE id=?", (apt["id"],)
    )
    make_treatment_notes(apt)
    return authenticated_client, apt, tid


async def _complete(client: Any, apt: dict[str, Any]) -> None:
    url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}/complete"
    assert (await client.post(url, json={"session_notes": "x"})).status_code == 200


async def test_a_send_waits_for_google_and_goes_out_once_it_is_connected(
    email_patient, monkeypatch
) -> None:
    import web.gcal as gcal
    from bot.services.followup_jobs import GOOGLE_RECHECK_HOURS, resume_after_google_connected

    client, apt, tid = email_patient
    gmail = FakeGmail()
    monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: gmail)
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        for check in range(3):  # the first run (with the follow-up job, skipped) and two rechecks
            if check:
                frozen.tick(timedelta(hours=GOOGLE_RECHECK_HOURS))
            assert await worker.run_once() >= 1
            job = _rec_job()
            assert job["status"] == "pending" and job["attempts"] == 0, "waiting, not failing"
            assert "connect Google" in job["last_error"]
        assert _alerts("recommendations_waiting_google") == 1, "one alert, not one per check"
        assert _alerts("send_failed") == 0
        assert gmail.sent == []
        notes = treatment_repo.get_by_appointment(apt["id"])
        assert notes is not None and notes.get("pending_recommendations"), "still queued"

        frozen.tick(timedelta(minutes=5))  # connecting happens between two rechecks
        _connect(tid)
        assert resume_after_google_connected(tid) == 1
        assert resume_after_google_connected("someone-else") == 0
        assert await worker.run_once() == 1, "due at once, not at the next recheck"

    assert _rec_job()["status"] == "done"
    assert len(gmail.sent) == 1
    assert _alerts("recommendations_waiting_google") == 0, "resolved by the delivery"
    assert _alerts("recommendations_waiting_google", open_only=False) == 1
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and not notes.get("pending_recommendations")


async def test_connecting_google_through_oauth_wakes_the_send(email_patient, monkeypatch) -> None:
    import web.routers.auth as auth

    async def _no_prefetch(_tid: str) -> None:
        return None

    client, apt, tid = email_patient
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        auth, "get_auth_url", lambda: ("https://accounts.google.com/o/oauth2/auth", "oauth-st8")
    )
    monkeypatch.setattr(auth, "exchange_code", lambda code, therapist_id: _connect(therapist_id))
    monkeypatch.setattr(auth, "prefetch_calendar", _no_prefetch)
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        waiting_until = _rec_job()["run_at"]
        await client.get("/auth/login")
        done = await client.get("/auth/callback?code=abc&state=oauth-st8")
        assert done.status_code in (302, 307)
        job = _rec_job()
        assert job["status"] == "pending" and job["run_at"] < waiting_until


async def test_sending_by_hand_settles_the_waiting_alert(email_patient) -> None:
    client, apt, _tid = email_patient
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        assert _alerts("recommendations_waiting_google") == 1
        treatment_repo.clear_pending_recommendations(apt["id"])  # e.g. "Send Now" by hand
        frozen.tick(timedelta(hours=7))
        await worker.run_once()
    assert _rec_job()["status"] == "done"
    assert _alerts("recommendations_waiting_google") == 0


@pytest.mark.parametrize("where", ["refresh", "send"])
async def test_a_refused_token_retries_then_dead_letters_with_one_alert_each(
    email_patient, monkeypatch, where: str
) -> None:
    from google.auth.exceptions import RefreshError

    import web.gcal as gcal

    client, apt, tid = email_patient
    _connect(tid)
    if where == "refresh":

        def _refused(_tid: str) -> Any:
            raise RefreshError("invalid_grant: Token has been expired or revoked.")

        monkeypatch.setattr(gcal, "get_gmail_service", _refused)
    else:
        gmail = FakeGmail(Exception("<HttpError 401 'Invalid Credentials'>: unauthorized"))
        monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: gmail)
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        for _ in range(q.DEFAULT_MAX_ATTEMPTS):
            assert await worker.run_once() >= 1  # the first run also takes the follow-up job
            frozen.tick(timedelta(hours=1))  # past every backoff
    job = _rec_job()
    assert job["status"] == "dead" and job["attempts"] == q.DEFAULT_MAX_ATTEMPTS
    assert _alerts("gmail_token_expired") == 1, "one reconnect alert"
    assert _alerts("send_failed") == 1, "one final alert"
    assert _alerts("recommendations_waiting_google") == 0
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and notes.get("pending_recommendations"), "kept for Send Now"


def test_send_failed_alerts_once_per_appointment(make_therapist, make_appointment) -> None:
    from web.services import notification_service as ns

    therapist = make_therapist()
    a = make_appointment(therapist=therapist)
    b = make_appointment(therapist=therapist, apt_time="11:00")
    first = ns.alert_send_failed(therapist["id"], a["id"], a["patient_id"], "A", "boom")
    assert ns.alert_send_failed(therapist["id"], a["id"], a["patient_id"], "A", "again") == first
    assert ns.alert_send_failed(therapist["id"], b["id"], b["patient_id"], "B", "boom") != first
    assert _alerts("send_failed") == 2
