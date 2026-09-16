"""Phase 3.3 — "Regenerate points" is a queued job the page follows, and it can be cancelled.

Before: the endpoint ran both AI batches inside the HTTP request while the page *also* polled
the database, so two sources of truth raced to render (a double render, or a button left stuck),
and a slow model simply hung the request. Now the endpoint answers 202 at once and enqueues the
same `points.generate` jobs the intake pipeline uses; the page renders only what the status says.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from bot.services import pipeline_jobs as pj
from tests.conftest import FakeChatModel
from zenflow.queue import get_default_queue
from zenflow.worker import Worker, default_registry

pytestmark = pytest.mark.integration

TEMPLATE = Path(__file__).resolve().parents[2] / "web" / "templates" / "treatment.html"
PW = "pw-Test-123"
OLD_POINTS = [{"code": "HT7", "rationale": "old"}, {"code": "PC6", "rationale": "old"}]
NEW_A = [{"code": "LR3"}, {"code": "GB20"}]
NEW_B = [{"code": "ST36"}, {"code": "KI3"}]


class Batches(FakeChatModel):
    """Batch A, then batch B; `on_call` runs inside the AI call (to cancel mid-flight)."""

    def __init__(self, calls: list[dict[str, Any]], replies: list[str]) -> None:
        super().__init__("", calls, "points")
        self.replies = replies
        self.on_call: Any = None

    async def ainvoke(self, messages: Any, **kw: Any) -> Any:
        done = len([c for c in self.calls if c["model"] == "points"])
        self.reply = self.replies[min(done, len(self.replies) - 1)]
        if self.on_call is not None:
            await self.on_call()
        return await super().ainvoke(messages, **kw)


@pytest.fixture
def points_model(fake_llm, monkeypatch):
    from bot.patient_bot.services import ai_intake

    model = Batches(fake_llm.calls, [json.dumps(NEW_A), json.dumps(NEW_B)])
    monkeypatch.setattr(ai_intake, "_LLM_POINTS", model)
    return model


async def _session(make_therapist, make_appointment, make_treatment_notes, login_as, language="en"):
    therapist = make_therapist(email=f"rg-{language}@example.com", password=PW, language=language)
    apt = make_appointment(therapist=therapist, summary="Headache")
    make_treatment_notes(apt, ai_suggested_points=OLD_POINTS)
    from web.repositories.treatment_repo import set_points_status

    set_points_status(apt["id"], "COMPLETED")
    client = await login_as(therapist)
    slug = f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    return client, apt, f"/api/treatment-notes/{slug}"


@pytest.fixture
async def session(make_therapist, make_appointment, make_treatment_notes, login_as):
    return await _session(make_therapist, make_appointment, make_treatment_notes, login_as)


def _notes(apt_id: int) -> dict[str, Any]:
    from web.repositories import treatment_repo

    notes = treatment_repo.get_by_appointment(apt_id)
    assert notes is not None
    return notes


def _codes(apt_id: int) -> list[str]:
    return [p["code"] for p in (_notes(apt_id).get("ai_suggested_points") or [])]


async def _step() -> int:
    """Run at most one due job."""
    return await Worker(
        get_default_queue(), default_registry, batch=1, handler_timeout=60
    ).run_once()


async def _drain() -> None:
    for _ in range(20):
        if not await _step():
            return
    raise AssertionError("jobs did not settle")


def _pipeline_jobs(apt_id: int) -> list[Any]:
    from bot.db import get_db

    return (
        get_db()
        .execute(
            "SELECT name, status, payload_json FROM jobs "
            "WHERE json_extract(payload_json, '$.appointment_id') = ? ORDER BY id",
            (apt_id,),
        )
        .fetchall()
    )


# ── the endpoint answers at once ──
async def test_regenerate_answers_202_and_generates_nothing_in_the_request(
    session, points_model, fake_redis
) -> None:
    client, apt, base = session
    resp = await client.post(f"{base}/regenerate-points")

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["points_status"] == "GENERATING_STAGE_2A"
    assert body["ai_suggested_points"] == [], "the UI clears the old formula"
    assert points_model.calls == [], "no AI call inside the HTTP request"
    assert _codes(apt["id"]) == [], "old points are cleared straight away"
    assert [j["name"] for j in _pipeline_jobs(apt["id"])] == [pj.POINTS_GENERATE]


async def test_the_status_walks_2a_2b_completed_and_new_points_replace_the_old(
    session, points_model, fake_redis
) -> None:
    client, apt, base = session
    await client.post(f"{base}/regenerate-points")

    seen = [_notes(apt["id"])["points_status"]]
    while await _step():
        seen.append(_notes(apt["id"])["points_status"])

    assert seen == ["GENERATING_STAGE_2A", "GENERATING_STAGE_2B", "COMPLETED"]
    assert _codes(apt["id"]) == ["LR3", "GB20", "ST36", "KI3"]
    assert not set(_codes(apt["id"])) & {"HT7", "PC6"}


async def test_the_therapists_language_reaches_the_job(
    make_therapist, make_appointment, make_treatment_notes, login_as, points_model, fake_redis
) -> None:
    from bot.patient_bot.services.ai_intake import POINT_SELECTION_PROMPT_HE

    client, apt, base = await _session(
        make_therapist, make_appointment, make_treatment_notes, login_as, language="he"
    )
    await client.post(f"{base}/regenerate-points")
    await _drain()

    prompt = str(points_model.calls[0]["messages"][0].content)
    assert prompt.startswith(POINT_SELECTION_PROMPT_HE.strip().splitlines()[0])


async def test_regenerating_twice_is_refused_while_the_first_runs(
    session, points_model, fake_redis
) -> None:
    client, _, base = session
    assert (await client.post(f"{base}/regenerate-points")).status_code == 202
    second = await client.post(f"{base}/regenerate-points")
    assert second.status_code == 409
    assert second.json()["points_status"] == "GENERATING_STAGE_2A"


async def test_regeneration_needs_a_diagnosis(
    make_therapist, make_appointment, make_treatment_notes, login_as, fake_redis
) -> None:
    therapist = make_therapist(email="nodiag@example.com", password=PW)
    apt = make_appointment(therapist=therapist)
    make_treatment_notes(apt, tcm_pattern="")
    client = await login_as(therapist)
    slug = f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    resp = await client.post(f"/api/treatment-notes/{slug}/regenerate-points")
    assert resp.status_code == 422


# ── cancel ──
async def test_cancel_stops_a_queued_run(session, points_model, fake_redis) -> None:
    client, apt, base = session
    await client.post(f"{base}/regenerate-points")

    resp = await client.post(f"{base}/cancel-generation")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"points_status": "CANCELLED", "cancelled_jobs": 1}

    await _drain()
    assert points_model.calls == [], "a cancelled job never reaches the AI"
    assert _notes(apt["id"])["points_status"] == "CANCELLED"


async def test_cancel_during_an_ai_call_discards_the_result_and_ends_the_chain(
    session, points_model, fake_redis
) -> None:
    client, apt, base = session
    await client.post(f"{base}/regenerate-points")

    async def _cancel() -> None:
        points_model.on_call = None
        resp = await client.post(f"{base}/cancel-generation")
        assert resp.status_code == 200

    points_model.on_call = _cancel
    await _drain()

    assert _codes(apt["id"]) == [], "the batch that finished after Cancel is thrown away"
    assert _notes(apt["id"])["points_status"] == "CANCELLED"
    assert [j["name"] for j in _pipeline_jobs(apt["id"])] == [
        pj.POINTS_GENERATE
    ], "batch 2 is never queued"


async def test_cancel_with_nothing_running_is_a_conflict(session, fake_redis) -> None:
    client, _, base = session
    resp = await client.post(f"{base}/cancel-generation")
    assert resp.status_code == 409
    assert resp.json()["points_status"] == "COMPLETED"


async def test_another_therapist_cannot_cancel(
    session, make_therapist, login_as, fake_redis
) -> None:
    client, apt, base = session
    await client.post(f"{base}/regenerate-points")
    intruder = await login_as(make_therapist(email="intruder@example.com", password=PW))
    assert (await intruder.post(f"{base}/cancel-generation")).status_code == 404
    assert _notes(apt["id"])["points_status"] == "GENERATING_STAGE_2A"


# ── a failure is visible, never a silent spinner ──
async def test_a_model_that_returns_nothing_ends_in_failed(
    session, fake_llm, fake_redis, monkeypatch
) -> None:
    from bot.db import get_db
    from bot.patient_bot.services import ai_intake

    monkeypatch.setattr(ai_intake, "_LLM_POINTS", FakeChatModel("[]", fake_llm.calls, "points"))
    client, apt, base = session
    await client.post(f"{base}/regenerate-points")
    for _ in range(2 * pj.MAX_ATTEMPTS):
        get_db().execute("UPDATE jobs SET run_at='2000-01-01T00:00:00Z' WHERE status='pending'")
        await _drain()
    assert _notes(apt["id"])["points_status"] == "FAILED"


# ── the page: one source of truth, and a Cancel button ──
def _function_body(html: str, name: str) -> str:
    start = re.search(rf"^(?:async )?function {name}\(", html, re.MULTILINE)
    assert start is not None, name
    nxt = re.search(r"^(?:async )?function \w+\(", html[start.end() :], re.MULTILINE)
    return html[start.end() : start.end() + nxt.start()] if nxt else html[start.end() :]


def test_the_page_follows_the_status_only_after_the_server_accepted() -> None:
    body = _function_body(TEMPLATE.read_text(encoding="utf-8"), "regeneratePoints")
    assert body.index("regenerate-points") < body.index(
        "_pollForPoints("
    ), "polling starts only after the 202 — no race between the response and the poller"
    assert "renderSuggestedPoints" not in body, "the response body is never rendered directly"


def test_the_page_offers_cancel_and_handles_cancelled() -> None:
    html = TEMPLATE.read_text(encoding="utf-8")
    assert 'onclick="cancelGeneration()"' in html
    assert "cancel-generation" in _function_body(html, "cancelGeneration")
    assert "'CANCELLED'" in _function_body(html, "_pollForPoints")
