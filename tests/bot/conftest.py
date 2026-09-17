"""Lightweight fakes for Telegram handlers (Phase 2.2).

Handlers only touch a small surface of the PTB objects (`update.effective_user`,
`update.message.text/reply_text`, `context.user_data`, `bot.send_message`), so plain stand-ins
keep the tests readable and fast. The full ConversationHandler state machine is exercised in
Phase 11.3.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest


class FakeBot:
    """Records outbound Telegram calls instead of performing them."""

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.fail_with = fail_with
        self._next_id = 500

    async def send_message(self, chat_id: int, text: str, **kw: Any) -> Any:
        if self.fail_with is not None:
            raise self.fail_with
        self._next_id += 1
        self.sent.append({"chat_id": chat_id, "text": text, **kw})
        return SimpleNamespace(message_id=self._next_id)

    # what the handlers assert on
    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]


class FakeMessage:
    def __init__(
        self,
        text: str | None = "hello",
        *,
        message_id: int = 1,
        user_id: int = 900_000_001,
        full_name: str = "Test Patient",
        reply_to_message: Any = None,
        photo: Any = None,
        voice: Any = None,
        document: Any = None,
    ) -> None:
        self.text = text
        self.message_id = message_id
        self.from_user = SimpleNamespace(
            id=user_id, full_name=full_name, first_name=full_name.split()[0]
        )
        self.reply_to_message = reply_to_message
        self.photo = photo
        self.voice = voice
        self.document = document
        self.replies: list[dict[str, Any]] = []

    async def reply_text(self, text: str, **kw: Any) -> None:
        self.replies.append({"text": text, **kw})

    def reply_texts(self) -> list[str]:
        return [r["text"] for r in self.replies]


class FakeQuery:
    def __init__(self, data: str = "", user_id: int = 900_000_001) -> None:
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, full_name="Test Patient", first_name="Test")
        self.answered = False
        self.edits: list[dict[str, Any]] = []

    async def answer(self, *a: Any, **k: Any) -> None:
        self.answered = True

    async def edit_message_text(self, text: str, **kw: Any) -> None:
        self.edits.append({"text": text, **kw})


def make_update(
    text: str | None = "hello",
    *,
    user_id: int = 900_000_001,
    full_name: str = "Test Patient",
    query: FakeQuery | None = None,
    **message_kw: Any,
) -> Any:
    message = (
        None
        if query is not None
        else FakeMessage(text, user_id=user_id, full_name=full_name, **message_kw)
    )
    return SimpleNamespace(
        message=message,
        callback_query=query,
        effective_user=SimpleNamespace(
            id=user_id, full_name=full_name, first_name=full_name.split()[0]
        ),
        effective_chat=SimpleNamespace(id=user_id),
    )


def make_context(user_data: dict[str, Any] | None = None, bot: FakeBot | None = None) -> Any:
    return SimpleNamespace(
        user_data=user_data if user_data is not None else {}, bot=bot or FakeBot()
    )


@pytest.fixture
def fake_bot() -> FakeBot:
    return FakeBot()


@pytest.fixture
def therapist_bot(monkeypatch: pytest.MonkeyPatch, fake_bot: FakeBot) -> FakeBot:
    """The Bot instance the patient bot uses to forward messages to the therapist."""
    import bot.patient_bot.therapist as pt
    from bot.interfaces import TelegramChannel

    monkeypatch.setattr(pt, "_therapist_channel", TelegramChannel(bot=fake_bot))
    return fake_bot


@pytest.fixture
def patient_bot(monkeypatch: pytest.MonkeyPatch) -> FakeBot:
    """The Bot instance the therapist bot uses to deliver replies to patients."""
    import bot.therapist_bot.handlers as th
    from bot.interfaces import TelegramChannel

    bot_ = FakeBot()
    monkeypatch.setattr(th, "_patient_channel", TelegramChannel(bot=bot_))
    return bot_
