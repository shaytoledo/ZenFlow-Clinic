"""Phase 1.1 / F2 — the 24h follow-up window must be timezone-proof.

Complete a session through the real API, then advance the frozen clock: the appointment is
due at T+23h and not at T+2h or T+48h — identically when the host clock is UTC, Jerusalem
(UTC+2/+3) or Los Angeles (UTC-7/-8).
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from freezegun import freeze_time

from bot.services.followup_scheduler import _find_due_followups
from web.repositories import treatment_repo
from zenflow import clock

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"


@pytest.mark.parametrize("clinic_tz", ["UTC", "Asia/Jerusalem", "America/Los_Angeles"])
async def test_followup_window_matches_at_23h_only(
    clinic_tz: str,
    authenticated_client: httpx.AsyncClient,
    make_appointment,
    make_treatment_notes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The window is an instant comparison in UTC: the clinic's zone (and the host's) must not
    move it. Host-local time cannot leak in by construction (ruff DTZ bans datetime.now())."""
    from zenflow import settings as S

    monkeypatch.setenv("CLINIC_TZ", clinic_tz)
    S.reset_settings()
    therapist_id = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": therapist_id}, apt_date="2026-03-01", apt_time="10:00")
    make_treatment_notes(apt)

    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/10-00/complete"
        resp = await authenticated_client.post(url, json={"session_notes": "done"})
        assert resp.status_code == 200, resp.text

        notes = treatment_repo.get_by_appointment(apt["id"])
        assert notes is not None
        assert notes["completed_at"] == FROZEN, "completed_at must be canonical UTC"
        assert clock.is_canonical(notes["updated_at"])
        assert notes["pending_rec_send_at"] == "2026-03-02T12:00:00Z"  # auto-queued +24h, UTC

        due_ids = lambda: {r["appointment_id"] for r in _find_due_followups()}  # noqa: E731
        frozen.tick(timedelta(hours=2))
        assert apt["id"] not in due_ids(), "T+2h must not be due"
        frozen.tick(timedelta(hours=21))  # T+23h
        assert apt["id"] in due_ids(), "T+23h must be due"
        frozen.tick(timedelta(hours=25))  # T+48h
        assert apt["id"] not in due_ids(), "T+48h must no longer be due"


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
