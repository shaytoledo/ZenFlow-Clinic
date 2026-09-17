"""Phase 2.3 — a bot restart must not lose a patient's half-finished booking.

Conversation state and `user_data` lived only in memory, so every deploy or crash dropped every
in-flight flow: the patient's next button press met a bot that had never heard of them. The plan's
acceptance test is literal: start a booking, restart the app, continue the booking to completion.

What is persisted is deliberately narrow (plan 2.3: "never persist raw clinical free text"):
conversation states and a whitelist of scheduling keys. Intake answers stay in Redis with their
own TTL; `bot_data` holds live asyncio tasks and is never stored.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest
from telegram import Update
from telegram.ext import Application, ConversationHandler
from telegram.request import BaseRequest, RequestData

from bot.persistence import SqlitePersistence
from bot.states import INTAKE_CONFIRM, SCHEDULE_DAY, SCHEDULE_HOUR, SCHEDULE_WEEK, SELECTING

PATIENT = 900_000_901
DAY = date(2026, 3, 12)
KEY = (PATIENT, PATIENT)


# ── the store itself ──
async def test_conversations_and_user_data_survive_a_new_instance(db) -> None:
    first = SqlitePersistence()
    await first.update_conversation("patient", KEY, SCHEDULE_DAY)
    await first.update_user_data(PATIENT, {"selected_therapist": "t1", "selected_week": 0})
    await first.flush()

    second = SqlitePersistence()  # what a restarted process sees
    assert (await second.get_conversations("patient"))[KEY] == SCHEDULE_DAY
    assert (await second.get_user_data())[PATIENT] == {
        "selected_therapist": "t1",
        "selected_week": 0,
    }


async def test_only_scheduling_keys_are_written(db) -> None:
    store = SqlitePersistence()
    await store.update_user_data(
        PATIENT,
        {
            "selected_therapist": "t1",
            "selected_day": "2026-03-12",
            "intake_answer": "my back has hurt since the accident",  # not on the whitelist
            "apts_to_cancel": [
                {
                    "id": 7,
                    "patient_id": PATIENT,
                    "date": "2026-03-12",
                    "time": "10:00",
                    "therapist_id": "t1",
                    "gcal_apt_event_id": None,
                    "summary": "Chronic lumbar pain, Liver Qi stagnation",  # clinical
                    "patient_name": "Moshe Levi",
                }
            ],
        },
    )

    from bot.db import get_db

    raw = " ".join(
        r["value_json"] for r in get_db().execute("SELECT value_json FROM bot_persistence")
    )
    assert "accident" not in raw, "free text must not reach the disk"
    assert "Qi stagnation" not in raw, "clinical summaries must not reach the disk"
    assert "Moshe" not in raw

    restored = (await SqlitePersistence().get_user_data())[PATIENT]
    assert restored["selected_day"] == "2026-03-12"
    assert restored["apts_to_cancel"] == [
        {
            "id": 7,
            "patient_id": PATIENT,
            "date": "2026-03-12",
            "time": "10:00",
            "therapist_id": "t1",
            "gcal_apt_event_id": None,
        }
    ], "cancelling still works after a restart"


async def test_an_ended_conversation_is_removed(db) -> None:
    store = SqlitePersistence()
    await store.update_conversation("patient", KEY, SCHEDULE_DAY)
    await store.update_conversation("patient", KEY, None)
    assert KEY not in await SqlitePersistence().get_conversations("patient")


async def test_dropped_user_data_is_removed(db) -> None:
    store = SqlitePersistence()
    await store.update_user_data(PATIENT, {"selected_therapist": "t1"})
    await store.drop_user_data(PATIENT)
    assert PATIENT not in await SqlitePersistence().get_user_data()


async def test_a_flow_older_than_the_timeout_is_not_resumed(db) -> None:
    """After a long outage the patient starts fresh instead of landing mid-booking."""
    from bot.db import get_db

    store = SqlitePersistence(stale_after_seconds=30 * 60)
    await store.update_conversation("patient", KEY, SCHEDULE_DAY)
    await store.update_user_data(
        PATIENT, {"selected_therapist": "t1", "selected_day": "2026-03-12"}
    )
    get_db().execute("UPDATE bot_persistence SET updated_at='2020-01-01T00:00:00Z'")

    fresh = SqlitePersistence(stale_after_seconds=30 * 60)
    assert KEY not in await fresh.get_conversations("patient")
    assert (await fresh.get_user_data())[PATIENT] == {
        "selected_therapist": "t1"
    }, "the therapist choice outlives the flow; the half-booking does not"


def test_live_objects_are_never_persisted() -> None:
    store = SqlitePersistence()
    assert store.store_data.bot_data is False, "bot_data holds running asyncio tasks"
    assert store.store_data.chat_data is False
    assert store.store_data.callback_data is False
    assert store.store_data.user_data is True


def test_the_patient_app_is_persistent(db) -> None:
    from bot.main import build_patient_app

    app = build_patient_app()
    assert isinstance(app.persistence, SqlitePersistence)
    conv = next(h for hs in app.handlers.values() for h in hs if isinstance(h, ConversationHandler))
    assert conv.persistent and conv.name == "patient"


# ── the acceptance test: book, restart, finish ──
class FakeTelegramApi(BaseRequest):
    """Answers the Bot API offline, so a real `Application` can process real `Update`s."""

    BOT_USER = {"id": 1_000_000_000, "is_bot": True, "first_name": "ZenFlow", "username": "zf_bot"}

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next = 1000

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def do_request(  # type: ignore[override]
        self, url: str, method: str, request_data: RequestData | None = None, **_: Any
    ) -> tuple[int, bytes]:
        api = url.rsplit("/", 1)[-1]
        params = request_data.parameters if request_data else {}
        self.calls.append((api, params))
        if api == "getMe":
            result: Any = self.BOT_USER
        elif api in ("sendMessage", "editMessageText"):
            self._next += 1
            result = {
                "message_id": self._next,
                "date": 1_700_000_000,
                "chat": {"id": params.get("chat_id", PATIENT), "type": "private"},
                "from": self.BOT_USER,
                "text": params.get("text", ""),
            }
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


_uid = iter(range(1, 10_000))
_PATIENT_USER = {"id": PATIENT, "is_bot": False, "first_name": "Pat"}
_CHAT = {"id": PATIENT, "type": "private"}


def _update(app: Application, payload: dict[str, Any]) -> Update:
    update = Update.de_json(payload, app.bot)
    assert update is not None
    return update


def _command(app: Application, text: str) -> Update:
    n = next(_uid)
    return _update(
        app,
        {
            "update_id": n,
            "message": {
                "message_id": n,
                "date": 1_700_000_000,
                "chat": _CHAT,
                "from": _PATIENT_USER,
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}],
            },
        },
    )


def _press(app: Application, data: str) -> Update:
    n = next(_uid)
    return _update(
        app,
        {
            "update_id": n,
            "callback_query": {
                "id": str(n),
                "from": _PATIENT_USER,
                "chat_instance": "ci",
                "data": data,
                "message": {
                    "message_id": 1,
                    "date": 1_700_000_000,
                    "chat": _CHAT,
                    "from": FakeTelegramApi.BOT_USER,
                    "text": "menu",
                },
            },
        },
    )


def _state(app: Application) -> object:
    conv = next(h for hs in app.handlers.values() for h in hs if isinstance(h, ConversationHandler))
    return conv._conversations.get(KEY)  # noqa: SLF001 — the state is the thing under test


@pytest.fixture
def booking_world(db, fake_redis, make_therapist, monkeypatch):
    """One therapist, free hours, and a calendar that accepts the booking."""
    from bot import config as botcfg
    from bot.patient_bot import schedule

    make_therapist(name="Dr Only", therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()

    async def _days(week_offset: int = 0, therapist_id: str | None = None):
        return [DAY]

    async def _hours(day, therapist_id: str | None = None):
        return ["10:00", "11:00"]

    async def _book(*a, **k):
        return "gcal-evt-restart"

    from web.services import booking_service

    monkeypatch.setattr(schedule, "get_available_days", _days)
    monkeypatch.setattr(schedule, "get_available_hours", _hours)
    monkeypatch.setattr(booking_service, "get_available_hours", _hours)
    monkeypatch.setattr(booking_service, "book_slot", _book)


async def test_a_booking_survives_a_restart(booking_world) -> None:
    from bot.db import get_db
    from bot.main import build_patient_app

    # ── process 1: the patient gets as far as choosing a week ──
    app = build_patient_app(request=FakeTelegramApi())
    await app.initialize()
    await app.process_update(_command(app, "/start"))
    assert _state(app) == SELECTING
    await app.process_update(_press(app, "schedule"))
    assert _state(app) == SCHEDULE_WEEK
    await app.process_update(_press(app, "week_0"))
    assert _state(app) == SCHEDULE_DAY
    await app.update_persistence()
    await app.shutdown()  # the deploy / crash

    # ── process 2: a brand-new application on the same database ──
    app = build_patient_app(request=FakeTelegramApi())
    await app.initialize()
    assert _state(app) == SCHEDULE_DAY, "the conversation came back"
    assert app.user_data[PATIENT]["selected_therapist"] == "t1", "and so did the therapist choice"

    await app.process_update(_press(app, f"day_{DAY.isoformat()}"))
    assert _state(app) == SCHEDULE_HOUR
    await app.process_update(_press(app, "hour_10:00"))
    assert _state(app) == INTAKE_CONFIRM
    await app.process_update(_press(app, "intake_no"))
    assert _state(app) == SELECTING
    await app.shutdown()

    from web.repositories import patient_repo

    row = (
        get_db()
        .execute(
            "SELECT * FROM appointments WHERE patient_id=? AND status='active'",
            (patient_repo.find_by_channel("telegram", PATIENT),),
        )
        .fetchone()
    )
    assert row is not None, "the booking that started before the restart was completed"
    assert (row["date"], row["time"], row["therapist_id"]) == (DAY.isoformat(), "10:00", "t1")
