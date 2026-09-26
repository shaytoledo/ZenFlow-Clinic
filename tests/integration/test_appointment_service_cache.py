"""Phase 11.1 — appointment_service read helpers + the 30s Redis cache.

Complements `test_appointment_aggregation` by covering the thin repo passthroughs and, importantly,
`list_all_cached`: it serves the dashboard from a 30-second Redis cache, populates it on a miss, and —
the resilience property — falls back to a direct read when Redis is unavailable.
"""

from __future__ import annotations

import json

import pytest

from web.services import appointment_service as svc

pytestmark = pytest.mark.integration

_CACHE_KEY = "zenflow:apts:all"


async def test_list_all_cached_populates_the_cache_on_a_miss(fake_redis, make_appointment) -> None:
    apt = make_appointment(apt_date="2026-03-02", apt_time="10:00")
    from bot.redis_client import get_async_redis

    assert await get_async_redis().get(_CACHE_KEY) is None, "cache starts empty"

    data = await svc.list_all_cached()
    assert any(a["patient_id"] == apt["patient_id"] for a in data), "the miss reads from the DB"
    assert await get_async_redis().get(_CACHE_KEY) is not None, "the result is now cached"


async def test_list_all_cached_returns_the_cached_value_on_a_hit(fake_redis) -> None:
    from bot.redis_client import get_async_redis

    sentinel = [{"patient_id": -1, "marker": "from-cache"}]
    await get_async_redis().set(_CACHE_KEY, json.dumps(sentinel))
    # No appointments exist in the DB, so a non-cached read would be []; getting the sentinel back
    # proves the cache was used.
    assert await svc.list_all_cached() == sentinel


async def test_list_all_cached_falls_back_to_a_direct_read_when_redis_is_down(
    make_appointment, monkeypatch
) -> None:
    apt = make_appointment(apt_date="2026-03-02", apt_time="10:00")

    def _boom():
        raise RuntimeError("redis is unavailable")

    monkeypatch.setattr("bot.redis_client.get_async_redis", _boom)
    data = await svc.list_all_cached()
    assert any(
        a["patient_id"] == apt["patient_id"] for a in data
    ), "a Redis outage still returns data"


def test_read_helpers_delegate_to_the_repository(make_appointment) -> None:
    apt = make_appointment(apt_date="2026-03-02", apt_time="10:00")
    tid = apt["therapist_id"]

    assert any(a["id"] == apt["id"] for a in svc.list_all())
    found = svc.get_by_patient_date_time(apt["patient_id"], "2026-03-02", "10:00", tid)
    assert found and found["id"] == apt["id"]
    by_patient = svc.list_by_patient(apt["patient_id"], tid)
    assert [a["id"] for a in by_patient] == [apt["id"]]


def test_get_by_patient_date_time_is_none_for_a_missing_slot(make_appointment) -> None:
    apt = make_appointment(apt_date="2026-03-02", apt_time="10:00")
    assert svc.get_by_patient_date_time(apt["patient_id"], "2026-03-02", "23:00") is None
