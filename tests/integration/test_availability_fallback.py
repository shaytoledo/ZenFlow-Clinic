"""Phase 11.6 (chaos) — availability degrades to local SQLite when Google is unavailable.

When a therapist has not connected Google Calendar (no token), booking must still work from the
local `availability` table. The bot booking-flow tests stub `get_available_days`/`get_available_hours`
out entirely, so the real fallback path (`_gcal_service` → None → `_read_local_avail` → `_local_hours`)
was never exercised. These tests drive it directly, and prove a Redis-cache outage doesn't take
availability down with it.

`clinic_today` and `_gcal_service` are pinned so the bookable window and the "no Google" branch are
deterministic.
"""

from __future__ import annotations

from datetime import date

import pytest

import bot.patient_bot.services.availability as avail
from web.repositories import availability_repo

pytestmark = pytest.mark.integration

_MONDAY = date(2026, 3, 2)  # anchor: offset-0 window is Tue 03-03 .. Sun 03-08
_WED = date(2026, 3, 4)


@pytest.fixture
def no_google(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the not-connected branch and a fixed 'today' so the window is deterministic."""
    monkeypatch.setattr(avail, "_gcal_service", lambda *_a, **_k: None)
    monkeypatch.setattr(avail, "clinic_today", lambda: _MONDAY)


async def test_local_slots_are_used_when_google_is_not_connected(
    no_google, fake_redis, make_therapist
) -> None:
    t = make_therapist(therapist_id="t_local")
    availability_repo.insert(t["id"], "2026-03-04T09:00:00Z", "2026-03-04T12:00:00Z")

    days = await avail.get_available_days(0, t["id"])
    assert _WED in days, "the local slot's date appears in the bookable week"

    hours = await avail.get_available_hours(_WED, t["id"])
    assert hours == ["09:00", "10:00", "11:00"], "a 3-hour slot yields three 1-hour openings"


async def test_a_booked_hour_is_removed_from_local_availability(
    no_google, fake_redis, make_therapist, make_appointment
) -> None:
    t = make_therapist(therapist_id="t_local")
    availability_repo.insert(t["id"], "2026-03-04T09:00:00Z", "2026-03-04T12:00:00Z")
    # An existing appointment at 10:00 on that day must not be offered again.
    make_appointment(therapist=t, apt_date="2026-03-04", apt_time="10:00")

    hours = await avail.get_available_hours(_WED, t["id"])
    assert hours == ["09:00", "11:00"], "the booked 10:00 hour is filtered out"


async def test_no_local_availability_yields_empty(no_google, fake_redis, make_therapist) -> None:
    t = make_therapist(therapist_id="t_empty")
    assert await avail.get_available_days(0, t["id"]) == []
    assert await avail.get_available_hours(_WED, t["id"]) == []


async def test_availability_survives_redis_being_down(
    no_google, make_therapist, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_therapist(therapist_id="t_local")
    availability_repo.insert(t["id"], "2026-03-04T09:00:00Z", "2026-03-04T12:00:00Z")

    def _redis_down():
        raise RuntimeError("redis is unreachable")

    # The cache lookup/save is best-effort — a Redis outage must not break availability.
    monkeypatch.setattr("bot.redis_client.get_async_redis", _redis_down)

    days = await avail.get_available_days(0, t["id"])
    hours = await avail.get_available_hours(_WED, t["id"])
    assert _WED in days
    assert hours == ["09:00", "10:00", "11:00"]
