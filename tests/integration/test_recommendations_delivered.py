"""Phase 5.5 — a delivered queue stamps the session, so it is never sent twice.

Before this, background delivery only cleared the queue entry; `recommendations_sent_at` stayed
empty, and completing the session again queued (and later sent) the same recommendations again.
"""

from __future__ import annotations

import base64
import email
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
DELIVERED_AT = "2026-03-02T12:01:00Z"


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _rec_jobs() -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in dbmod.get_db().execute(
            "SELECT * FROM jobs WHERE name='recommendations.dispatch' ORDER BY id"
        )
    ]


async def _complete(client: Any, apt: dict[str, Any]) -> None:
    url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}/complete"
    assert (await client.post(url, json={"session_notes": "x"})).status_code == 200


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def users(self) -> FakeGmail:
        return self

    def messages(self) -> FakeGmail:
        return self

    def send(self, userId: str, body: dict[str, Any]) -> FakeGmail:  # noqa: N803 - Google's name
        self.sent.append({"userId": userId, **body})
        return self

    def execute(self) -> dict[str, str]:
        return {"id": "m1"}


async def test_an_emailed_queue_is_stamped_and_not_sent_again(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, monkeypatch
) -> None:
    import web.gcal as gcal

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    dbmod.get_db().execute(
        """INSERT INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at)
           VALUES (?, 'x', '', '2026-01-01T00:00:00Z')""",
        (tid,),
    )
    gmail = FakeGmail()
    monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: gmail)
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
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        # the therapist opens the session again and presses "Complete Session" once more
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=48))
        await worker.run_once()

    assert len(gmail.sent) == 1, "delivered once, not re-queued by the second completion"
    assert gmail.sent[0]["userId"] == "me"
    mime = email.message_from_bytes(base64.urlsafe_b64decode(gmail.sent[0]["raw"]))
    assert mime["To"] == "dana@example.com"
    assert mime.get_content_type() == "text/plain" and mime.get_content_charset() == "utf-8"
    body = mime.get_payload(decode=True)
    assert isinstance(body, bytes) and body.decode("utf-8").startswith("Hi Dana,")
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None
    assert notes["recommendations_sent_at"] == DELIVERED_AT
    assert not notes.get("pending_recommendations") and not notes.get("pending_rec_send_at")
    assert [j["status"] for j in _rec_jobs()] == ["done"]


async def test_a_telegram_queue_is_stamped_too(
    authenticated_client, make_appointment, make_treatment_notes, fake_telegram
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and notes["recommendations_sent_at"] == DELIVERED_AT
    assert not notes.get("pending_recommendations")


def test_marking_delivered_touches_only_that_session(
    make_appointment, make_treatment_notes, frozen_clock
) -> None:
    a = make_appointment()
    b = make_appointment(apt_time="11:00")
    for apt in (a, b):
        make_treatment_notes(apt)
        treatment_repo.save_pending_recommendations(
            apt["id"], [{"text": "x"}], "2026-03-02T10:00:00Z"
        )
    treatment_repo.mark_recommendations_delivered(a["id"])
    notes_a = treatment_repo.get_by_appointment(a["id"])
    notes_b = treatment_repo.get_by_appointment(b["id"])
    assert notes_a is not None and notes_b is not None
    assert notes_a["recommendations_sent_at"] and not notes_a.get("pending_recommendations")
    assert not notes_b.get("recommendations_sent_at") and notes_b.get("pending_recommendations")
