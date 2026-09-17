"""Regressions for the PR #6 (Phase 1.3) review findings — at-least-once hazards."""

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


def _jobs(name: str | None = None) -> list[dict[str, Any]]:
    rows = [dict(r) for r in dbmod.get_db().execute("SELECT * FROM jobs ORDER BY id")]
    return [r for r in rows if name is None or r["name"] == name]


def _worker(**kw: Any) -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test", **kw)


def _step1_sends(calls: list[dict[str, Any]]) -> int:
    return sum("0–10" in c["text"] for c in calls)


def _rec_sends(calls: list[dict[str, Any]]) -> int:
    return sum("recommendations" in c["text"].lower() for c in calls)


async def _complete(client: httpx.AsyncClient, apt: dict[str, Any]) -> None:
    url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}/complete"
    assert (await client.post(url, json={"session_notes": "x"})).status_code == 200


@pytest.fixture
def clinic(authenticated_client, make_appointment, make_treatment_notes):
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    return authenticated_client, apt


# ── finding 1: completing again reschedules the follow-up from the latest completion ──
async def test_second_completion_reschedules_the_followup(clinic, fake_telegram) -> None:
    client, apt = clinic
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=20))
        await _complete(client, apt)  # therapist fixes notes 20h later
        assert [j["run_at"] for j in _jobs("followup.send_step1")] == [
            "2026-03-02T12:00:00Z",
            "2026-03-03T08:00:00Z",
        ]
        frozen.tick(timedelta(hours=4, minutes=1))  # first job due: superseded, must not send
        await worker.run_once()
        assert _step1_sends(fake_telegram.calls) == 0
        frozen.tick(timedelta(hours=20))  # 24h after the second completion
        await worker.run_once()
        assert _step1_sends(fake_telegram.calls) == 1


