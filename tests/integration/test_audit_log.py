"""Plan 8.1 — `audit_log`: what happened to this patient's data, when, and who did it.

Append-only (the database refuses an UPDATE or DELETE), one row per clinical mutation, with the
actor the change really came from: the therapist at the dashboard, the patient in a chat, an API
client, a background job, or the AI.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

import pytest

import bot.db as dbmod

pytestmark = pytest.mark.integration

TG = 940_000_001
DAY = "2026-03-12"


def _rows(action: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM audit_log"
    params: tuple[Any, ...] = ()
    if action:
        sql += " WHERE action=?"
        params = (action,)
    return [dict(r) for r in dbmod.get_db().execute(sql + " ORDER BY id", params)]


def _actions() -> list[str]:
    return [r["action"] for r in _rows()]


@pytest.fixture(autouse=True)
def calendar(monkeypatch):
    from web.services import booking_service

    async def _hours(day: date, therapist_id: str | None = None) -> list[str]:
        return ["09:00", "10:00", "11:00"]

    async def _book(*a: Any, **k: Any) -> str:
        return "gcal-1"

    async def _restore(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(booking_service, "get_available_hours", _hours)
    monkeypatch.setattr(booking_service, "book_slot", _book)
    monkeypatch.setattr(booking_service, "restore_slot", _restore)


# ── the table itself ──
def test_the_trail_cannot_be_rewritten(db) -> None:
    """An audit trail an actor can edit is not one."""
    from web.services import audit

    audit.record("appointment.created", "appointment", 1, after={"time": "10:00"})
    (row,) = _rows()

    conn = dbmod.get_db()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE audit_log SET action='nothing' WHERE id=?", (row["id"],))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM audit_log WHERE id=?", (row["id"],))
    assert _actions() == ["appointment.created"]


def test_a_row_says_who_what_and_when(db) -> None:
    from web.services import audit
    from zenflow import clock

    with audit.acting_as("therapist", "t1", ip="203.0.113.7", user_agent="Firefox"):
        audit.record(
            "treatment_notes.updated",
            "treatment_notes",
            7,
            before={"session_notes": "old"},
            after={"session_notes": "new"},
        )

    (row,) = _rows()
    assert (row["actor_type"], row["actor_id"]) == ("therapist", "t1")
    assert (row["entity_type"], row["entity_id"]) == ("treatment_notes", "7")
    assert json.loads(row["before_json"]) == {"session_notes": "old"}
    assert json.loads(row["after_json"]) == {"session_notes": "new"}
    assert (row["ip"], row["user_agent"]) == ("203.0.113.7", "Firefox")
    assert clock.normalize(row["ts"]) == row["ts"]


def test_without_an_actor_it_is_the_system(db) -> None:
    from web.services import audit

    audit.record("followup.expired", "followup", 3)
    (row,) = _rows()
    assert (row["actor_type"], row["actor_id"]) == ("system", "")


def test_secrets_never_reach_the_trail(db) -> None:
    from web.services import audit

    audit.record(
        "therapist.updated",
        "therapist",
        "t1",
        after={"password_hash": "pbkdf2$secret", "token": "1234567890:AAH-very-secret-token-value"},
    )
    (row,) = _rows()
    stored = row["after_json"]
    assert "pbkdf2$secret" not in stored and "AAH-very-secret-token-value" not in stored
    assert "redacted" in stored.lower()


def test_a_broken_trail_never_breaks_the_change(db, monkeypatch) -> None:
    """Recording is best effort: losing an audit row must not lose the patient's appointment."""
    from web.services import audit

    def _boom(*a: Any, **k: Any) -> None:
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(audit, "_insert", _boom)
    audit.record("appointment.created", "appointment", 1)  # does not raise


# ── one row per clinical mutation, with the right actor ──
async def test_a_dashboard_booking_is_the_therapists(authenticated_client) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    resp = await authenticated_client.post(
        "/api/appointments", json={"patient_name": "Noa", "date": DAY, "time": "10:00"}
    )
    assert resp.status_code == 200

    (row,) = _rows("appointment.created")
    assert (row["actor_type"], row["actor_id"]) == ("therapist", tid)
    assert row["entity_id"] == str(resp.json()["appointment_id"])
    assert json.loads(row["after_json"])["local_time"] == "10:00"
    assert row["request_id"], "the request it came from"


async def test_a_bot_booking_is_the_patients(db, fake_redis, make_therapist, monkeypatch) -> None:
    from bot.patient_bot import schedule
    from tests.bot.conftest import FakeQuery, make_context, make_update

    t = make_therapist(therapist_id="t1")
    query = FakeQuery("intake_no", user_id=TG)
    update = make_update(None, user_id=TG, full_name="Dana Levi", query=query)
    context = make_context(
        {"selected_therapist": t["id"], "selected_day": DAY, "selected_time": "10:00"}
    )
    await schedule.skip_intake(update, context)

    from web.repositories import patient_repo

    (row,) = _rows("appointment.created")
    assert row["actor_type"] == "patient"
    assert row["actor_id"] == str(patient_repo.find_by_channel("telegram", TG))


