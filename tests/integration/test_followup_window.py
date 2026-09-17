"""Phase 1.1 / F2, re-checked in Phase 6.1 — the 24h follow-up fires on an instant, not a zone.

Complete a session through the real API, then advance the frozen clock: step 1 goes out at
T+24h and not before — identically when the clinic is in UTC, Jerusalem (UTC+2/+3) or Los Angeles
(UTC-7/-8). Since Phase 1.3 the follow-up is a queued job (`completed_at + 24h`); the old 22–26 h
polling window is gone. The host's own zone cannot leak in by construction: ruff DTZ bans naive
`datetime.now()`. (freezegun's `tz_offset` cannot test that, because it also shifts
`datetime.now(UTC)`.)
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from freezegun import freeze_time

from web.repositories import treatment_repo
from zenflow import clock
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _step1(calls: list[dict]) -> int:
    return sum("1–10" in c["text"] for c in calls)


@pytest.mark.parametrize("clinic_tz", ["UTC", "Asia/Jerusalem", "America/Los_Angeles"])
async def test_step1_goes_out_at_24h_in_every_zone(
    clinic_tz: str,
    authenticated_client: httpx.AsyncClient,
    make_appointment,
    make_treatment_notes,
    fake_telegram,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("CLINIC_TZ", clinic_tz)
    S.reset_settings()
    therapist_id = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": therapist_id}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    worker = _worker()

    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/10-00/complete"
        resp = await authenticated_client.post(url, json={"session_notes": "done"})
        assert resp.status_code == 200, resp.text

        notes = treatment_repo.get_by_appointment(apt["id"])
        assert notes is not None
        assert notes["completed_at"] == FROZEN, "completed_at must be canonical UTC"
        assert clock.is_canonical(notes["updated_at"])
        assert notes["pending_rec_send_at"] == "2026-03-02T12:00:00Z"  # auto-queued +24h, UTC

        frozen.tick(timedelta(hours=2))
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 0, "T+2h is too early"
        frozen.tick(timedelta(hours=21, minutes=59))  # T+23h59m
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 0, "one minute before 24h is still too early"
        frozen.tick(timedelta(minutes=1))  # T+24h
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 1, "T+24h sends step 1"
        frozen.tick(timedelta(hours=24))
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 1, "and never again"


async def test_pending_recommendations_due_uses_the_same_clock(
    authenticated_client: httpx.AsyncClient, make_appointment, make_treatment_notes
) -> None:
    therapist_id = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": therapist_id}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    with freeze_time(FROZEN, tz_offset=3, ignore=["itsdangerous"]) as frozen:
        url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/10-00/complete"
        assert (await authenticated_client.post(url, json={})).status_code == 200
        assert treatment_repo.list_due_pending_recommendations(clock.iso_now()) == []
        frozen.tick(timedelta(hours=24, minutes=1))
        due = treatment_repo.list_due_pending_recommendations(clock.iso_now())
        assert [r["appointment_id"] for r in due] == [apt["id"]]


async def test_a_redis_flush_neither_loses_nor_repeats_step1(
    authenticated_client: httpx.AsyncClient,
    make_appointment,
    make_treatment_notes,
    fake_telegram,
    fake_redis,
) -> None:
    """Plan 6.1: the Redis "already sent" key was the only dedupe; a flush meant a re-send.
    The job and the `followup_sent_at` stamp live in the database."""
    therapist_id = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": therapist_id}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/10-00/complete"
        assert (await authenticated_client.post(url, json={})).status_code == 200
        fake_redis.sync.flushall()  # between completion and T+24h
        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 1, "the job survived the flush"
        fake_redis.sync.flushall()  # after the send
        frozen.tick(timedelta(hours=1))
        from bot.services.followup_scheduler import reconcile

        reconcile()  # the safety-net sweep must not re-send either
        await worker.run_once()
    assert _step1(fake_telegram.calls) == 1, "no re-send without the Redis key"