# ── findings 2/3: a failure after a successful send must not re-send, and the stamp is repaired ──
async def test_db_stamp_failure_after_send_is_repaired_without_resending(
    make_completed_session, fake_telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.services import followup_scheduler as fs
    from bot.services.followup_jobs import enqueue_followup

    real_stamp = fs._stamp_sent
    calls = {"n": 0}

    def _flaky_stamp(appointment_id: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        real_stamp(appointment_id)

    monkeypatch.setattr(fs, "_stamp_sent", _flaky_stamp)
    with freeze_time(FROZEN) as frozen:
        apt = make_completed_session(completed_at=clock.iso_now())
        enqueue_followup(apt["id"], clock.iso_now())
        frozen.tick(timedelta(hours=24, minutes=1))
        worker = _worker()
        await worker.run_once()  # sends, stamp fails → retry scheduled
        frozen.tick(timedelta(seconds=q.BACKOFF_BASE_SECONDS + 1))
        await worker.run_once()  # retry repairs the stamp
    assert _step1_sends(fake_telegram.calls) == 1
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and clock.is_canonical(notes["followup_sent_at"])
    assert [j["status"] for j in _jobs()] == ["done"]


async def test_redis_failure_after_send_does_not_resend(
    make_completed_session, fake_telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bot.services import followup_scheduler as fs
    from bot.services.followup_jobs import enqueue_followup

    async def _redis_down(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("redis timeout")

    from web.repositories import followup_repo

    def _db_write_down(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("database is locked")

    monkeypatch.setattr(fs, "_mark_sent", _redis_down)
    monkeypatch.setattr(followup_repo, "mark_sent", _db_write_down)  # opening the conversation
    with freeze_time(FROZEN) as frozen:
        apt = make_completed_session(completed_at=clock.iso_now())
        enqueue_followup(apt["id"], clock.iso_now())
        frozen.tick(timedelta(hours=24, minutes=1))
        worker = _worker()
        await worker.run_once()
        frozen.tick(timedelta(hours=1))
        await worker.run_once()
    assert _step1_sends(fake_telegram.calls) == 1
    assert [j["status"] for j in _jobs()] == ["done"]


# ── finding 4: a failed "sent" notification must not re-send the recommendations ──
async def test_notification_failure_after_send_does_not_resend(
    clinic, fake_telegram, monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.services import notification_service

    def _boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(notification_service, "alert_recommendations_sent", _boom)
    client, apt = clinic
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        frozen.tick(timedelta(hours=2))
        await worker.run_once()
    assert _rec_sends(fake_telegram.calls) == 1
    assert {j["status"] for j in _jobs("recommendations.dispatch")} == {"done"}


# ── finding 5: "Send Now" after the auto-queue must not deliver twice ──
async def test_send_now_clears_the_auto_queued_recommendations(clinic, fake_telegram) -> None:
    client, apt = clinic
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)  # auto-queued for T+24h
        url = f"/api/treatment-notes/{apt['patient_id']}/2026-03-01/10-00/send-recommendations"
        resp = await client.post(
            url,
            json={
                "items": [{"enabled": True, "category": "Diet", "text": "warm food"}],
                "schedule_hours": 0,
            },
        )
        assert resp.status_code == 200, resp.text
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and not notes.get("pending_recommendations")
    # exactly one delivery of recommendations reached the patient
    assert len([c for c in fake_telegram.calls if "0–10" not in c["text"]]) == 1


# ── finding 6: a job that dead-letters on timeout still alerts the therapist ──
async def test_recommendation_timeout_on_final_attempt_alerts_once(
    clinic, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from bot.services import followup_scheduler as fs

    async def _hang(row: dict[str, Any]) -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(fs, "dispatch_recommendations", _hang)
    client, apt = clinic
    worker = _worker(handler_timeout=0.01)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        for _ in range(q.DEFAULT_MAX_ATTEMPTS):
            await worker.run_once()
            frozen.tick(timedelta(hours=24))
    (job,) = _jobs("recommendations.dispatch")
    assert job["status"] == "dead"
    alerts = (
        dbmod.get_db()
        .execute(
            "SELECT COUNT(*) FROM notifications WHERE kind='send_failed' AND appointment_id=?",
            (apt["id"],),
        )
        .fetchone()[0]
    )
    assert alerts == 1


# ── finding 7: reconciliation covers the whole expiry window and survives a bad row ──
async def test_reconcile_covers_the_expiry_window_and_skips_bad_rows(
    make_completed_session,
) -> None:
    from bot.services.followup_scheduler import reconcile

    with freeze_time(FROZEN):
        recent = make_completed_session(completed_at=clock.hours_ago(40), apt_time="09:00")
        bad = make_completed_session(completed_at=clock.hours_ago(2), apt_time="10:00")
        good = make_completed_session(completed_at=clock.hours_ago(1), apt_time="11:00")
        treatment_repo.save_pending_recommendations(bad["id"], [{"text": "x"}], "not a time")
        treatment_repo.save_pending_recommendations(
            good["id"], [{"text": "y"}], clock.hours_ahead(5)
        )
        counts = reconcile()
    ids = {json.loads(j["payload_json"])["appointment_id"] for j in _jobs("followup.send_step1")}
    assert recent["id"] in ids, "40h-old session is still inside the 48h expiry window"
    assert [
        json.loads(j["payload_json"])["appointment_id"] for j in _jobs("recommendations.dispatch")
    ] == [good["id"]]
    assert counts["errors"] == 1


# ── finding 8: stronger versions of the weak assertions ──
async def test_gmail_not_connected_does_not_realert_on_later_sweeps(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, monkeypatch
) -> None:
    import web.services.email_service as es
    from bot.services.followup_scheduler import reconcile

    def _nc(*_a: Any, **_k: Any) -> None:
        raise es.EmailNotConfigured("no google")

    monkeypatch.setattr(es, "send_email", _nc)
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
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(authenticated_client, apt)
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        for _ in range(3):  # three reconciliation sweeps later
            frozen.tick(timedelta(minutes=30))
            reconcile()
            await worker.run_once()
    alerts = (
        dbmod.get_db()
        .execute(
            "SELECT COUNT(*) FROM notifications WHERE kind='recommendations_waiting_google' "
            "AND appointment_id=?",
            (apt["id"],),
        )
        .fetchone()[0]
    )
    assert alerts == 1


async def test_telegram_failure_is_the_recorded_retry_reason(
    make_completed_session, fake_telegram
) -> None:
    from bot.services.followup_jobs import enqueue_followup

    fake_telegram.fail_next("Too Many Requests: retry after 30", status=429, retry_after=30)
    with freeze_time(FROZEN) as frozen:
        apt = make_completed_session(completed_at=clock.iso_now())
        enqueue_followup(apt["id"], clock.iso_now())
        frozen.tick(timedelta(hours=24, minutes=1))
        await _worker().run_once()
    (job,) = _jobs()
    assert job["status"] == "pending"
    assert "Too Many Requests: retry after 30" in job["last_error"]
    assert "TEST-PATIENT-BOT-TOKEN" not in job["last_error"]
