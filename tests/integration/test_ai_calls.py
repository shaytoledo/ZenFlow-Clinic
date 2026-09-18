"""Plan 8.2 — `ai_calls`: what the AI cost, how long it took, and what it did when it failed.

Every model call the system makes goes through one meter, and leaves one row: the stage it served,
the provider and model, how long it took, whether it worked, and — as hashes, never as text — the
prompt and the answer. "The AI gave a weird diagnosis" becomes a query; a clinical prompt still
never lands in a table.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import pytest

import bot.db as dbmod
from tests.conftest import FakeChatModel

pytestmark = pytest.mark.integration

TG = 930_000_001
PAIN = "my lower back has hurt since Tuesday"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _rows(stage: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ai_calls"
    params: tuple[Any, ...] = ()
    if stage:
        sql += " WHERE stage=?"
        params = (stage,)
    return [dict(r) for r in dbmod.get_db().execute(sql + " ORDER BY id", params)]


class SlowModel(FakeChatModel):
    async def ainvoke(self, messages: Any, **kw: Any) -> Any:
        await asyncio.sleep(5)
        raise AssertionError("the caller should have given up long before this")


class BrokenModel(FakeChatModel):
    async def ainvoke(self, messages: Any, **kw: Any) -> Any:
        raise RuntimeError("connection refused by ollama")


class CountingModel(FakeChatModel):
    """A model that reports its token usage the way LangChain does."""

    async def ainvoke(self, messages: Any, **kw: Any) -> Any:
        message = await super().ainvoke(messages, **kw)
        usage = {"input_tokens": 314, "output_tokens": 42, "total_tokens": 356}
        message.usage_metadata = usage  # type: ignore[attr-defined]
        return message


# ── one row per call ──
async def test_a_call_leaves_one_row_with_what_it_cost(db, fake_llm, fake_redis) -> None:
    from bot.patient_bot.services import ai_intake

    await ai_intake.get_next_question(TG, PAIN)

    (row,) = _rows("intake.question")
    assert row["status"] == "ok" and row["error"] is None
    assert row["provider"] and row["model"], "which backend answered"
    assert isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0
    assert SHA256.match(row["prompt_sha256"]) and SHA256.match(row["response_sha256"])


async def test_the_patients_words_never_reach_the_table(db, fake_llm, fake_redis) -> None:
    """The prompt is evidence that two calls were the same call — not a copy of the conversation."""
    from bot.patient_bot.services import ai_intake

    await ai_intake.get_next_question(TG, PAIN)

    (row,) = _rows()
    assert PAIN not in " ".join(str(v) for v in row.values())
    assert row["prompt_debug"] is None and row["response_debug"] is None


async def test_the_same_prompt_hashes_the_same(db, fake_llm, fake_redis) -> None:
    from bot.patient_bot.services import ai_intake

    await ai_intake.summarize_history([{"role": "user", "content": PAIN}], log_tag="a")
    await ai_intake.summarize_history([{"role": "user", "content": PAIN}], log_tag="b")
    await ai_intake.summarize_history([{"role": "user", "content": "something else"}], log_tag="c")

    first, second, third = (r["prompt_sha256"] for r in _rows("pipeline.summary"))
    assert first == second and first != third


async def test_tokens_are_recorded_when_the_model_reports_them(
    db, fake_llm, fake_redis, monkeypatch
) -> None:
    from bot.patient_bot.services import ai_intake

    monkeypatch.setattr(
        ai_intake, "_LLM", CountingModel("Where does it hurt?", fake_llm.calls, "short")
    )
    await ai_intake.get_next_question(TG, PAIN)

    (row,) = _rows()
    assert (row["prompt_tokens"], row["completion_tokens"]) == (314, 42)


async def test_a_model_that_says_nothing_about_tokens_still_records(
    db, fake_llm, fake_redis
) -> None:
    from bot.patient_bot.services import ai_intake

    await ai_intake.get_next_question(TG, PAIN)
    (row,) = _rows()
    assert (row["prompt_tokens"], row["completion_tokens"]) == (None, None)


# ── failures are the point of the table ──
async def test_a_timeout_is_recorded_and_the_patient_still_gets_a_question(
    db, fake_llm, fake_redis, monkeypatch
) -> None:
    from bot.patient_bot.services import ai_intake

    monkeypatch.setattr(ai_intake, "_LLM", SlowModel("", fake_llm.calls, "short"))
    monkeypatch.setattr(ai_intake, "OLLAMA_TIMEOUT", 0.05)

    question = await ai_intake.get_next_question(TG, PAIN)
    assert question in ai_intake.get_fallback_questions("en")

    (row,) = _rows()
    assert row["status"] == "timeout"
    assert "0.05" in row["error"]
    assert row["response_sha256"] is None
    assert row["duration_ms"] >= 0


async def test_a_broken_backend_is_recorded_with_its_error(
    db, fake_llm, fake_redis, monkeypatch
) -> None:
    from bot.patient_bot.services import ai_intake

    monkeypatch.setattr(ai_intake, "_LLM_LONG", BrokenModel("", fake_llm.calls, "long"))
    summary = await ai_intake.summarize_history([{"role": "user", "content": PAIN}], log_tag="x")
    assert summary == ai_intake.FALLBACK_SUMMARY

    (row,) = _rows()
    assert row["status"] == "error"
    assert "RuntimeError" in row["error"] and "connection refused" in row["error"]
    assert row["response_sha256"] is None


async def test_an_error_carrying_a_secret_is_redacted(
    db, fake_llm, fake_redis, monkeypatch
) -> None:
    from bot.patient_bot.services import ai_intake

    key = "sk-ant-api03-verysecretvalue"

    class Leaky(FakeChatModel):
        async def ainvoke(self, messages: Any, **kw: Any) -> Any:
            raise RuntimeError(f"401 unauthorized for api_key={key}")

    monkeypatch.setattr(ai_intake, "_LLM_LONG", Leaky("", fake_llm.calls, "long"))
    await ai_intake.summarize_history([{"role": "user", "content": PAIN}], log_tag="x")

    (row,) = _rows()
    assert key not in row["error"]


async def test_a_broken_meter_never_breaks_the_ai_call(
    db, fake_llm, fake_redis, monkeypatch
) -> None:
    """Losing a metric must not lose the patient's question."""
    import sqlite3

    from bot.patient_bot.services import ai_intake
    from tests.conftest import CANNED_QUESTION
    from web.services import ai_calls

    def _boom(*a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(ai_calls, "_insert", _boom)
    assert await ai_intake.get_next_question(TG, PAIN) == CANNED_QUESTION
    assert _rows() == []


# ── the appointment a call belongs to ──
async def test_a_pipeline_call_names_its_appointment(
    db, fake_redis, make_appointment, make_patient
) -> None:
    from web.services import ai_calls

    apt = make_appointment(patient=make_patient("Dana"))
    with ai_calls.for_appointment(apt["id"]):
        ai_calls.record("pipeline.summary", provider="ollama", model="gemma3", duration_ms=12)
    ai_calls.record("intake.question", provider="ollama", model="gemma3", duration_ms=3)

    first, second = _rows()
    assert first["appointment_id"] == apt["id"]
    assert second["appointment_id"] is None


def test_a_generation_is_the_ais_work_on_one_appointment(db, fake_redis) -> None:
    """The pipeline's own block says both things at once: which appointment, and who is acting."""
    from bot.services import pipeline_jobs
    from web.services import ai_calls, audit

    with pipeline_jobs._exclusive(42):
        assert ai_calls.current_appointment() == 42
        assert audit.current_actor().actor_type == "ai"

    assert ai_calls.current_appointment() is None
    assert audit.current_actor().actor_type == "system"


# ── prompts in the clear: dev only, behind a flag ──
async def test_prompts_are_kept_only_when_dev_asks_for_them(
    db, fake_llm, fake_redis, monkeypatch
) -> None:
    from bot.patient_bot.services import ai_intake
    from zenflow.settings import get_settings, reset_settings

    monkeypatch.setenv("ZF_AI_DEBUG_PROMPTS", "1")
    monkeypatch.setenv("ENV", "dev")
    reset_settings()
    await ai_intake.get_next_question(TG, PAIN)

    (row,) = _rows()
    assert PAIN in row["prompt_debug"]
    assert row["response_debug"]

    # …and the same flag on a server changes nothing at all.
    monkeypatch.setenv("ENV", "staging")
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "staging-token-key-0123456789abcdef0123456789abcdef")
    reset_settings()
    assert get_settings().env == "staging", "the settings really are a server's"

    await ai_intake.get_next_question(TG, "and my neck too")
    kept = _rows()[-1]
    assert kept["prompt_debug"] is None and kept["response_debug"] is None
    assert kept["prompt_sha256"], "the hash is kept everywhere; only the text is not"


