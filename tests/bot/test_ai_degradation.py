"""Phase 11.6 (chaos) — graceful degradation when the AI (Ollama/Anthropic) is unavailable.

The intake conversation depends on a local LLM that can time out, error, or be entirely absent. The
patient must never see the flow break: `get_next_question` falls back to the scripted question list
(in the patient's language) and `generate_summary` falls back to a fixed summary. These tests inject
each failure mode at the `ai_calls.ask` seam and assert the fallback text is returned, never an
exception.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from bot.patient_bot.services import ai_intake
from web.services import ai_calls


class _FakeHistory:
    """A minimal stand-in for RedisChatMessageHistory so the test needs no Redis."""

    def __init__(self) -> None:
        self.messages: list = []

    def add_user_message(self, m: str) -> None:
        self.messages.append(HumanMessage(content=m))

    def add_ai_message(self, m: str) -> None:
        self.messages.append(AIMessage(content=m))


@pytest.fixture
def isolated_intake(monkeypatch: pytest.MonkeyPatch) -> _FakeHistory:
    """Route history to memory and disable compression so only the LLM seam matters."""
    hist = _FakeHistory()
    monkeypatch.setattr(ai_intake, "_get_history", lambda _uid: hist)

    async def _no_compress(_uid: int) -> None:
        return None

    monkeypatch.setattr(ai_intake, "_maybe_compress", _no_compress)
    monkeypatch.setattr(ai_intake, "_rolling_summaries", {})
    return hist


async def _ask_raises(exc: BaseException):
    async def _boom(*_a, **_k):
        raise exc

    return _boom


@pytest.mark.parametrize(
    "lang,pool", [("en", "FALLBACK_QUESTIONS"), ("he", "FALLBACK_QUESTIONS_HE")]
)
async def test_next_question_falls_back_on_ai_timeout(
    isolated_intake, monkeypatch: pytest.MonkeyPatch, lang: str, pool: str
) -> None:
    monkeypatch.setattr(ai_intake, "_LLM", object())  # a configured model…
    monkeypatch.setattr(ai_calls, "ask", await _ask_raises(TimeoutError()))  # …that times out

    q = await ai_intake.get_next_question(1, "my lower back aches", lang=lang)
    assert q in getattr(
        ai_intake, pool
    ), "a scripted question in the patient's language is returned"
    assert isolated_intake.messages[-1].content == q, "the fallback is recorded in history"


async def test_next_question_falls_back_on_any_ai_error(
    isolated_intake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_intake, "_LLM", object())
    monkeypatch.setattr(ai_calls, "ask", await _ask_raises(RuntimeError("ollama refused")))
    q = await ai_intake.get_next_question(2, "I feel dizzy", lang="en")
    assert q in ai_intake.FALLBACK_QUESTIONS


async def test_next_question_falls_back_when_no_model_is_configured(
    isolated_intake, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ollama not installed / not configured at all — _LLM is None, ask must never be called.
    monkeypatch.setattr(ai_intake, "_LLM", None)

    async def _must_not_call(*_a, **_k):
        raise AssertionError("ask() must not be called when no model is configured")

    monkeypatch.setattr(ai_calls, "ask", _must_not_call)
    q = await ai_intake.get_next_question(3, "headache", lang="en")
    assert q in ai_intake.FALLBACK_QUESTIONS


async def test_summary_falls_back_on_ai_failure(
    isolated_intake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_intake, "_LLM_LONG", object())
    monkeypatch.setattr(ai_calls, "ask", await _ask_raises(TimeoutError()))
    summary = await ai_intake.generate_summary(4, "that is everything")
    assert summary == ai_intake.FALLBACK_SUMMARY


async def test_summary_falls_back_when_no_model_is_configured(
    isolated_intake, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ai_intake, "_LLM_LONG", None)
    summary = await ai_intake.generate_summary(5, "done")
    assert summary == ai_intake.FALLBACK_SUMMARY
