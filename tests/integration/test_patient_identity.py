"""Plan 7.2 — patients have an internal id; how to reach them lives in `patient_channels`.

A patient used to BE a Telegram user id, and a manual booking was a negative number. Whether a
patient can be messaged is now a property of the patient (a linked channel), never of the id.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import patient_repo

pytestmark = pytest.mark.integration

TG = 900_777_001


def _q(sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute(sql, args)]


# ── the repository ──
def test_a_channel_identity_maps_to_one_patient(db) -> None:
    first = patient_repo.for_channel("telegram", TG, "Dana")
    again = patient_repo.for_channel("telegram", str(TG), "Dana Levi")
    other = patient_repo.for_channel("telegram", TG + 1, "Someone")

    assert first == again != other
    assert first > 0 and first != TG, "the internal id is not the Telegram id"
    assert patient_repo.find_by_channel("telegram", TG) == first
    assert patient_repo.find_by_channel("telegram", 12345) is None
    patient = patient_repo.get(first)
    assert patient is not None and patient["full_name"] == "Dana", "a known name is not replaced"


def test_a_blank_name_is_filled_later(db) -> None:
    pid = patient_repo.for_channel("telegram", TG, "")
    patient_repo.for_channel("telegram", TG, "Dana")
    assert (patient_repo.get(pid) or {})["full_name"] == "Dana"


def test_the_messaging_contact_is_a_channel_not_a_sign(db) -> None:
    telegram = patient_repo.for_channel("telegram", TG, "Dana")
    manual = patient_repo.create("Noa", phone="050-1", email="noa@example.com")

    assert patient_repo.messaging_contact(telegram) == ("telegram", str(TG))
    assert patient_repo.messaging_contact(manual) is None
    assert manual > 0

    patient_repo.link_channel(manual, "telegram", TG + 5)
    assert patient_repo.messaging_contact(manual) == (
        "telegram",
        str(TG + 5),
    ), "linking a channel later makes the same patient reachable — the id never changes"


def test_a_channel_identity_cannot_belong_to_two_patients(db) -> None:
    a = patient_repo.for_channel("telegram", TG, "A")
    b = patient_repo.create("B")
    with pytest.raises(patient_repo.ChannelTaken):
        patient_repo.link_channel(b, "telegram", TG)
    patient_repo.link_channel(a, "telegram", TG)  # linking the owner again is a no-op
    assert patient_repo.messaging_contact(b) is None


def test_the_primary_channel_wins(db) -> None:
    pid = patient_repo.create("C")
    patient_repo.link_channel(pid, "telegram", 111, primary=False)
    patient_repo.link_channel(pid, "telegram", 222, primary=True)
    patient_repo.link_channel(pid, "telegram", 333, primary=False)
    assert patient_repo.messaging_contact(pid) == ("telegram", "222")
    primaries = _q(
        "SELECT COUNT(*) AS n FROM patient_channels WHERE patient_id=? AND is_primary=1", pid
    )
    assert primaries == [{"n": 1}]


@pytest.mark.parametrize(
    ("channel", "external_id"),
    [("sms", "1"), ("telegram", ""), ("telegram", "x" * 65)],
)
def test_the_channel_table_guards_its_values(db, channel: str, external_id: str) -> None:
    pid = patient_repo.create("D")
    with pytest.raises((sqlite3.IntegrityError, ValueError)):
        patient_repo.link_channel(pid, channel, external_id)


def test_deleting_a_patient_removes_its_channels(db) -> None:
    pid = patient_repo.for_channel("telegram", TG, "E")
    dbmod.get_db().execute("DELETE FROM patients WHERE id=?", (pid,))
    assert _q("SELECT * FROM patient_channels") == []


# ── bookings create patients, never negative ids ──
async def test_a_bot_booking_links_the_telegram_user(
    db, fake_redis, make_therapist, monkeypatch
) -> None:
    from bot.patient_bot import schedule
    from bot.states import SELECTING
    from tests.bot.conftest import FakeQuery, make_context, make_update

    async def _book(*a: Any, **k: Any) -> str:
        return "evt"

    from web.services import booking_service

    async def _hours(day: Any, therapist_id: Any = None) -> list[str]:
        return ["10:00", "11:00"]

    monkeypatch.setattr(booking_service, "book_slot", _book)
    monkeypatch.setattr(booking_service, "get_available_hours", _hours)
    t = make_therapist(therapist_id="t1", telegram_id=700_001)
    day = date(2026, 3, 12)
    for hour in ("10:00", "11:00"):
        user_data = {
            "selected_therapist": t["id"],
            "selected_day": day.isoformat(),
            "selected_time": hour,
        }
        query = FakeQuery("intake_no", user_id=TG)
        update = make_update(None, user_id=TG, full_name="Dana Levi", query=query)
        assert await schedule.skip_intake(update, make_context(user_data)) == SELECTING

    pid = patient_repo.find_by_channel("telegram", TG)
    assert pid is not None
    rows = _q("SELECT patient_id, patient_name, source FROM appointments ORDER BY time")
    assert rows == [
        {"patient_id": pid, "patient_name": "Dana Levi", "source": "telegram"},
        {"patient_id": pid, "patient_name": "Dana Levi", "source": "telegram"},
    ]
    assert (patient_repo.get(pid) or {})["full_name"] == "Dana Levi"


async def test_a_manual_booking_creates_a_patient_without_a_channel(authenticated_client) -> None:
    resp = await authenticated_client.post(
        "/api/appointments",
        json={
            "patient_name": "Noa Manual",
            "date": "2026-03-10",
            "time": "09:00",
            "patient_phone": "052-2222222",
            "patient_email": "noa@example.com",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pid = body["patient_id"]
    assert pid > 0
    assert body["treatment_url"] == f"/treatment/{pid}/2026-03-10/09-00"
    patient = patient_repo.get(pid)
    assert patient is not None
    assert (patient["full_name"], patient["phone"], patient["email"]) == (
        "Noa Manual",
        "052-2222222",
        "noa@example.com",
    )
    assert patient_repo.messaging_contact(pid) is None
    assert _q("SELECT MIN(patient_id) AS m FROM appointments")[0]["m"] > 0


async def test_a_manual_booking_for_a_known_patient_reuses_the_id(
    authenticated_client, make_appointment, make_patient
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    known = make_patient("Dana", telegram_id=TG)
    make_appointment(therapist={"id": tid}, patient=known, apt_date="2026-03-01")

    resp = await authenticated_client.post(
        "/api/appointments",
        json={
            "patient_name": "Dana",
            "date": "2026-03-11",
            "time": "09:00",
            "existing_patient_id": known["patient_id"],
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["patient_id"] == known["patient_id"]
    assert len(_q("SELECT id FROM patients")) == 1


async def test_another_therapists_patient_cannot_be_booked(
    authenticated_client, make_appointment, make_patient, make_therapist
) -> None:
    """A guessed id must not attach a booking — and with it follow-up messages — to a stranger."""
    stranger = make_patient("Stranger", telegram_id=TG)
    make_appointment(therapist=make_therapist(therapist_id="t9"), patient=stranger)

    resp = await authenticated_client.post(
        "/api/appointments",
        json={
            "patient_name": "Stranger",
            "date": "2026-03-11",
            "time": "09:00",
            "existing_patient_id": stranger["patient_id"],
        },
    )

    assert resp.status_code == 404
    assert len(_q("SELECT id FROM appointments")) == 1


async def test_an_unknown_patient_id_is_refused(authenticated_client) -> None:
    resp = await authenticated_client.post(
        "/api/appointments",
        json={
            "patient_name": "X",
            "date": "2026-03-11",
            "time": "09:00",
            "existing_patient_id": 4242,
        },
    )
    assert resp.status_code == 404
    assert _q("SELECT id FROM appointments") == []


# ── messages go to the channel identity ──
FROZEN = "2026-03-01T12:00:00Z"
ITEMS = [{"enabled": True, "category": "Diet", "text": "Warm food"}]


async def test_the_follow_up_goes_to_the_telegram_id(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, fake_telegram
) -> None:
    import bot.services.followup_jobs  # noqa: F401
    from zenflow import queue as q
    from zenflow import worker as w

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=TG)
    apt = make_appointment(therapist={"id": tid}, patient=patient, apt_date="2026-03-01")
    make_treatment_notes(apt)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        resp = await authenticated_client.post(
            f"/api/treatment-notes/{apt['patient_id']}/2026-03-01/10-00/complete", json={}
        )
        assert resp.status_code == 200
        frozen.tick(timedelta(hours=24, minutes=1))
        await w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="t").run_once()

    assert fake_telegram.calls, "the check-in and the recommendations went out"
    assert {c["chat_id"] for c in fake_telegram.calls} == {TG}
    logged = _q("SELECT DISTINCT patient_id, channel FROM message_log")
    assert logged == [{"patient_id": patient["patient_id"], "channel": "telegram"}]


async def test_a_manual_booking_of_a_telegram_patient_is_reachable(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    """Reachability belongs to the patient: booking them by hand does not make them unreachable."""
    from web.repositories import followup_repo

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=TG)
    apt = make_appointment(
        therapist={"id": tid}, patient={**patient, "source": "manual"}, apt_date="2026-03-01"
    )
    make_treatment_notes(apt)
    resp = await authenticated_client.post(
        f"/api/treatment-notes/{apt['patient_id']}/2026-03-01/10-00/complete", json={}
    )
    assert resp.status_code == 200
    row = followup_repo.get(apt["id"])
    assert row is not None and (row["status"], row["channel"]) == ("scheduled", "telegram")


async def test_send_now_uses_the_telegram_id(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, fake_telegram
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=TG)
    apt = make_appointment(therapist={"id": tid}, patient=patient, apt_date="2026-03-01")
    make_treatment_notes(apt)

    resp = await authenticated_client.post(
        f"/api/treatment-notes/{apt['patient_id']}/2026-03-01/10-00/send-recommendations",
        json={"items": ITEMS},
    )

    assert resp.status_code == 200, resp.text
    assert [c["chat_id"] for c in fake_telegram.calls] == [TG]


async def test_a_patient_without_a_channel_is_email_only(
    authenticated_client, make_appointment, make_patient, make_treatment_notes, fake_telegram
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, patient=make_patient("Noa", manual=True))
    make_treatment_notes(apt)
    slug = f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"

    notes = (await authenticated_client.get(f"/api/treatment-notes/{slug}")).json()
    assert notes["is_manual"] is True

    resp = await authenticated_client.post(
        f"/api/treatment-notes/{slug}/send-recommendations", json={"items": ITEMS}
    )
    assert resp.status_code == 422 and resp.json()["status"] == "needs_email"
    assert fake_telegram.api_calls == []


async def test_a_follow_up_reply_is_matched_through_the_channel(
    db, fake_redis, make_completed_session, make_patient
) -> None:
    from bot.services.followup_scheduler import consume_followup_conversation
    from web.repositories import followup_repo
    from zenflow import clock

    patient = make_patient("Dana", telegram_id=TG)
    apt = make_completed_session(patient=patient)
    followup_repo.schedule(apt["id"], clock.iso_now())
    followup_repo.mark_sent(apt["id"], [])

    consumed, _ = await consume_followup_conversation(patient["patient_id"], "5")
    assert consumed is False, "an internal id is not a Telegram sender"
    consumed, _ = await consume_followup_conversation(TG, "5")
    assert consumed is True
    assert (followup_repo.get(apt["id"]) or {})["pain_level"] == 5


async def test_cancelling_lists_the_senders_own_appointments(
    db, fake_redis, make_therapist, make_appointment, make_patient, monkeypatch
) -> None:
    from bot.patient_bot import cancel
    from bot.states import CANCEL_SELECT
    from tests.bot.conftest import FakeQuery, make_context, make_update

    t = make_therapist(therapist_id="t1")
    mine = make_patient("Dana", telegram_id=TG)
    make_appointment(therapist=t, patient=mine, apt_date="2099-01-05")
    make_appointment(therapist=t, patient=make_patient("Other"), apt_date="2099-01-06")

    user_data: dict[str, Any] = {}
    query = FakeQuery("cancel", user_id=TG)
    update = make_update(None, user_id=TG, query=query)
    assert await cancel.show_appointments(update, make_context(user_data)) == CANCEL_SELECT
    assert [a["date"] for a in user_data["apts_to_cancel"]] == ["2099-01-05"]


# ── no id-sign heuristics are left ──
def test_no_code_decides_anything_by_the_sign_of_a_patient_id() -> None:
    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(r"(patient_?[iI]d|pat_id|\bpid)\)?\s*<\s*0")
    offenders = [
        f"{p.relative_to(root)}:{n}"
        for folder in ("bot", "web", "zenflow")
        for p in (root / folder).rglob("*")
        if p.suffix in {".py", ".js", ".html"}
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_manual_bookings_no_longer_mint_negative_ids() -> None:
    source = (
        Path(__file__).resolve().parents[2] / "web/repositories/appointment_repo.py"
    ).read_text(encoding="utf-8")
    assert "-int(time.time()" not in source