async def test_the_debug_copies_can_be_forgotten(db, fake_llm, fake_redis, monkeypatch) -> None:
    from web.services import ai_calls
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_AI_DEBUG_PROMPTS", "1")
    monkeypatch.setenv("ENV", "dev")
    reset_settings()
    ai_calls.record("intake.question", duration_ms=1, prompt=PAIN, response="ok")
    assert _rows()[0]["prompt_debug"] == PAIN

    assert ai_calls.forget_prompts(older_than_days=0) == 1
    (row,) = _rows()
    assert row["prompt_debug"] is None and row["prompt_sha256"], "the hash outlives the text"


# ── what 8.4 will read ──
def test_the_summary_answers_cost_latency_and_failure_rate(db) -> None:
    from web.services import ai_calls

    for ms in (10, 20, 30, 40, 50, 60, 70, 80, 90, 100):
        ai_calls.record(
            "pipeline.points",
            provider="ollama",
            model="gemma3",
            duration_ms=ms,
            prompt_tokens=100,
            completion_tokens=20,
        )
    ai_calls.record("pipeline.points", duration_ms=500, status="timeout", error="timed out")
    ai_calls.record("intake.question", duration_ms=5)

    got = ai_calls.summary(hours=24)
    assert got["calls"] == 12 and got["failures"] == 1
    assert got["failure_rate"] == pytest.approx(1 / 12, rel=1e-3)
    assert got["p50_ms"] == 50 and got["p95_ms"] == 500
    assert got["prompt_tokens"] == 1000 and got["completion_tokens"] == 200
    points = got["stages"]["pipeline.points"]
    assert points["calls"] == 11 and points["failures"] == 1 and points["p95_ms"] == 500