async def test_an_api_booking_names_the_client(client, make_therapist) -> None:
    from bot import config as botcfg
    from web.repositories import api_client_repo

    make_therapist(therapist_id="t1")
    botcfg.reload_therapists()
    _id, key = api_client_repo.create("whatsapp-bridge")
    client.headers["Authorization"] = f"Bearer {key}"

    resp = await client.post(
        "/api/v1/appointments",
        json={
            "therapist_id": "t1",
            "start_at": f"{DAY}T08:00:00Z",
            "patient": {"name": "Dana", "channel": "telegram", "external_id": str(TG)},
        },
    )
    assert resp.status_code == 201, resp.text

    (row,) = _rows("appointment.created")
    assert (row["actor_type"], row["actor_id"]) == ("api", "whatsapp-bridge")


async def test_cancelling_and_completing_are_recorded(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid}, patient=make_patient("Dana"), apt_date=DAY, apt_time="10:00"
    )
    make_treatment_notes(apt)

    slug = f"{apt['patient_id']}/{DAY}/10-00"
    assert (
        await authenticated_client.post(
            f"/api/treatment-notes/{slug}", json={"session_notes": "tender at LI4"}
        )
    ).status_code == 200
    assert (
        await authenticated_client.post(f"/api/treatment-notes/{slug}/complete", json={})
    ).status_code == 200
    assert (
        await authenticated_client.delete(f"/api/v1/appointments/{apt['id']}")
    ).status_code == 200

    assert _actions() == [
        "treatment_notes.updated",
        "session.completed",
        "appointment.cancelled",
    ]
    assert {r["actor_id"] for r in _rows()} == {tid}
    notes = _rows("treatment_notes.updated")[0]
    assert json.loads(notes["after_json"])["session_notes"] == "tender at LI4"


async def test_a_patients_answer_is_the_patients(
    db, fake_redis, make_completed_session, make_patient
) -> None:
    from bot.services.followup_scheduler import consume_followup_conversation
    from web.repositories import followup_repo
    from zenflow import clock

    patient = make_patient("Dana", telegram_id=TG)
    apt = make_completed_session(patient=patient)
    followup_repo.schedule(apt["id"], clock.iso_now())
    followup_repo.mark_sent(apt["id"], [])
    _rows()  # the schedule itself is a system row

    consumed, _prompt = await consume_followup_conversation(TG, "5")
    assert consumed

    answered = _rows("followup.answered")
    assert len(answered) == 1
    assert (answered[0]["actor_type"], answered[0]["actor_id"]) == (
        "patient",
        str(patient["patient_id"]),
    )
    assert json.loads(answered[0]["after_json"])["pain_level"] == 5


async def test_the_ai_is_named_when_it_writes_notes(
    db, fake_redis, make_appointment, make_patient
) -> None:
    from bot.patient_bot.services.appointments import save_treatment_notes
    from web.services import audit

    apt = make_appointment(patient=make_patient("Dana"))
    with audit.acting_as("ai", "gemma3:latest"):
        save_treatment_notes(apt["id"], apt["patient_id"], {"tcm_pattern": "Liver Qi Stagnation"})

    (row,) = _rows("treatment_notes.updated")
    assert (row["actor_type"], row["actor_id"]) == ("ai", "gemma3:latest")


# ── the plan's rule, checked against the routes themselves ──
CLINICAL_MUTATIONS = {
    ("POST", "/api/appointments"),
    ("POST", "/api/v1/appointments"),
    ("DELETE", "/api/v1/appointments/{appointment_id}"),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}"),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/complete"),
    ("POST", "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/manual-feedback"),
}


def test_every_clinical_mutation_is_audited() -> None:
    """A new endpoint that changes clinical data has to say so here — and record it."""
    import inspect

    from web.app import app
    from web.services import audit

    audited = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not path.startswith("/api/"):
            continue
        for method in getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}:
            if (method, path) not in CLINICAL_MUTATIONS:
                continue
            source = inspect.getsource(inspect.getmodule(endpoint) or endpoint)
            assert (
                audit.record.__name__ in source or "audit." in source
            ), f"{method} {path} changes clinical data but never records it"
            audited.add((method, path))
    assert audited == CLINICAL_MUTATIONS, f"missing routes: {CLINICAL_MUTATIONS - audited}"


async def test_manual_feedback_is_recorded(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid}, patient=make_patient("Noa", manual=True), apt_date=DAY
    )
    make_treatment_notes(apt)
    resp = await authenticated_client.post(
        f"/api/treatment-notes/{apt['patient_id']}/{DAY}/{apt['time'].replace(':', '-')}/manual-feedback",
        json={"rating": 4, "notes": "Called: much better"},
    )
    assert resp.status_code == 200
    (row,) = _rows("followup.recorded")
    assert (row["actor_type"], row["actor_id"]) == ("therapist", tid)
