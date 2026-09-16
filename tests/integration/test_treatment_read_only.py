"""Phase 3.2 — the treatment page never starts a generation on its own.

Before: opening a session with no saved points made the page fire `rediagnose` and then
`generate-points` from JavaScript on load, even while the bot's pipeline was still working on
the same session. Now generation runs only on an explicit click, and the server refuses a second
generation while one is in progress (409 with the current status; `force=true` overrides a status
left behind by a crash, never a running job).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from bot.services import pipeline_jobs as pj
from zenflow import leases

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).resolve().parents[2] / "web" / "templates" / "treatment.html"
GENERATING_ENDPOINTS = ("rediagnose", "generate-points", "regenerate-points")
PW = "pw-Test-123"


@pytest.fixture
async def session(make_therapist, make_appointment, make_treatment_notes, login_as):
    """A signed-in therapist with an intake-backed appointment and a saved diagnosis."""
    therapist = make_therapist(email="ro@example.com", password=PW)
    apt = make_appointment(
        therapist=therapist,
        summary="Headache for three days",
        intake_history=[{"role": "user", "content": "My head hurts"}],
    )
    make_treatment_notes(apt, ai_suggested_points=[])
    client = await login_as(therapist)
    slug = f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    return {
        "client": client,
        "apt": apt,
        "base": f"/api/treatment-notes/{slug}",
        "page": f"/treatment/{slug}",
    }


def _set_status(apt_id: int, status: str) -> None:
    from web.repositories.treatment_repo import set_points_status

    set_points_status(apt_id, status)


def _job_count() -> int:
    from bot.db import get_db

    return int(get_db().execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"])


async def _call(client: Any, base: str, endpoint: str, force: bool = False) -> Any:
    url = f"{base}/{endpoint}" + ("?force=true" if force else "")
    body = {"tongue_observation": "", "pulse_observation": ""} if endpoint == "rediagnose" else None
    return await client.post(url, json=body)


# ── opening the page generates nothing ──
async def test_opening_the_session_twice_enqueues_and_generates_nothing(
    session, fake_llm, fake_redis
) -> None:
    client = session["client"]
    for _ in range(2):
        assert (await client.get(session["page"])).status_code == 200
        assert (await client.get(session["base"])).status_code == 200
    assert _job_count() == 0
    assert fake_llm.calls == []


def test_the_page_script_only_generates_from_an_explicit_click() -> None:
    """Every function that calls a generating endpoint runs only on a click or an Enter key."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "_autoLoadDiagnosis" not in html

    starts = list(re.finditer(r"^(?:async )?function (\w+)\(", html, re.MULTILINE))
    callers: set[str] = set()
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(html)
        body = html[match.end() : end]
        if any(f"/{ep}" in body for ep in GENERATING_ENDPOINTS):
            callers.add(match.group(1))
    assert callers == {"generateDiagnosisAndPoints", "triggerRediagnosis", "regeneratePoints"}

    for name in callers:
        for call in re.finditer(rf"\b{name}\(", html):
            line_start = html.rfind("\n", 0, call.start()) + 1
            line = html[line_start : html.find("\n", call.start())]
            if f"function {name}(" in line:
                continue
            explicit = (
                "onclick=" in line
                or "addEventListener('click'" in line
                or ("addEventListener('keydown'" in line and "'Enter'" in line)
            )
            assert explicit, f"{name}() is called outside a user action: {line.strip()}"


# ── the server refuses to overlap ──
@pytest.mark.parametrize("endpoint", GENERATING_ENDPOINTS)
async def test_a_running_generation_is_refused_with_its_status(
    session, fake_llm, fake_redis, endpoint: str
) -> None:
    _set_status(session["apt"]["id"], "GENERATING_STAGE_1")
    resp = await _call(session["client"], session["base"], endpoint)
    assert resp.status_code == 409
    assert resp.json()["points_status"] == "GENERATING_STAGE_1"
    assert fake_llm.calls == [], "no AI call is made for a refused request"


@pytest.mark.parametrize("endpoint", GENERATING_ENDPOINTS)
async def test_force_overrides_a_stale_status(session, fake_llm, fake_redis, endpoint: str) -> None:
    """A status a crashed run left behind must not lock the therapist out."""
    _set_status(session["apt"]["id"], "GENERATING")
    resp = await _call(session["client"], session["base"], endpoint, force=True)
    if endpoint == "regenerate-points":  # queued since Phase 3.3
        assert resp.status_code == 202, resp.text
        assert _job_count() == 1, "the forced run was queued"
    else:
        assert resp.status_code == 200, resp.text
        assert fake_llm.calls, "the forced run did generate"


@pytest.mark.parametrize("endpoint", GENERATING_ENDPOINTS)
async def test_force_never_overrides_a_live_job(
    session, fake_llm, fake_redis, endpoint: str
) -> None:
    apt_id = session["apt"]["id"]
    assert leases.acquire(pj.lock_name(apt_id), "job-99", ttl_seconds=600)
    resp = await _call(session["client"], session["base"], endpoint, force=True)
    assert resp.status_code == 409
    assert fake_llm.calls == []


async def test_an_idle_session_can_be_generated_explicitly(session, fake_llm, fake_redis) -> None:
    client, base = session["client"], session["base"]
    assert (await _call(client, base, "rediagnose")).status_code == 200
    resp = await _call(client, base, "generate-points")
    assert resp.status_code == 200
    assert resp.json()["points_status"] == "COMPLETED"
    assert leases.acquire(
        pj.lock_name(session["apt"]["id"]), "next", ttl_seconds=1
    ), "the web request released the lease when it finished"
