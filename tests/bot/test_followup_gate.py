"""Phase 11.3 — the 24h follow-up gate (BOT_AUDIT B3).

`handle_followup_reply` / `handle_followup_button` run in a lower handler group than the
ConversationHandler, so they see every message/button first. When the update belongs to an open
check-in they answer it and raise `ApplicationHandlerStop` (keeping it away from the conversation
without changing the patient's state); otherwise they do nothing and let it through. These tests
drive the gate with the `followup_scheduler` consume calls stubbed, asserting exactly that behaviour.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from telegram.ext import ApplicationHandlerStop

from bot.patient_bot import followup as fu

UID = 900_700_001


class _Msg:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.replies: list[dict[str, Any]] = []

    async def reply_text(self, text: str, **kw: Any) -> None:
        self.replies.append({"text": text, **kw})


class _Query:
    def __init__(self, data: str, message: _Msg | None = None) -> None:
        self.data = data
        self.message = message
        self.answered: Any = "unset"
        self.markup_edits: list[Any] = []

    async def answer(self, toast: Any = None) -> None:
        self.answered = toast

    async def edit_message_reply_markup(self, reply_markup: Any = None) -> None:
        self.markup_edits.append(reply_markup)


def _upd(message: Any = None, query: Any = None) -> Any:
    return SimpleNamespace(
        message=message, callback_query=query, effective_user=SimpleNamespace(id=UID)
    )


def _ctx() -> Any:
    return SimpleNamespace(user_data={})


def _stub_reply(monkeypatch: pytest.MonkeyPatch, result: tuple[bool, Any]) -> None:
    async def _consume(_uid: int, _text: str) -> tuple[bool, Any]:
        return result

    monkeypatch.setattr("bot.services.followup_scheduler.consume_followup_conversation", _consume)


def _stub_button(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def _consume(_uid: int, _data: str) -> Any:
        return result

    monkeypatch.setattr("bot.services.followup_scheduler.consume_followup_button", _consume)


# ── typed replies ────────────────────────────────────────────────────────────────


async def test_a_non_checkin_message_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_reply(monkeypatch, (False, None))
    msg = _Msg("just chatting")
    await fu.handle_followup_reply(_upd(message=msg), _ctx())  # returns, no ApplicationHandlerStop
    assert msg.replies == []


async def test_an_empty_or_missing_message_is_ignored() -> None:
    await fu.handle_followup_reply(_upd(message=_Msg("")), _ctx())  # no text → early return
    await fu.handle_followup_reply(_upd(message=None), _ctx())  # no message at all


async def test_a_consumed_reply_with_a_next_prompt_sends_it_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = SimpleNamespace(text="On a scale of 1–10, how is the pain?", buttons=None)
    _stub_reply(monkeypatch, (True, prompt))
    msg = _Msg("much better")
    with pytest.raises(ApplicationHandlerStop):
        await fu.handle_followup_reply(_upd(message=msg), _ctx())
    assert msg.replies and "scale" in msg.replies[0]["text"]


async def test_a_consumed_final_reply_stops_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_reply(monkeypatch, (True, None))
    msg = _Msg("all good, thanks")
    with pytest.raises(ApplicationHandlerStop):
        await fu.handle_followup_reply(_upd(message=msg), _ctx())
    assert msg.replies == [], "a final answer is consumed silently"


# ── tapped buttons ─────────────────────────────────────────────────────────────


async def test_a_non_followup_button_passes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    q = _Query("main_menu")  # not an 'fu:' callback
    await fu.handle_followup_button(_upd(query=q), _ctx())
    assert q.answered == "unset", "a foreign button is left for the conversation handler"


async def test_a_consumed_button_answers_removes_its_buttons_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = SimpleNamespace(
        consumed=True, toast="Recorded", keep_buttons=None, remove_buttons=True, prompt=None
    )
    _stub_button(monkeypatch, result)
    q = _Query("fu:12:1:7", message=_Msg("prev"))
    with pytest.raises(ApplicationHandlerStop):
        await fu.handle_followup_button(_upd(query=q), _ctx())
    assert q.answered == "Recorded"
    assert q.markup_edits == [None], "an answered question loses its buttons"


async def test_a_consumed_button_sends_the_next_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = SimpleNamespace(text="Any new symptoms?", buttons=[[("Yes", "fu:12:2:y")]])
    result = SimpleNamespace(
        consumed=True, toast=None, keep_buttons=None, remove_buttons=False, prompt=prompt
    )
    _stub_button(monkeypatch, result)
    msg = _Msg("prev")
    q = _Query("fu:12:1:7", message=msg)
    with pytest.raises(ApplicationHandlerStop):
        await fu.handle_followup_button(_upd(query=q), _ctx())
    assert msg.replies and "symptoms" in msg.replies[0]["text"]


async def test_a_button_the_scheduler_does_not_consume_passes_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = SimpleNamespace(
        consumed=False, toast=None, keep_buttons=None, remove_buttons=False, prompt=None
    )
    _stub_button(monkeypatch, result)
    q = _Query("fu:99:1:x")
    await fu.handle_followup_button(_upd(query=q), _ctx())  # returns, no ApplicationHandlerStop
    assert q.answered == "unset"
