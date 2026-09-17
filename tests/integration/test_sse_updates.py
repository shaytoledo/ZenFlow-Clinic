"""Phase 3.4 — live treatment updates over server-sent events (`ZF_SSE_UPDATES`).

The page used to ask the server every 2 seconds whether anything had changed. With the flag on it
opens one stream instead: the server sends the notes once, then again whenever a status write
publishes a wake-up on Redis (`zenflow:treatment:{appointment_id}`), and closes the stream when
nothing is generating any more. The database stays the single source of truth — the message only
says "look again". With the flag off, or if the stream fails, the page polls as before.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import pytest

from tests.integration import treatment_source
from zenflow import events

pytestmark = pytest.mark.integration

PW = "pw-Test-123"


@pytest.fixture
def sse_on(monkeypatch):
    import zenflow.settings as settings_mod

    monkeypatch.setenv("ZF_SSE_UPDATES", "1")
    settings_mod.reset_settings()
    yield
    settings_mod.reset_settings()


@pytest.fixture
async def session(make_therapist, make_appointment, make_treatment_notes, login_as):
    therapist = make_therapist(email="sse@example.com", password=PW)
    apt = make_appointment(therapist=therapist, summary="Headache")
    make_treatment_notes(apt, ai_suggested_points=[])
    client = await login_as(therapist)
    slug = f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    return {"client": client, "apt": apt, "slug": slug, "therapist": therapist}


def _status(apt_id: int, status: str) -> None:
    from web.repositories.treatment_repo import set_points_status

    set_points_status(apt_id, status)


def _parse(chunk: str) -> tuple[str, Any]:
    event, data = "message", None
    for line in chunk.strip().splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1])
    return event, data


async def _never_disconnected() -> bool:
    return False


# ── the stream ──
async def test_a_status_change_reaches_the_subscriber_within_a_second(session, fake_redis) -> None:
    """Plan 3.4 acceptance: subscribe, push a stage transition, receive it within 1 s."""
    from web.routers.api.treatment import notes_events

    apt_id = session["apt"]["id"]
    _status(apt_id, "GENERATING_STAGE_1")
    stream = notes_events(apt_id, _never_disconnected, recheck_seconds=30)

    event, first = _parse(await asyncio.wait_for(anext(stream), timeout=1))
    assert event == "notes" and first["points_status"] == "GENERATING_STAGE_1"

    nxt = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)  # the generator is now waiting on the channel
    _status(apt_id, "GENERATING_STAGE_2A")
    event, update = _parse(await asyncio.wait_for(nxt, timeout=1))
    assert event == "notes" and update["points_status"] == "GENERATING_STAGE_2A"


async def test_the_stream_ends_when_nothing_is_generating(session, fake_redis) -> None:
    from web.routers.api.treatment import notes_events

    apt_id = session["apt"]["id"]
    _status(apt_id, "GENERATING_STAGE_2B")
    stream = notes_events(apt_id, _never_disconnected, recheck_seconds=30)
    await asyncio.wait_for(anext(stream), timeout=1)

    nxt = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)
    _status(apt_id, "COMPLETED")
    event, data = _parse(await asyncio.wait_for(nxt, timeout=1))
    assert (event, data["points_status"]) == ("notes", "COMPLETED")
    assert _parse(await asyncio.wait_for(anext(stream), timeout=1))[0] == "done"
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)


async def test_a_missed_wakeup_is_caught_by_the_recheck(session, fake_redis, monkeypatch) -> None:
    """A write that never publishes (Redis blip) still shows up within the recheck interval."""
    from web.repositories import treatment_repo
    from web.routers.api.treatment import notes_events

    apt_id = session["apt"]["id"]
    _status(apt_id, "GENERATING_STAGE_1")
    stream = notes_events(apt_id, _never_disconnected, recheck_seconds=0.2)
    await asyncio.wait_for(anext(stream), timeout=1)

    monkeypatch.setattr(treatment_repo, "notify_treatment", lambda _apt: None)
    _status(apt_id, "GENERATING_STAGE_2A")
    event, data = _parse(await asyncio.wait_for(anext(stream), timeout=1))
    assert data["points_status"] == "GENERATING_STAGE_2A"


async def test_the_stream_stops_when_the_page_goes_away(session, fake_redis) -> None:
    from web.routers.api.treatment import notes_events

    apt_id = session["apt"]["id"]
    _status(apt_id, "GENERATING_STAGE_1")

    async def _gone() -> bool:
        return True

    stream = notes_events(apt_id, _gone, recheck_seconds=0.05)
    await asyncio.wait_for(anext(stream), timeout=1)
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(stream), timeout=1)


async def test_a_status_write_survives_a_redis_outage(session, monkeypatch) -> None:
    import bot.redis_client as rc

    class _Down:
        def publish(self, *_: Any) -> None:
            raise ConnectionError("redis down")

    monkeypatch.setattr(rc, "_sync_client", _Down())
    _status(session["apt"]["id"], "GENERATING_STAGE_1")  # must not raise
    events.notify_treatment(session["apt"]["id"])


# ── the endpoint ──
async def test_the_endpoint_is_off_unless_the_flag_is_on(session, fake_redis) -> None:
    resp = await session["client"].get(f"/api/treatment-notes/{session['slug']}/stream")
    assert resp.status_code == 404


async def test_the_endpoint_streams_when_on(session, fake_redis, sse_on) -> None:
    _status(session["apt"]["id"], "COMPLETED")  # finished → the stream closes by itself
    resp = await session["client"].get(f"/api/treatment-notes/{session['slug']}/stream")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events_seen = [_parse(c)[0] for c in resp.text.split("\n\n") if c.strip()]
    assert events_seen == ["notes", "done"]


async def test_another_therapist_cannot_subscribe(
    session, make_therapist, login_as, fake_redis, sse_on
) -> None:
    intruder = await login_as(make_therapist(email="sse-x@example.com", password=PW))
    resp = await intruder.get(f"/api/treatment-notes/{session['slug']}/stream")
    assert resp.status_code == 404


# ── the page ──
async def test_the_page_knows_whether_to_stream(session, fake_redis, monkeypatch) -> None:
    import zenflow.settings as settings_mod

    def config(html: str) -> dict:
        found = re.search(r'id="treatment-config">(.*?)</script>', html)
        assert found is not None
        return json.loads(found.group(1))

    page = f"/treatment/{session['slug']}"
    off = await session["client"].get(page)
    assert config(off.text)["sse_updates"] is False

    monkeypatch.setenv("ZF_SSE_UPDATES", "1")
    settings_mod.reset_settings()
    try:
        on = await session["client"].get(page)
    finally:
        settings_mod.reset_settings()
    assert config(on.text)["sse_updates"] is True


def test_the_page_falls_back_to_polling() -> None:
    html = treatment_source.javascript()
    assert "new EventSource(" in html
    assert "onerror" in html and "setInterval" in html
