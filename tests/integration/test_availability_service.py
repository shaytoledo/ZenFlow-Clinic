"""Phase 11.1/11.2 — the local-availability service layer.

`availability_service` sits between the schedule page and the `availability` table: it lists/adds/
removes local slots, shapes them into FullCalendar events, and invalidates the bot's Redis
availability cache when a slot is added. It was the least-covered service (~27%). These tests drive
the CRUD round-trip, the tenant-scoped delete, the FullCalendar transform, and the cache-invalidation
side effect.
"""

from __future__ import annotations

import pytest

from web.services import availability_service as svc

pytestmark = pytest.mark.integration


def test_to_fc_event_has_the_fullcalendar_shape() -> None:
    ev = svc.to_fc_event(
        {"id": "s1", "start": "2026-03-04T09:00:00Z", "end": "2026-03-04T10:00:00Z"}
    )
    assert ev["id"] == "s1"
    assert ev["title"] == "✅ Available"
    assert ev["start"] == "2026-03-04T09:00:00Z" and ev["end"] == "2026-03-04T10:00:00Z"
    assert ev["editable"] is False, "availability blocks are not drag-edited as events"
    assert ev["extendedProps"] == {"type": "available", "calendarId": "local"}


def test_to_fc_events_maps_every_slot() -> None:
    slots = [
        {"id": "a", "start": "2026-03-04T09:00:00Z", "end": "2026-03-04T10:00:00Z"},
        {"id": "b", "start": "2026-03-05T11:00:00Z", "end": "2026-03-05T12:00:00Z"},
    ]
    events = svc.to_fc_events(slots)
    assert [e["id"] for e in events] == ["a", "b"]
    assert all(e["extendedProps"]["calendarId"] == "local" for e in events)


def test_add_local_inserts_and_returns_a_fullcalendar_event(fake_redis, make_therapist) -> None:
    t = make_therapist(therapist_id="t1")
    ev = svc.add_local(t["id"], "2026-03-04T09:00:00Z", "2026-03-04T10:00:00Z")
    assert len(ev["id"]) == 32, "a uuid4 hex id"
    assert ev["title"] == "✅ Available"

    listed = svc.list_local(t["id"])
    assert len(listed) == 1 and listed[0]["id"] == ev["id"]
    assert listed[0]["start"] == "2026-03-04T09:00:00Z"


def test_list_local_defaults_to_the_default_therapist(fake_redis) -> None:
    svc.add_local("default", "2026-03-04T09:00:00Z", "2026-03-04T10:00:00Z")
    # None resolves to the "default" therapist key.
    assert len(svc.list_local(None)) == 1


def test_list_local_is_scoped_to_the_therapist(fake_redis, make_therapist) -> None:
    a, b = make_therapist(therapist_id="t_a"), make_therapist(therapist_id="t_b")
    svc.add_local(a["id"], "2026-03-04T09:00:00Z", "2026-03-04T10:00:00Z")
    svc.add_local(b["id"], "2026-03-05T09:00:00Z", "2026-03-05T10:00:00Z")
    assert len(svc.list_local(a["id"])) == 1, "b's slot does not leak into a's list"


def test_remove_local_is_tenant_scoped(fake_redis, make_therapist) -> None:
    a, b = make_therapist(therapist_id="t_a"), make_therapist(therapist_id="t_b")
    ev = svc.add_local(a["id"], "2026-03-04T09:00:00Z", "2026-03-04T10:00:00Z")
    assert svc.remove_local(ev["id"], therapist_id=b["id"]) == 0, "b cannot delete a's slot"
    assert svc.remove_local(ev["id"], therapist_id=a["id"]) == 1
    assert svc.list_local(a["id"]) == []


def test_add_local_invalidates_the_bot_availability_cache(fake_redis, make_therapist) -> None:
    from bot.redis_client import get_sync_redis

    t = make_therapist(therapist_id="t1")
    r = get_sync_redis()
    r.set("zenflow:avail:days:t1:0", "cached")
    r.set("zenflow:avail:hours:t1:2026-03-04", "cached")

    svc.add_local(t["id"], "2026-03-04T09:00:00Z", "2026-03-04T10:00:00Z")

    assert not r.exists("zenflow:avail:days:t1:0"), "adding a slot clears the stale day cache"
    assert not r.exists("zenflow:avail:hours:t1:2026-03-04"), "and the stale hours cache"


def test_add_local_saves_even_when_redis_is_down(make_therapist, monkeypatch) -> None:
    # Cache invalidation is best-effort — a Redis outage must not stop a slot being saved.
    def _boom():
        raise RuntimeError("redis is unreachable")

    monkeypatch.setattr("bot.redis_client.get_sync_redis", _boom)
    t = make_therapist(therapist_id="t1")
    ev = svc.add_local(t["id"], "2026-03-04T09:00:00Z", "2026-03-04T10:00:00Z")
    assert len(ev["id"]) == 32
    assert len(svc.list_local(t["id"])) == 1, "the slot is saved despite the failed cache clear"
