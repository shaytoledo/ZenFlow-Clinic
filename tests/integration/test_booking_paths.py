"""Plan 7.3b — the Telegram flow and the dashboard book through the one booking service.

Until now each had its own order of steps. They now call `web/services/booking_service.py`, so
there is one answer to "is this hour free?", one patient resolution and one calendar order.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

import bot.db as dbmod
from tests.bot.conftest import FakeQuery, make_context, make_update
from web.repositories import patient_repo

pytestmark = pytest.mark.integration

TG = 930_777_001
DAY = date(2026, 3, 12)
HOURS = ["09:00", "10:00", "11:00"]
ROOT = Path(__file__).resolve().parents[2]


def _q(sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute(sql, args)]


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    """Published hours, and a calendar that records what it was asked to do."""
    from web.services import booking_service

    events: dict[str, list[Any]] = {"booked": [], "restored": [], "hours": list(HOURS)}

    async def _hours(day: date, therapist_id: str | None = None) -> list[str]:
        return list(events["hours"]) if day == DAY else []

    async def _book(*a: Any, **k: Any) -> str:
        events["booked"].append(k or a)
        return "gcal-evt-1"

    async def _restore(*a: Any, **k: Any) -> None:
        events["restored"].append((a, k))

    monkeypatch.setattr(booking_service, "get_available_hours", _hours)
    monkeypatch.setattr(booking_service, "book_slot", _book)
    monkeypatch.setattr(booking_service, "restore_slot", _restore)
    return events


@pytest.fixture
def therapist(db, make_therapist):
    from bot import config as botcfg

    t = make_therapist(name="Dr One", therapist_id="t1", telegram_id=700_001)
    botcfg.reload_therapists()
    return t


def _booking_context(therapist_id: str, hour: str = "10:00") -> Any:
    return make_context(
        {
            "selected_therapist": therapist_id,
            "selected_day": DAY.isoformat(),
            "selected_time": hour,
        }
    )


# ── one implementation ──
def test_only_one_module_inserts_an_appointment() -> None:
    """Every booking path goes through `save_appointment`, which the service owns."""
    inserts = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for folder in ("bot", "web", "zenflow")
        for p in (ROOT / folder).rglob("*.py")
        if re.search(r"INSERT\s+INTO\s+appointments", p.read_text(encoding="utf-8"), re.I)
    )
    assert inserts == ["bot/patient_bot/services/appointments.py"]


def test_save_appointment_is_called_by_the_booking_service_only() -> None:
    callers = sorted(
        str(p.relative_to(ROOT)).replace("\\", "/")
        for folder in ("bot", "web", "zenflow")
        for p in (ROOT / folder).rglob("*.py")
        if re.search(r"^\s*(await |)save_appointment\(", p.read_text(encoding="utf-8"), re.M)
        or re.search(r"\bsave_appointment,$", p.read_text(encoding="utf-8"), re.M)
    )
    assert callers == ["web/services/booking_service.py"]


# ── the Telegram flow ──
async def test_the_bot_books_through_the_service(therapist, fake_redis, calendar) -> None:
    from bot.patient_bot import schedule
    from bot.states import SELECTING

    query = FakeQuery("intake_no", user_id=TG)
    update = make_update(None, user_id=TG, full_name="Dana Levi", query=query)

    assert await schedule.skip_intake(update, _booking_context(therapist["id"])) == SELECTING

    pid = patient_repo.find_by_channel("telegram", TG)
    (row,) = _q("SELECT * FROM appointments")
    assert (row["patient_id"], row["date"], row["time"], row["source"]) == (
        pid,
        DAY.isoformat(),
        "10:00",
        "telegram",
    )
    assert row["gcal_apt_event_id"] == "gcal-evt-1" and calendar["booked"]
    assert query.edits, "the patient is told in the conversation"
    assert _q("SELECT name FROM jobs") == [], "no confirmation job: the chat confirms"


async def test_the_bot_stores_notes_against_the_patient_not_the_telegram_id(
    therapist, fake_redis
) -> None:
    """The empty notes row created at booking time used to carry the Telegram user id."""
    from bot.patient_bot import schedule

    query = FakeQuery("intake_no", user_id=TG)
    update = make_update(None, user_id=TG, full_name="Dana Levi", query=query)
    await schedule.skip_intake(update, _booking_context(therapist["id"]))

    pid = patient_repo.find_by_channel("telegram", TG)
    assert _q("SELECT patient_id FROM treatment_notes") == [{"patient_id": pid}]
    assert pid != TG


async def test_an_hour_taken_meanwhile_is_told_to_the_patient(
    therapist, fake_redis, calendar
) -> None:
    from bot.patient_bot import schedule
    from bot.states import SELECTING

    calendar["hours"] = ["09:00"]  # 10:00 is gone
    query = FakeQuery("intake_no", user_id=TG)
    update = make_update(None, user_id=TG, full_name="Dana Levi", query=query)

    assert await schedule.skip_intake(update, _booking_context(therapist["id"])) == SELECTING

    assert _q("SELECT id FROM appointments") == []
    assert "no longer" in " ".join(e["text"] for e in query.edits).lower() or query.edits


async def test_the_intake_booking_keeps_its_conversation(
    therapist, fake_redis, fake_llm, monkeypatch
) -> None:
    from bot.patient_bot import schedule
    from bot.patient_bot.services.ai_intake import initialize_intake

    monkeypatch.setattr(schedule, "_start_generation", lambda *a, **k: None)
    initialize_intake(TG, "What brings you in today?")
    context = _booking_context(therapist["id"])
    context.user_data["intake_count"] = 0
    for answer in ["Headache", "Three days", "Right side", "Worse with stress", "Sleep is poor"]:
        await schedule.handle_intake_answer(
            make_update(answer, user_id=TG, full_name="Dana Levi"), context
        )

    pid = patient_repo.find_by_channel("telegram", TG)
    (row,) = _q("SELECT * FROM appointments")
    assert row["patient_id"] == pid and row["source"] == "telegram"
    (intake,) = _q("SELECT patient_id, history_json FROM intake_sessions")
    assert intake["patient_id"] == pid
    assert "Headache" in intake["history_json"]
    assert _q("SELECT patient_id FROM treatment_notes") == [{"patient_id": pid}]


async def test_cancelling_from_the_bot_hands_the_hour_back(
    therapist, fake_redis, calendar, make_appointment, make_patient
) -> None:
    from bot.patient_bot import cancel
    from bot.states import SELECTING

    patient = make_patient("Dana", telegram_id=TG)
    apt = make_appointment(
        therapist=therapist, patient=patient, apt_date=DAY.isoformat(), apt_time="10:00"
    )
    context = make_context({"selected_therapist": therapist["id"], "apts_to_cancel": [apt]})
    query = FakeQuery("cancel_apt_0", user_id=TG)
    update = make_update(None, user_id=TG, query=query)

    assert await cancel.confirm_cancel(update, context) == SELECTING

    assert _q("SELECT status FROM appointments") == [{"status": "cancelled"}]
    assert calendar["restored"], "the hour went back to the availability calendar"
    # the intake cache lives in a Redis this test never started: clearing it must not matter


# ── the dashboard ──
async def test_the_dashboard_books_through_the_service(authenticated_client, calendar) -> None:
    resp = await authenticated_client.post(
        "/api/appointments",
        json={
            "patient_name": "Noa Manual",
            "date": DAY.isoformat(),
            "time": "10:00",
            "patient_phone": "052-2222222",
            "patient_email": "noa@example.com",
            "notes": "walk-in",
        },
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    (row,) = _q("SELECT * FROM appointments")
    assert (row["source"], row["time"], row["summary"]) == ("manual", "10:00", "walk-in")
    assert row["patient_id"] == body["patient_id"]
    assert row["gcal_apt_event_id"] == "gcal-evt-1" and calendar["booked"]
    patient = patient_repo.get(body["patient_id"])
    assert patient is not None and patient["phone"] == "052-2222222"
    assert body["treatment_url"] == f"/treatment/{body['patient_id']}/{DAY.isoformat()}/10-00"
    assert _q("SELECT name FROM jobs") == [], "the therapist booked it; no confirmation is sent"


async def test_the_dashboard_may_book_outside_the_published_hours(
    authenticated_client, calendar
) -> None:
    """A therapist's own calendar is theirs — availability is for patients and machine clients."""
    calendar["hours"] = []
    resp = await authenticated_client.post(
        "/api/appointments",
        json={"patient_name": "Late Extra", "date": DAY.isoformat(), "time": "19:00"},
    )
    assert resp.status_code == 200, resp.text
    assert _q("SELECT time FROM appointments") == [{"time": "19:00"}]


async def test_the_dashboard_still_refuses_a_taken_hour(
    authenticated_client, make_appointment, make_patient
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    make_appointment(
        therapist={"id": tid},
        patient=make_patient("Taken"),
        apt_date=DAY.isoformat(),
        apt_time="10:00",
    )
    resp = await authenticated_client.post(
        "/api/appointments",
        json={"patient_name": "Walk-in", "date": DAY.isoformat(), "time": "10:00"},
    )
    assert resp.status_code == 409 and "booked" in resp.json()["detail"].lower()
    assert len(_q("SELECT id FROM appointments")) == 1
