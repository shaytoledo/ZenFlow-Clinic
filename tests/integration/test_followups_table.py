"""Phase 6.3 — the `followups` table: one row per session's 24h check-in.

Covers the schema, the start-up backfill from `treatment_notes`, the lifecycle
(scheduled → sent → in_progress → completed / expired, no_channel), the conversation surviving a
Redis flush (open item from 6.1), adoption of a pre-6.3 Redis conversation, and the parallel
`treatment_notes` write.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import followup_repo, treatment_repo
from zenflow import clock
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"
PID = 900_000_601


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


async def _complete(client: Any, apt: dict[str, Any]) -> None:
    url = f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}/complete"
    assert (await client.post(url, json={"session_notes": "x"})).status_code == 200


async def _answer(text: str, patient_id: int = PID) -> str:
    from bot.services.followup_scheduler import consume_followup_conversation

    consumed, prompt = await consume_followup_conversation(patient_id, text)
    assert consumed and prompt is not None, f"{text!r} was not taken as a follow-up answer"
    return str(prompt.text)


@pytest.fixture
def session(authenticated_client, make_appointment, make_treatment_notes):
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = {"patient_id": PID, "name": "Noa Katz", "source": "telegram"}
    apt = make_appointment(
        therapist={"id": tid}, patient=patient, apt_date="2026-03-01", apt_time="10:00"
    )
    make_treatment_notes(apt)
    return authenticated_client, apt


# ── schema ──
def test_the_table_guards_its_values(db) -> None:
    conn = dbmod.get_db()
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(followups)")}
    assert {
        "appointment_id",
        "patient_id",
        "therapist_id",
        "channel",
        "status",
        "scheduled_for",
        "sent_at",
        "completed_at",
        "pain_level",
        "improvement_rating",
        "side_effects",
        "sleep_quality",
        "adherence",
        "free_text",
        "ai_summary",
        "needs_attention",
        "conversation_json",
        "source",
    } <= columns
    conn.execute(
        "INSERT INTO appointments (patient_id, patient_name, therapist_id, date, time, status) "
        "VALUES (1, 'X', 't', '2026-01-01', '09:00', 'active')"
    )
    base = (
        "INSERT INTO followups (appointment_id, patient_id, status, pain_level, "
        "created_at, updated_at) VALUES (1, 1, ?, ?, 'x', 'x')"
    )
    for status, pain in (("lost", 1), ("sent", 11)):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(base, (status, pain))
    conn.execute(base, ("sent", 0))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(base, ("sent", 3))  # one row per appointment


# ── lifecycle ──
async def test_a_checkin_goes_from_scheduled_to_completed_in_the_database(
    session, fake_telegram, fake_redis
) -> None:
    client, apt = session
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        row = followup_repo.get(apt["id"])
        assert row is not None
        assert (row["status"], row["channel"], row["scheduled_for"]) == (
            "scheduled",
            "telegram",
            "2026-03-02T12:00:00Z",
        )

        frozen.tick(timedelta(hours=24))
        await worker.run_once()
        row = followup_repo.get(apt["id"])
        assert row is not None and row["status"] == "sent" and row["step"] == 1
        assert row["sent_at"] == "2026-03-02T12:00:00Z"
        assert "0–10" in row["conversation"][0]["content"]

        await _answer("6")
        fake_redis.sync.flushall()  # plan 6.1: a flush mid-check-in used to lose the answers
        await _answer("4")
        row = followup_repo.get(apt["id"])
        assert row is not None and row["status"] == "in_progress" and row["step"] == 3
        assert (row["pain_level"], row["improvement_rating"]) == (6, 4)

        await _answer("none")  # side effects
        await _answer("better")  # sleep
        await _answer("yes")  # followed the advice
        frozen.tick(timedelta(minutes=5))
        reply = await _answer("Slept much better")

    assert reply and "thank" in reply.lower()
    row = followup_repo.get(apt["id"])
    assert row is not None
    assert row["status"] == "completed" and row["step"] is None
    assert row["completed_at"] == "2026-03-02T12:05:00Z"
    assert row["free_text"] == "Slept much better" and row["source"] == "patient"
    assert [m["role"] for m in row["conversation"]] == ["ai"] + ["user", "ai"] * 6
    assert (row["side_effects"], row["sleep_quality"], row["adherence"]) == ([], "better", "yes")
    assert row["ai_summary"] and "6/10" in row["ai_summary"] and not row["needs_attention"]
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None, "parallel write for one release"
    assert notes["followup_rating"] == 4
    assert notes["followup_conversation"]["pain_level"] == 6
    assert notes["followup_conversation"]["notes"] == "Slept much better"

    from bot.services.followup_scheduler import consume_followup_conversation

    assert await consume_followup_conversation(PID, "7") == (False, None), "closed now"


async def test_an_unfinished_checkin_expires_and_stops_catching_messages(
    session, fake_telegram
) -> None:
    from bot.services.followup_scheduler import consume_followup_conversation, reconcile

    client, apt = session
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=24))
        await worker.run_once()
        await _answer("5")
        frozen.tick(timedelta(hours=48, minutes=1))
        assert await consume_followup_conversation(PID, "3") == (False, None)
        assert reconcile()["expired"] == 1
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "expired" and row["pain_level"] == 5


async def test_a_manual_booking_is_no_channel_from_the_start(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, patient=make_patient("M", manual=True))
    make_treatment_notes(apt)
    await _complete(authenticated_client, apt)
    row = followup_repo.get(apt["id"])
    assert row is not None and (row["status"], row["channel"]) == ("no_channel", "none")


async def test_completing_again_moves_only_a_pending_checkin(session, fake_telegram) -> None:
    client, apt = session
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=2))
        await _complete(client, apt)
        row = followup_repo.get(apt["id"])
        assert row is not None and row["scheduled_for"] == "2026-03-02T14:00:00Z"
        frozen.tick(timedelta(hours=24))
        await worker.run_once()
        frozen.tick(timedelta(hours=1))
        await _complete(client, apt)
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "sent", "a sent check-in is not reset"
    assert row["scheduled_for"] == "2026-03-02T14:00:00Z"


async def test_the_therapists_entry_becomes_the_record_unless_the_patient_answered(
    session,
) -> None:
    client, apt = session
    url = f"/api/treatment-notes/{PID}/2026-03-01/10-00/manual-feedback"
    assert (await client.post(url, json={"rating": None, "notes": "  "})).status_code == 200
    assert followup_repo.get(apt["id"]) is None, "an empty form is not an outcome"
    resp = await client.post(url, json={"rating": 2, "notes": "Called: still sore"})
    assert resp.status_code == 200
    row = followup_repo.get(apt["id"])
    assert row is not None
    assert (row["status"], row["source"], row["improvement_rating"], row["free_text"]) == (
        "completed",
        "therapist_manual",
        2,
        "Called: still sore",
    )

    dbmod.get_db().execute(
        "UPDATE followups SET source='patient', improvement_rating=5, free_text='great' "
        "WHERE appointment_id=?",
        (apt["id"],),
    )
    await client.post(url, json={"rating": 1, "notes": "overwrite?"})
    row = followup_repo.get(apt["id"])
    assert row is not None and (row["source"], row["improvement_rating"]) == ("patient", 5)


async def test_a_conversation_opened_before_the_table_is_adopted(session, fake_redis) -> None:
    """In flight at deploy time: the state is only in Redis."""
    _client, apt = session
    legacy = {
        "appointment_id": apt["id"],
        "therapist_id": apt["therapist_id"],
        "step": 2,
        "pain_level": 7,
        "conversation": [{"role": "ai", "content": "pain?"}, {"role": "user", "content": "7"}],
    }
    fake_redis.sync.set(f"zenflow:followup:conv:{PID}", json.dumps(legacy))
    with freeze_time(FROZEN):
        await _answer("3")
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "in_progress" and row["step"] == 3
    assert (row["pain_level"], row["improvement_rating"]) == (7, 3)
    assert len(row["conversation"]) == 4
    assert fake_redis.sync.get(f"zenflow:followup:conv:{PID}") is None, "Redis copy dropped"


# ── backfill ──
def _notes(apt: dict[str, Any], **fields: Any) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    dbmod.get_db().execute(
        f"UPDATE treatment_notes SET {sets} WHERE appointment_id=?",  # noqa: S608 - test data
        (*fields.values(), apt["id"]),
    )


def test_backfill_derives_every_state_once(
    make_appointment, make_patient, make_treatment_notes
) -> None:
    conn = dbmod.get_db()
    times = iter(f"{h:02d}:00" for h in range(8, 20))
    apts = {}
    for name in ("answered", "manual", "waiting", "stale", "unsent", "old", "nochannel", "open"):
        patient = make_patient(name, manual=name == "nochannel")
        apts[name] = make_appointment(patient=patient, apt_time=next(times))
        make_treatment_notes(apts[name])
    now = "2026-03-10T12:00:00Z"
    conversation = {
        "pain_level": 3,
        "improvement_rating": 4,
        "notes": "fine",
        "conversation": [{"role": "ai", "content": "hi"}],
    }
    _notes(
        apts["answered"],
        completed_at="2026-03-08T10:00:00Z",
        followup_sent_at="2026-03-09T10:00:00Z",
        followup_conversation=json.dumps(conversation),
        followup_rating=4,
    )
    _notes(
        apts["manual"],
        completed_at="2026-03-01T10:00:00Z",
        manual_feedback_rating=2,
        manual_feedback_notes="phoned",
    )
    _notes(
        apts["waiting"],
        completed_at="2026-03-09T00:00:00Z",
        followup_sent_at="2026-03-10T00:00:00Z",
    )
    _notes(
        apts["stale"], completed_at="2026-03-01T10:00:00Z", followup_sent_at="2026-03-02T10:00:00Z"
    )
    _notes(apts["unsent"], completed_at="2026-03-10T08:00:00Z")
    _notes(apts["old"], completed_at="2026-03-01T08:00:00Z")
    _notes(apts["nochannel"], completed_at="2026-03-10T08:00:00Z")
    # "open": never completed, nothing sent → no row
    conn.execute("DELETE FROM followups")
    before = [dict(r) for r in conn.execute("SELECT * FROM treatment_notes ORDER BY id")]

    assert followup_repo.backfill_from_treatment_notes(conn, now) == 7
    assert followup_repo.backfill_from_treatment_notes(conn, now) == 0, "idempotent"
    assert [dict(r) for r in conn.execute("SELECT * FROM treatment_notes ORDER BY id")] == before

    def row(name: str) -> dict[str, Any]:
        found = followup_repo.get(apts[name]["id"])
        assert found is not None, name
        return found

    answered = row("answered")
    assert (answered["status"], answered["source"]) == ("completed", "patient")
    assert (answered["pain_level"], answered["improvement_rating"], answered["free_text"]) == (
        3,
        4,
        "fine",
    )
    assert answered["conversation"] == [{"role": "ai", "content": "hi"}]
    assert answered["completed_at"] == "2026-03-09T10:00:00Z"
    manual = row("manual")
    assert (manual["status"], manual["source"], manual["improvement_rating"]) == (
        "completed",
        "therapist_manual",
        2,
    )
    assert manual["free_text"] == "phoned"
    assert row("waiting")["status"] == "sent"
    assert row("stale")["status"] == "expired"
    unsent = row("unsent")
    assert (unsent["status"], unsent["scheduled_for"]) == ("scheduled", "2026-03-11T08:00:00Z")
    assert row("old")["status"] == "expired"
    nochannel = row("nochannel")
    assert (nochannel["status"], nochannel["channel"]) == ("no_channel", "none")
    assert followup_repo.get(apts["open"]["id"]) is None


def test_backfill_runs_at_start_up(db, make_appointment, make_treatment_notes) -> None:
    apt = make_appointment()
    make_treatment_notes(apt)
    _notes(apt, completed_at=clock.iso_now())
    dbmod.get_db().execute("DELETE FROM followups")
    dbmod.init_db()
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "scheduled"
