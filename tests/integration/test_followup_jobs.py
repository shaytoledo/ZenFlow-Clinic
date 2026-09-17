"""Phase 1.3 — the follow-up and recommendation schedulers run on the durable job queue.

Primary path: "Complete Session" enqueues `followup.send_step1` (T+24h) and
`recommendations.dispatch` (at the queued send time). A low-frequency reconciliation sweep
re-enqueues anything missed. Handlers are idempotent against the database, not only Redis.
Also fixes F1: the email fallback called send_email() without the therapist id.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import treatment_repo
from zenflow import clock
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"


def _jobs() -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute("SELECT * FROM jobs ORDER BY id")]


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


async def _complete(client: httpx.AsyncClient, apt: dict[str, Any]) -> None:
    url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}/complete"
    resp = await client.post(url, json={"session_notes": "done"})
    assert resp.status_code == 200, resp.text


@pytest.fixture
def clinic(authenticated_client, make_appointment, make_treatment_notes):
    """A Telegram patient's appointment with AI recommendations, owned by the signed-in therapist."""
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    return authenticated_client, apt


async def test_complete_session_enqueues_both_jobs_once(clinic) -> None:
    client, apt = clinic
    with freeze_time(FROZEN, ignore=["itsdangerous"]):
        await _complete(client, apt)
        await _complete(client, apt)  # a second click must not duplicate anything
    jobs = {j["name"]: j for j in _jobs()}
    assert set(jobs) == {"followup.send_step1", "recommendations.dispatch"}
    assert len(_jobs()) == 2
    assert jobs["followup.send_step1"]["run_at"] == "2026-03-02T12:00:00Z"
    assert json.loads(jobs["followup.send_step1"]["payload_json"]) == {
        "appointment_id": apt["id"],
        "completed_at": FROZEN,
    }
    assert jobs["recommendations.dispatch"]["run_at"] == "2026-03-02T12:00:00Z"


async def test_jobs_fire_at_24h_exactly_once(clinic, fake_telegram) -> None:
    client, apt = clinic
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=23))
        assert await worker.run_once() == 0, "nothing is due before 24h"
        assert fake_telegram.calls == []

        frozen.tick(timedelta(hours=1, minutes=1))
        assert await worker.run_once() == 2
        texts = [c["text"] for c in fake_telegram.calls]
        assert len(texts) == 2
        assert any("1–10" in t for t in texts), "follow-up step 1 was sent"
        assert any("recommendations" in t.lower() for t in texts), "recommendations were sent"
        assert all(c["chat_id"] == apt["patient_id"] for c in fake_telegram.calls)

        notes = treatment_repo.get_by_appointment(apt["id"])
        assert notes is not None
        assert clock.is_canonical(notes["followup_sent_at"])
        assert notes.get("pending_recommendations") in (None, [], "")
        assert {j["status"] for j in _jobs()} == {"done"}

        frozen.tick(timedelta(hours=1))
        assert await worker.run_once() == 0
        assert len(fake_telegram.calls) == 2, "no re-send"


async def test_reconciliation_enqueues_sessions_completed_without_a_job(
    make_completed_session, fake_telegram
) -> None:
    from bot.services.followup_scheduler import reconcile

    with freeze_time(FROZEN):
        apt = make_completed_session(completed_at=clock.hours_ago(3))
        old = make_completed_session(completed_at=clock.hours_ago(72), apt_time="11:00")
        assert _jobs() == []
        assert reconcile()["followups"] == 1
        assert reconcile()["followups"] == 1  # idempotent: still one job
        (job,) = _jobs()
        assert json.loads(job["payload_json"])["appointment_id"] == apt["id"]
        assert job["run_at"] == "2026-03-02T09:00:00Z"  # completed_at + 24h
        assert old["id"] != apt["id"]


async def test_followup_already_sent_in_db_is_not_sent_again(
    make_completed_session, fake_telegram
) -> None:
    from bot.services.followup_jobs import enqueue_followup

    with freeze_time(FROZEN) as frozen:
        apt = make_completed_session(completed_at=clock.iso_now())
        dbmod.get_db().execute(
            "UPDATE treatment_notes SET followup_sent_at=? WHERE appointment_id=?",
            (clock.iso_now(), apt["id"]),
        )
        enqueue_followup(apt["id"], clock.iso_now())
        frozen.tick(timedelta(hours=24, minutes=1))
        assert await _worker().run_once() == 1
    assert fake_telegram.calls == [], "the database stamp, not a Redis key, is the dedupe"


