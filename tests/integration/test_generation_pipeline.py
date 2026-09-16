"""Phase 3.1 — AI generation runs once, server-side, as durable jobs right after the intake.

Before: the last intake answer fired `asyncio.ensure_future(_summary_and_tcm(...))`. A bot
restart lost it, nothing retried it, and the pipeline read the conversation from a Redis key with
a 30-minute TTL. Now the conversation is written to the database with the appointment, and four
queued jobs take it from there:

    intake.finalize → diagnosis.generate → points.generate(batch 1) → points.generate(batch 2)

Acceptance (plan 3.1): finish an intake against the fake LLM and every DB write lands without the
web app being involved at all.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from bot.patient_bot import schedule
from bot.services import pipeline_jobs as pj
from tests.bot.conftest import make_context, make_update
from tests.conftest import CANNED_DIAGNOSIS, CANNED_POINTS, CANNED_SUMMARY, FakeChatModel
from zenflow import leases
from zenflow.queue import get_default_queue
from zenflow.worker import Worker, default_registry

pytestmark = pytest.mark.integration

PATIENT = 920_000_001
DAY = date(2026, 3, 12)
SECOND_BATCH = [
    {"code": "ST36", "name": "Zusanli", "reason": "Tonifies Qi and Blood"},
    {"code": "PC6", "name": "Neiguan", "reason": "Calms the Shen, aids sleep"},
]


class SequenceModel(FakeChatModel):
    """Returns the queued replies in order (then repeats the last); a reply may be an exception."""

    def __init__(self, replies: list[Any], calls: list[dict[str, Any]], role: str) -> None:
        super().__init__("", calls, role)
        self.replies = list(replies)

    async def ainvoke(self, messages: Any, **kw: Any) -> Any:
        self.reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(self.reply, BaseException):
            self.calls.append({"model": self.role, "messages": messages})
            raise self.reply
        return await super().ainvoke(messages, **kw)


@pytest.fixture
def llm(fake_llm, monkeypatch):
    """Summary, then diagnosis on the long model; two different point batches."""
    from bot.patient_bot.services import ai_intake

    long_model = SequenceModel(
        [CANNED_SUMMARY, json.dumps(CANNED_DIAGNOSIS)], fake_llm.calls, "long"
    )
    points_model = SequenceModel(
        [json.dumps(CANNED_POINTS), json.dumps(SECOND_BATCH)], fake_llm.calls, "points"
    )
    monkeypatch.setattr(ai_intake, "_LLM_LONG", long_model)
    monkeypatch.setattr(ai_intake, "_LLM_POINTS", points_model)
    fake_llm.long, fake_llm.points = long_model, points_model
    return fake_llm


@pytest.fixture
def therapist(db, make_therapist):
    from bot import config as botcfg

    t = make_therapist(name="Dr Pipe", therapist_id="t1", telegram_id=730_001)
    botcfg.reload_therapists()
    return t


@pytest.fixture(autouse=True)
def no_calendar(monkeypatch):
    async def _book(*a: Any, **k: Any) -> str:
        return "gcal-pipeline"

    monkeypatch.setattr(schedule, "book_slot", _book)


async def _finish_intake(therapist: dict[str, Any]) -> int:
    """Answers 1-4 go through the normal handler, answer 5 completes the booking."""
    from bot.patient_bot.services.ai_intake import initialize_intake

    initialize_intake(PATIENT, "What brings you in today?")
    user_data: dict[str, Any] = {
        "selected_therapist": therapist["id"],
        "selected_day": DAY.isoformat(),
        "selected_time": "10:00",
        "intake_count": 0,
    }
    answers = ["Headache", "Three days", "Right side", "Worse with stress", "Sleep is poor"]
    for answer in answers:
        await schedule.handle_intake_answer(
            make_update(answer, user_id=PATIENT), make_context(user_data)
        )
    from bot.db import get_db

    row = (
        get_db()
        .execute("SELECT id FROM appointments WHERE patient_id=? AND status='active'", (PATIENT,))
        .fetchone()
    )
    assert row is not None
    return int(row["id"])


async def _drain(max_rounds: int = 20) -> None:
    worker = Worker(get_default_queue(), default_registry, handler_timeout=60)
    for _ in range(max_rounds):
        if not await worker.run_once():
            return
    raise AssertionError("the pipeline did not settle")


def _jobs_db() -> Any:
    from bot.db import get_db

    return get_db()


def _notes(apt_id: int) -> dict[str, Any]:
    from web.repositories import treatment_repo

    notes = treatment_repo.get_by_appointment(apt_id)
    assert notes is not None
    return notes


# ── acceptance ──
async def test_an_intake_produces_summary_diagnosis_and_both_point_batches(
    llm, therapist, fake_redis
) -> None:
    apt_id = await _finish_intake(therapist)
    assert not any(
        c["model"] in ("long", "points") for c in llm.calls
    ), "nothing is generated inside the Telegram handler"
    assert _notes(apt_id)["points_status"] == "GENERATING_STAGE_0", "queued, visibly"

    await _drain()

    from bot.db import get_db

    apt = get_db().execute("SELECT summary FROM appointments WHERE id=?", (apt_id,)).fetchone()
    assert apt["summary"] == CANNED_SUMMARY
    notes = _notes(apt_id)
    assert notes["tcm_pattern"] == CANNED_DIAGNOSIS["tcm_pattern"]
    codes = [p["code"] for p in notes["ai_suggested_points"]]
    assert codes == [p["code"] for p in CANNED_POINTS] + ["ST36", "PC6"]
    assert notes["points_status"] == "COMPLETED"


async def test_the_conversation_is_in_the_database_before_any_job_runs(
    llm, therapist, fake_redis
) -> None:
    """A restart can take the Redis intake history with it; the jobs must not need it."""
    from bot.db import get_db
    from bot.patient_bot.services import ai_intake

    apt_id = await _finish_intake(therapist)
    row = (
        get_db()
        .execute("SELECT history_json FROM intake_sessions WHERE appointment_id=?", (apt_id,))
        .fetchone()
    )
    history = json.loads(row["history_json"])
    assert history[0] == {"role": "assistant", "content": "What brings you in today?"}
    assert history[-1] == {"role": "user", "content": "Sleep is poor"}

    ai_intake._get_history(PATIENT).clear()  # the Redis history is gone
    await _drain()
    assert _notes(apt_id)["points_status"] == "COMPLETED"
    long_calls = [c for c in llm.calls if c["model"] == "long"]
    transcript = " ".join(str(getattr(m, "content", m)) for m in long_calls[0]["messages"])
    assert "Sleep is poor" in transcript, "the summary was built from the stored conversation"


async def test_each_stage_is_idempotent(llm, therapist, fake_redis) -> None:
    apt_id = await _finish_intake(therapist)
    await _drain()
    calls_after_first_run = len(llm.calls)

    payload = {"appointment_id": apt_id, "patient_id": PATIENT, "run": "intake"}
    await pj.finalize_intake(payload)
    await pj.generate_diagnosis(payload)
    await pj.generate_points({**payload, "batch": 1})
    await pj.generate_points({**payload, "batch": 2})

    assert len(llm.calls) == calls_after_first_run, "a replayed job does no generation"
    assert len(_notes(apt_id)["ai_suggested_points"]) == len(CANNED_POINTS) + len(SECOND_BATCH)


async def test_a_failed_llm_call_is_retried_not_lost(
    llm, therapist, fake_redis, monkeypatch
) -> None:
    from bot.patient_bot.services import ai_intake

    flaky = SequenceModel(
        [TimeoutError("ollama timeout"), CANNED_SUMMARY, json.dumps(CANNED_DIAGNOSIS)],
        llm.calls,
        "long",
    )
    monkeypatch.setattr(ai_intake, "_LLM_LONG", flaky)
    apt_id = await _finish_intake(therapist)

    await _drain()
    job = (
        _jobs_db()
        .execute(
            "SELECT status, attempts, last_error FROM jobs WHERE name=?", (pj.INTAKE_FINALIZE,)
        )
        .fetchone()
    )
    assert job["status"] == "pending", "the failed attempt is scheduled for a retry"
    assert job["attempts"] == 1 and "summary" in job["last_error"].lower()

    _jobs_db().execute("UPDATE jobs SET run_at='2000-01-01T00:00:00Z' WHERE status='pending'")
    await _drain()
    assert _notes(apt_id)["points_status"] == "COMPLETED"


async def test_an_exhausted_stage_marks_the_session_failed(
    llm, therapist, fake_redis, monkeypatch
) -> None:
    """The therapist must see a failure, not a spinner that never ends."""
    from bot.patient_bot.services import ai_intake

    monkeypatch.setattr(
        ai_intake, "_LLM_LONG", SequenceModel([TimeoutError("down")], llm.calls, "long")
    )
    apt_id = await _finish_intake(therapist)
    conn = _jobs_db()
    # Stage 0 degrades to a placeholder summary on its last attempt; Stage 1 then exhausts too.
    for _ in range(2 * pj.MAX_ATTEMPTS):
        conn.execute("UPDATE jobs SET run_at='2000-01-01T00:00:00Z' WHERE status='pending'")
        await _drain()

    assert _notes(apt_id)["points_status"] == "FAILED"
    from bot.db import get_db
    from bot.patient_bot.services.ai_intake import FALLBACK_SUMMARY

    apt = get_db().execute("SELECT summary FROM appointments WHERE id=?", (apt_id,)).fetchone()
    assert apt["summary"] == FALLBACK_SUMMARY, "a missing summary alone does not stop the pipeline"


async def test_two_generations_never_overlap(llm, therapist, fake_redis) -> None:
    apt_id = await _finish_intake(therapist)
    assert leases.acquire(pj.lock_name(apt_id), "someone-else", ttl_seconds=600)

    payload = {"appointment_id": apt_id, "patient_id": PATIENT, "run": "intake"}
    with pytest.raises(pj.PipelineBusy):
        await pj.finalize_intake(payload)
    assert not any(c["model"] == "long" for c in llm.calls)

    leases.release(pj.lock_name(apt_id), "someone-else")
    await pj.finalize_intake(payload)
    assert any(c["model"] == "long" for c in llm.calls)


def test_the_pipeline_handlers_are_registered() -> None:
    from zenflow.worker import load_default_handlers

    load_default_handlers()
    for name in (pj.INTAKE_FINALIZE, pj.DIAGNOSIS_GENERATE, pj.POINTS_GENERATE):
        assert default_registry.get(name) is not None
        assert default_registry.dead_hook(name) is not None


def test_the_handler_no_longer_fires_and_forgets() -> None:
    import inspect

    assert "ensure_future" not in inspect.getsource(schedule)
