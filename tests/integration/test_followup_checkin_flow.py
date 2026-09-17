"""Phase 6.2 — the extended 24h check-in, end to end: buttons, typed fallbacks, red flags, summary."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from bot.services import followup_checkin as fc
from web.repositories import followup_repo, treatment_repo
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"
PID = 900_000_701


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _alerts(kind: str) -> list[dict[str, Any]]:
    return [
        dict(r) for r in dbmod.get_db().execute("SELECT * FROM notifications WHERE kind=?", (kind,))
    ]


@pytest.fixture
async def checkin(authenticated_client, make_appointment, make_treatment_notes, fake_telegram):
    """A completed session whose step 1 has just gone out (T+24h)."""
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = {"patient_id": PID, "name": "Dana Levi", "source": "telegram"}
    apt = make_appointment(
        therapist={"id": tid}, patient=patient, apt_date="2026-03-01", apt_time="10:00"
    )
    make_treatment_notes(apt)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:  # held for the whole test
        url = f"/api/treatment-notes/{PID}/2026-03-01/10-00/complete"
        assert (await authenticated_client.post(url, json={})).status_code == 200
        frozen.tick(timedelta(hours=24))
        await _worker().run_once()
        frozen.tick(timedelta(minutes=10))
        yield apt, fake_telegram


async def _tap(step: int, value: str, apt_id: int) -> Any:
    from bot.services.followup_scheduler import consume_followup_button

    return await consume_followup_button(PID, fc.callback(apt_id, step, value))


async def _type(text: str) -> Any:
    from bot.services.followup_scheduler import consume_followup_conversation

    consumed, prompt = await consume_followup_conversation(PID, text)
    assert consumed
    return prompt


async def test_step1_goes_out_with_buttons(checkin) -> None:
    apt, telegram = checkin
    (call,) = (c for c in telegram.calls if "0–10" in c["text"])
    keyboard = call["reply_markup"]["inline_keyboard"]
    assert [b["text"] for b in keyboard[0]] == ["0", "1", "2", "3", "4", "5"]
    assert keyboard[1][-1]["callback_data"] == f"fu:{apt['id']}:1:10"
    assert call["text"].startswith("🌿 Hi Dana!")


async def test_a_checkin_answered_with_buttons(checkin) -> None:
    apt, _ = checkin
    apt_id = apt["id"]
    result = await _tap(1, "3", apt_id)
    assert result.consumed and result.keep_buttons is None
    assert "changed" in result.prompt.text and len(result.prompt.buttons) == 5
    await _tap(2, "5", apt_id)

    toggled = await _tap(3, "t:soreness", apt_id)
    assert toggled.prompt is None, "a toggle only redraws the buttons"
    assert "✓ Soreness" in [label for row in toggled.keep_buttons for label, _ in row]
    await _tap(3, "t:fatigue", apt_id)
    await _tap(3, "t:fatigue", apt_id)  # changed their mind
    sleep = await _tap(3, "done", apt_id)
    assert "sleep" in sleep.prompt.text

    stale = await _tap(2, "1", apt_id)  # an old message's button
    assert stale.consumed and stale.toast == "That question was already answered."
    other = await _tap(4, "better", apt_id + 1)
    assert other.toast == "That question was already answered."

    await _tap(4, "skip", apt_id)
    await _tap(5, "partly", apt_id)
    done = await _tap(6, "skip", apt_id)
    assert "Thank you" in done.prompt.text and done.prompt.buttons == []

    row = followup_repo.get(apt_id)
    assert row is not None and row["status"] == "completed"
    assert (row["pain_level"], row["improvement_rating"], row["side_effects"]) == (
        3,
        5,
        ["soreness"],
    )
    assert (row["sleep_quality"], row["adherence"], row["free_text"]) == (None, "partly", None)
    assert not row["needs_attention"]
    users = [m["content"] for m in row["conversation"] if m["role"] == "user"]
    assert users == ["3", "5 — Much better", "Soreness", "Skip", "Partly", "Skip"]
    assert row["ai_summary"] == (
        "Pain 3/10 · Much better (5/5) · advice followed: Partly\n"
        "side effects: Soreness · no note"
    ), "no AI in tests: the fixed wording"
    notes = treatment_repo.get_by_appointment(apt_id)
    assert notes is not None
    saved = notes["followup_conversation"]
    assert saved["side_effects"] == ["soreness"] and saved["adherence"] == "partly"
    assert notes["followup_rating"] == 5 and saved["needs_attention"] is False
    assert _alerts("followup_red_flag") == []


async def test_a_typed_answer_that_does_not_fit_asks_again(checkin) -> None:
    apt, _ = checkin
    prompt = await _type("a lot")
    assert prompt.text == "Please choose a number from *0* to *10*. 🙏"
    assert prompt.buttons == fc.question(1, apt["id"], "en").buttons
    row = followup_repo.get(apt["id"])
    assert row is not None and row["step"] == 1 and row["pain_level"] is None


async def test_a_red_flag_alerts_the_therapist_at_once_and_only_once(checkin) -> None:
    apt, _ = checkin
    await _type("9")
    alerts = _alerts("followup_red_flag")
    assert len(alerts) == 1, "raised on the answer, not at the end"
    alert = alerts[0]
    assert alert["severity"] == "error" and alert["persistent"] == 1
    assert alert["appointment_id"] == apt["id"] and "pain 9/10" in alert["body"]
    assert alert["title"] == "Dana Levi needs attention after their treatment"
    row = followup_repo.get(apt["id"])
    assert row is not None and row["needs_attention"] and row["status"] == "in_progress"

    await _type("1")  # much worse — still the same alert
    await _type("fainted")
    for text in ("same", "no", "skip"):
        await _type(text)
    assert len(_alerts("followup_red_flag")) == 1
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "completed" and row["needs_attention"]
    assert row["side_effects"] == ["fainting"]
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None and notes["followup_conversation"]["needs_attention"] is True


async def test_fainting_alone_is_a_red_flag(checkin) -> None:
    apt, _ = checkin
    await _type("2")
    await _type("4")
    await _type("dizzy")
    assert _alerts("followup_red_flag") == []
    row = followup_repo.get(apt["id"])
    assert row is not None and row["step"] == 4

    followup_repo.save_progress(apt["id"], step=3, conversation=[], answers={})
    await _type("fainted")
    (alert,) = _alerts("followup_red_flag")
    assert "reported fainting" in alert["body"]


class _Model:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply
        self.prompts: list[Any] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.prompts.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return SimpleNamespace(content=self.reply)


@pytest.mark.parametrize(
    ("reply", "kept"),
    [
        ("Pain low (3/10), much better.\nMild soreness; advice partly followed.", True),
        ("one\ntwo\nthree", False),
        ("x" * 400, False),
        ("   ", False),
        (TimeoutError("slow"), False),
    ],
)
async def test_the_ai_may_word_the_summary_but_never_decides_it(
    monkeypatch, reply: str | Exception, kept: bool
) -> None:
    from bot.patient_bot.services import ai_intake
    from bot.services.followup_scheduler import summarize_checkin

    model = _Model(reply)
    monkeypatch.setattr(ai_intake, "_LLM", model)
    answers = {"pain_level": 3, "improvement_rating": 5, "side_effects": ["soreness"]}
    text = await summarize_checkin(answers, "en")
    fixed = fc.summary(answers, "en")
    assert text == (str(reply).strip() if kept else fixed)
    system, human = model.prompts[0]
    assert "Use only the facts below" in system.content
    assert human.content == fixed, "the AI sees the answers, nothing else"


class _Query:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered: list[Any] = []
        self.markups: list[Any] = []
        self.message = SimpleNamespace(replies=[])

        async def _reply(text: str, **kw: Any) -> None:
            self.message.replies.append({"text": text, **kw})

        self.message.reply_text = _reply

    async def answer(self, text: Any = None) -> None:
        self.answered.append(text)

    async def edit_message_reply_markup(self, reply_markup: Any = None) -> None:
        self.markups.append(reply_markup)


async def test_the_telegram_button_handler(checkin) -> None:
    from telegram.ext import ApplicationHandlerStop

    from bot.patient_bot.followup import handle_followup_button

    apt, _ = checkin
    context: Any = SimpleNamespace()

    def update(query: _Query) -> Any:
        return SimpleNamespace(
            callback_query=query, effective_user=SimpleNamespace(id=PID), message=None
        )

    ignored = _Query("book:2026-03-02")
    await handle_followup_button(update(ignored), context)
    assert ignored.answered == [], "not a check-in button: left for the conversation"

    tap = _Query(fc.callback(apt["id"], 1, "4"))
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_button(update(tap), context)
    assert tap.answered == [None] and tap.markups == [None], "answered: buttons removed"
    (reply,) = tap.message.replies
    assert "changed" in reply["text"]
    assert len(reply["reply_markup"].inline_keyboard) == 5

    toggle = _Query(fc.callback(apt["id"], 2, "3"))
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_button(update(toggle), context)
    soreness = _Query(fc.callback(apt["id"], 3, "t:soreness"))
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_button(update(soreness), context)
    (markup,) = soreness.markups
    assert markup.inline_keyboard[1][0].text == "✓ Soreness"
    assert soreness.message.replies == []

    stale = _Query(fc.callback(apt["id"], 1, "7"))
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_button(update(stale), context)
    assert stale.answered == ["That question was already answered."]
    assert stale.markups == [None], "an out-of-date question loses its buttons"

    invalid = _Query(fc.callback(apt["id"], 3, "t:none"))
    with pytest.raises(ApplicationHandlerStop):
        await handle_followup_button(update(invalid), context)
    assert invalid.answered == ["Please choose one of the options. 🙏"]
    assert invalid.markups == [], "a refused tap keeps the question's buttons"


async def test_the_button_handler_is_registered_before_the_conversation() -> None:
    from telegram.ext import CallbackQueryHandler

    from bot.main import build_patient_app
    from bot.patient_bot.followup import handle_followup_button

    app = build_patient_app()
    early = [h for h in app.handlers.get(-1, []) if isinstance(h, CallbackQueryHandler)]
    assert [h.callback for h in early] == [handle_followup_button]