def test_the_summary_of_a_quiet_clinic_is_zeroes(db) -> None:
    from web.services import ai_calls

    got = ai_calls.summary(hours=24)
    assert got["calls"] == 0 and got["failures"] == 0 and got["failure_rate"] == 0.0
    assert got["p50_ms"] == 0 and got["p95_ms"] == 0 and got["stages"] == {}


def test_the_history_of_one_appointment_reads_newest_first(db) -> None:
    from web.services import ai_calls

    with ai_calls.for_appointment(7):
        ai_calls.record("pipeline.summary", duration_ms=1)
        ai_calls.record("pipeline.diagnosis", duration_ms=2)
    ai_calls.record("intake.question", duration_ms=3)

    assert [r["stage"] for r in ai_calls.history(7)] == [
        "pipeline.diagnosis",
        "pipeline.summary",
    ]


# ── the rule, checked against the code ──
def test_every_model_call_goes_through_the_meter() -> None:
    """A second `ainvoke` somewhere else is a call nobody can cost, time or explain."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (*root.glob("bot/**/*.py"), *root.glob("web/**/*.py"), *root.glob("zenflow/*.py")):
        if path.name == "ai_calls.py":
            continue
        if ".ainvoke(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(root)))
    assert offenders == [], f"these call a model without metering it: {offenders}"


async def test_the_checkin_summary_is_metered(db, fake_llm, fake_redis) -> None:
    from bot.services.followup_scheduler import summarize_checkin

    await summarize_checkin({"pain_level": 3, "improvement": "better"}, "en")

    (row,) = _rows("followup.summary")
    assert row["status"] == "ok" and row["model"]