async def test_followup_that_fires_too_late_is_skipped(
    make_completed_session, fake_telegram
) -> None:
    from bot.services.followup_jobs import FOLLOWUP_EXPIRE_HOURS, enqueue_followup

    with freeze_time(FROZEN) as frozen:
        apt = make_completed_session(completed_at=clock.iso_now())
        enqueue_followup(apt["id"], clock.iso_now())
        frozen.tick(timedelta(hours=FOLLOWUP_EXPIRE_HOURS + 1))  # bot was down for two days
        assert await _worker().run_once() == 1
    assert fake_telegram.calls == []


async def test_telegram_failure_retries_the_followup(
    make_completed_session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.services.followup_jobs import enqueue_followup

    with freeze_time(FROZEN) as frozen:
        apt = make_completed_session(completed_at=clock.iso_now())
        enqueue_followup(apt["id"], clock.iso_now())
        frozen.tick(timedelta(hours=24, minutes=1))
        # block_real_telegram (autouse) makes every send raise → the job must retry, not vanish
        assert await _worker().run_once() == 1
    (job,) = _jobs()
    assert job["status"] == "pending" and job["attempts"] == 1 and job["last_error"]
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and not notes["followup_sent_at"]


async def test_manual_patient_email_fallback_passes_the_therapist_id_f1(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, monkeypatch
) -> None:
    """F1: the fallback used to call send_email(to, subject, body) — always a TypeError."""
    import web.services.email_service as es

    sent: list[tuple[Any, ...]] = []
    monkeypatch.setattr(es, "send_email", lambda *a, **k: sent.append(a))

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Manual Patient", manual=True)
    apt = make_appointment(
        therapist={"id": tid}, patient=patient, apt_date="2026-03-01", apt_time="10:00"
    )
    dbmod.get_db().execute(
        "UPDATE appointments SET patient_email='patient@example.com' WHERE id=?", (apt["id"],)
    )
    make_treatment_notes(apt)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await _worker().run_once()

    assert len(sent) == 1
    therapist_id, to, subject, body = sent[0]
    assert therapist_id == tid
    assert to == "patient@example.com"
    assert "recommendations" in subject.lower() and body
    assert {j["name"]: j["status"] for j in _jobs()} == {
        "followup.send_step1": "done",  # manual patient: no messaging channel, skipped
        "recommendations.dispatch": "done",
    }
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and not notes.get("pending_recommendations")


async def test_gmail_not_connected_alerts_once_and_keeps_the_recommendations(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, monkeypatch
) -> None:
    import web.services.email_service as es

    def _not_connected(*_a: Any, **_k: Any) -> None:
        raise es.EmailNotConfigured("therapist has not connected Google")

    monkeypatch.setattr(es, "send_email", _not_connected)
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid},
        patient=make_patient("M", manual=True),
        apt_date="2026-03-01",
        apt_time="10:00",
    )
    dbmod.get_db().execute(
        "UPDATE appointments SET patient_email='m@example.com' WHERE id=?", (apt["id"],)
    )
    make_treatment_notes(apt)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await _worker().run_once()

    failed = (
        dbmod.get_db()
        .execute(
            "SELECT COUNT(*) FROM notifications WHERE kind='recommendations_waiting_google' "
            "AND appointment_id=?",
            (apt["id"],),
        )
        .fetchone()[0]
    )
    assert failed == 1
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and notes.get("pending_recommendations"), "kept for Send Now"


async def test_rescheduled_recommendations_do_not_fire_on_the_old_job(
    clinic, fake_telegram
) -> None:
    from bot.services.followup_jobs import enqueue_recommendations

    client, apt = clinic
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)  # queued for T+24h
        later = clock.hours_ahead(48)
        treatment_repo.save_pending_recommendations(
            apt["id"], [{"category": "Diet", "text": "x"}], later
        )
        enqueue_recommendations(apt["id"], later)
        frozen.tick(timedelta(hours=24, minutes=1))
        await _worker().run_once()
        assert not any("recommendations" in c["text"].lower() for c in fake_telegram.calls)
        frozen.tick(timedelta(hours=24))
        await _worker().run_once()
        assert sum("recommendations" in c["text"].lower() for c in fake_telegram.calls) == 1
