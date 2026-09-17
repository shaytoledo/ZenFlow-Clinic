"""Q7 / plan 6.1 — sessions never marked complete, behind ZF_AUTO_FOLLOWUP (off by default).

With the flag on, the reconciliation sweep gives every session that ended within the send window
and was never completed an automatic check-in, 24 h after it ended. Completing the session takes
the check-in over. With the flag off nothing changes. Both paths are tested (plan 0.4).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import followup_repo
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

#: the appointment below is 2026-03-01 10:00 in Jerusalem (UTC+2) = 08:00Z, ended 09:00Z
NOW = "2026-03-01T12:00:00Z"


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _step1(calls: list[dict[str, Any]]) -> int:
    return sum("0–10" in c["text"] for c in calls)


def _jobs() -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute("SELECT * FROM jobs ORDER BY id")]


@pytest.fixture
def flag(monkeypatch):
    from zenflow import settings as S

    def _set(on: bool) -> None:
        monkeypatch.setenv("ZF_AUTO_FOLLOWUP", "1" if on else "0")
        S.reset_settings()

    yield _set
    S.reset_settings()


@pytest.fixture
def session(authenticated_client, make_appointment):
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, apt_date="2026-03-01", apt_time="10:00")
    return authenticated_client, apt


def test_the_flag_is_off_by_default_and_listed(flag) -> None:
    from zenflow import settings as S

    flag(False)
    assert S.get_settings().flags.auto_followup is False
    assert "AUTO_FOLLOWUP" in S.FLAG_NAMES
    assert S.get_settings().flags.snapshot()["AUTO_FOLLOWUP"] is False
    flag(True)
    assert S.get_settings().flags.auto_followup is True


async def test_flag_off_changes_nothing(flag, session, fake_telegram) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(False)
    _client, apt = session
    with freeze_time(NOW) as frozen:
        assert reconcile()["auto"] == 0
        frozen.tick(timedelta(hours=30))
        reconcile()
        await _worker().run_once()
    assert followup_repo.get(apt["id"]) is None and _jobs() == []
    assert fake_telegram.calls == []


async def test_flag_on_sends_an_automatic_checkin_24h_after_the_session(
    flag, session, fake_telegram
) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(True)
    _client, apt = session
    worker = _worker()
    with freeze_time(NOW) as frozen:
        assert reconcile()["auto"] == 1
        assert reconcile()["auto"] == 1, "idempotent"
        (job,) = _jobs()
        assert job["run_at"] == "2026-03-02T09:00:00Z"
        row = followup_repo.get(apt["id"])
        assert row is not None and row["auto"] and row["status"] == "scheduled"

        frozen.tick(timedelta(hours=20, minutes=59))
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 0
        frozen.tick(timedelta(minutes=1))
        await worker.run_once()
    assert _step1(fake_telegram.calls) == 1
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "sent" and row["auto"]

    from web.services.followup_view import followup_view

    view = followup_view(row, "en")
    assert view is not None and view["detail"].endswith(
        "(Automatic: the session was not marked complete.)"
    )


async def test_completing_the_session_takes_the_checkin_over(
    flag, session, fake_telegram, make_treatment_notes
) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(True)
    client, apt = session
    make_treatment_notes(apt)
    worker = _worker()
    with freeze_time(NOW, ignore=["itsdangerous"]) as frozen:
        reconcile()
        frozen.tick(timedelta(hours=2))
        resp = await client.post(
            f"/api/treatment-notes/{apt['patient_id']}/2026-03-01/10-00/complete", json={}
        )
        assert resp.status_code == 200
        row = followup_repo.get(apt["id"])
        assert row is not None and not row["auto"], "the completion owns it now"
        assert row["scheduled_for"] == "2026-03-02T14:00:00Z"
        frozen.tick(timedelta(hours=19, minutes=1))  # the automatic job's time
        await worker.run_once()
        assert _step1(fake_telegram.calls) == 0, "the automatic job skipped itself"
        frozen.tick(timedelta(hours=5))
        await worker.run_once()
        reconcile()
        await worker.run_once()
    assert _step1(fake_telegram.calls) == 1


async def test_an_automatic_checkin_is_not_sent_again_after_completion(
    flag, session, fake_telegram
) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(True)
    client, apt = session
    worker = _worker()
    with freeze_time(NOW, ignore=["itsdangerous"]) as frozen:
        reconcile()
        frozen.tick(timedelta(hours=21, minutes=1))
        await worker.run_once()  # the automatic check-in goes out (no notes yet)
        assert _step1(fake_telegram.calls) == 1
        resp = await client.post(
            f"/api/treatment-notes/{apt['patient_id']}/2026-03-01/10-00/complete", json={}
        )
        assert resp.status_code == 200  # completed only now
        frozen.tick(timedelta(hours=25))
        await worker.run_once()
    assert _step1(fake_telegram.calls) == 1, "one check-in per session"


@pytest.mark.parametrize(
    ("apt_date", "apt_time", "status"),
    [
        ("2026-03-01", "13:30", "active"),  # not over yet (13:30 local = 11:30Z, ends 12:30Z)
        ("2026-02-26", "10:00", "active"),  # ended more than 48 h ago
        ("2026-03-01", "09:00", "cancelled"),
    ],
)
async def test_only_ended_recent_active_sessions_qualify(
    flag, authenticated_client, make_appointment, apt_date: str, apt_time: str, status: str
) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(True)
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid}, apt_date=apt_date, apt_time=apt_time, status=status
    )
    with freeze_time(NOW):
        assert reconcile()["auto"] == 0
    assert followup_repo.get(apt["id"]) is None


async def test_a_manual_booking_gets_the_call_alert(
    flag, authenticated_client, make_appointment, make_patient, fake_telegram
) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(True)
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid},
        patient=make_patient("M", manual=True),
        apt_date="2026-03-01",
        apt_time="10:00",
    )
    with freeze_time(NOW) as frozen:
        reconcile()
        reconcile()
        frozen.tick(timedelta(hours=22))
        await _worker().run_once()
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "no_channel" and row["auto"]
    alerts = (
        dbmod.get_db()
        .execute("SELECT COUNT(*) FROM notifications WHERE kind='followup_no_channel'")
        .fetchone()[0]
    )
    assert alerts == 1 and fake_telegram.calls == []


def test_a_bad_time_does_not_stop_the_sweep(flag, db, make_appointment) -> None:
    from bot.services.followup_scheduler import reconcile

    flag(True)
    good = make_appointment(apt_date="2026-03-01", apt_time="10:00")
    make_appointment(apt_date="2026-03-01", apt_time="late")
    with freeze_time(NOW):
        counts = reconcile()
    assert counts["auto"] == 1 and counts["errors"] == 1
    assert followup_repo.get(good["id"]) is not None
