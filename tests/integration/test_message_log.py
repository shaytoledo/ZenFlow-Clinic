"""Plan 8.3 — `message_log` finished: both directions, and the relay.

6.6 recorded what the clinic sends a patient (recommendations, the 24h check-in) and 7.3 added
booking confirmations. What was still invisible is the conversation itself: a patient's message to
their therapist, the therapist's reply, and a patient's answer to a check-in. The log is metadata —
who, when, on which channel, and whether it arrived — never the words themselves.
"""

from __future__ import annotations

import pytest

import bot.db as dbmod
from tests.bot.conftest import FakeBot, FakeMessage, make_context, make_update

pytestmark = pytest.mark.integration

TG = 950_000_001
THERAPIST_TG = 700_001
SECRET = "the pain moved to my left shoulder"


def _rows(kind: str | None = None) -> list[dict]:
    rows = [dict(r) for r in dbmod.get_db().execute("SELECT * FROM message_log ORDER BY id")]
    return [r for r in rows if kind is None or r["kind"] == kind]


@pytest.fixture
def therapist(make_therapist):
    from bot import config as botcfg

    t = make_therapist(name="Dr Lee", therapist_id="t1", telegram_id=THERAPIST_TG)
    botcfg.reload_therapists()
    return t


@pytest.fixture
def therapist_bot(monkeypatch):
    """The client the patient bot forwards through (tests/bot/conftest has the same wiring)."""
    from bot.interfaces import TelegramChannel
    from bot.patient_bot import therapist as pt

    fake = FakeBot()
    monkeypatch.setattr(pt, "_therapist_channel", TelegramChannel(bot=fake))
    return fake


@pytest.fixture
def patient_bot(monkeypatch):
    """The client the therapist bot delivers replies through."""
    import bot.therapist_bot.handlers as th
    from bot.interfaces import TelegramChannel

    fake = FakeBot()
    monkeypatch.setattr(th, "_patient_channel", TelegramChannel(bot=fake))
    return fake


# ── the relay, both ways ──
async def test_a_patients_message_to_their_therapist_is_logged_inbound(
    db, fake_redis, therapist_bot, therapist, make_patient
) -> None:
    from bot.patient_bot import therapist as pt

    patient = make_patient("Dana", telegram_id=TG)
    update = make_update(SECRET, user_id=TG, full_name="Dana Levi")
    await pt.start_relay(update, make_context({"selected_therapist": therapist["id"]}))

    (row,) = _rows("relay")
    assert (row["direction"], row["channel"], row["status"]) == ("in", "telegram", "sent")
    assert row["patient_id"] == patient["patient_id"], "the internal id, never the Telegram one"
    assert row["therapist_id"] == therapist["id"]
    assert row["provider_message_id"], "the forwarded copy, so a reply can be traced back to it"
    assert row["error"] is None


async def test_a_therapists_reply_is_logged_outbound(
    db, fake_redis, patient_bot, therapist, make_patient
) -> None:
    from bot.patient_bot.services import relay as prelay
    from bot.therapist_bot.handlers import _handle_relay

    patient = make_patient("Dana", telegram_id=TG)
    prelay.save_relay_mapping(11, TG, "t1", "Dana")
    await _handle_relay(FakeMessage("rest it and come in Thursday", user_id=THERAPIST_TG), "t1")

    (row,) = _rows("relay")
    assert (row["direction"], row["status"]) == ("out", "sent")
    assert (row["patient_id"], row["therapist_id"]) == (patient["patient_id"], "t1")


async def test_a_relay_that_never_arrived_is_logged_as_failed(
    db, fake_redis, therapist, make_patient, monkeypatch
) -> None:
    from bot.interfaces import TelegramChannel
    from bot.patient_bot import therapist as pt
    from tests.bot.conftest import FakeBot

    make_patient("Dana", telegram_id=TG)
    broken = FakeBot(fail_with=RuntimeError("telegram is down"))
    monkeypatch.setattr(pt, "_therapist_channel", TelegramChannel(bot=broken))

    update = make_update(SECRET, user_id=TG)
    await pt.start_relay(update, make_context({"selected_therapist": therapist["id"]}))

    (row,) = _rows("relay")
    assert (row["direction"], row["status"]) == ("in", "failed")
    assert "telegram is down" in row["error"]
    assert row["provider_message_id"] is None


async def test_the_log_never_holds_what_was_said(
    db, fake_redis, therapist_bot, therapist, make_patient
) -> None:
    """A delivery trail is metadata. The words belong to the conversation, not to a log table."""
    from bot.patient_bot import therapist as pt

    make_patient("Dana", telegram_id=TG)
    update = make_update(SECRET, user_id=TG)
    await pt.start_relay(update, make_context({"selected_therapist": therapist["id"]}))

    (row,) = _rows("relay")
    assert SECRET not in " ".join(str(v) for v in row.values())


async def test_a_message_from_someone_with_no_patient_record_is_still_logged(
    db, fake_redis, therapist_bot, therapist
) -> None:
    """Somebody who never booked writes to the clinic: the attempt is recorded without a patient."""
    from bot.patient_bot import therapist as pt

    update = make_update("hello?", user_id=TG)
    await pt.start_relay(update, make_context({"selected_therapist": therapist["id"]}))

    (row,) = _rows("relay")
    assert row["patient_id"] is None and row["therapist_id"] == therapist["id"]


async def test_a_broken_log_never_costs_a_relayed_message(
    db, fake_redis, therapist_bot, therapist, make_patient, monkeypatch
) -> None:
    import sqlite3

    from web.repositories import message_log_repo

    def _boom(*a, **k):
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(message_log_repo, "record", _boom)
    make_patient("Dana", telegram_id=TG)
    update = make_update(SECRET, user_id=TG)
    from bot.patient_bot import therapist as pt

    await pt.start_relay(update, make_context({"selected_therapist": therapist["id"]}))

    assert len(therapist_bot.sent) == 1, "the therapist still got the message"
    assert "could not reach" not in " ".join(update.message.reply_texts()).lower()


async def test_a_reply_typed_on_the_messages_page_is_logged_too(
    authenticated_client, make_patient, fake_telegram
) -> None:
    """The dashboard is a third way to answer a patient; the trail must not depend on the door."""
    from bot.patient_bot.services import relay as prelay

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=TG)
    prelay.save_relay_mapping(12, TG, tid, "Dana")

    resp = await authenticated_client.post(
        "/api/messages/send", json={"patient_id": TG, "text": "see you Thursday"}
    )
    assert resp.status_code == 200, resp.text

    (row,) = _rows("relay")
    assert (row["direction"], row["status"]) == ("out", "sent")
    assert (row["patient_id"], row["therapist_id"]) == (patient["patient_id"], tid)
    assert "see you Thursday" not in str(row)


# ── the patient answering a check-in ──
async def test_a_checkin_answer_is_logged_inbound(
    db, fake_redis, make_completed_session, make_patient
) -> None:
    from bot.services.followup_scheduler import consume_followup_conversation
    from web.repositories import followup_repo
    from zenflow import clock

    patient = make_patient("Dana", telegram_id=TG)
    apt = make_completed_session(patient=patient)
    followup_repo.schedule(apt["id"], clock.iso_now())
    followup_repo.mark_sent(apt["id"], [])

    consumed, _prompt = await consume_followup_conversation(TG, "5")
    assert consumed

    inbound = [r for r in _rows("followup") if r["direction"] == "in"]
    assert len(inbound) == 1
    assert inbound[0]["appointment_id"] == apt["id"]
    assert inbound[0]["patient_id"] == patient["patient_id"]
    assert inbound[0]["status"] == "sent", "a received message arrived by definition"


# ── what the therapist sees ──
async def test_the_session_log_shows_both_directions(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    from web.repositories import message_log_repo

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=TG)
    apt = make_appointment(therapist={"id": tid}, patient=patient, apt_date="2026-03-12")
    make_treatment_notes(apt)
    common = {
        "channel": "telegram",
        "therapist_id": tid,
        "patient_id": patient["patient_id"],
        "appointment_id": apt["id"],
        "status": "sent",
    }
    message_log_repo.record(kind="followup", **common)
    for _ in range(3):
        message_log_repo.record(kind="followup", direction="in", **common)

    page = (await authenticated_client.get(f"/treatment/{apt['patient_id']}/2026-03-12/10-00")).text
    assert "dl_" not in page, "every label is translated, not a key"
    assert "Received" in page and "×3" in page, "three answers, as one line"


async def test_a_confirmation_reads_as_words_not_a_key(
    authenticated_client, make_appointment, make_patient, make_treatment_notes
) -> None:
    """7.3 started logging booking confirmations; the card had no wording for them."""
    from web.repositories import message_log_repo

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    patient = make_patient("Dana", telegram_id=TG)
    apt = make_appointment(therapist={"id": tid}, patient=patient, apt_date="2026-03-12")
    make_treatment_notes(apt)
    message_log_repo.record(
        channel="whatsapp",
        kind="confirmation",
        status="sent",
        therapist_id=tid,
        patient_id=patient["patient_id"],
        appointment_id=apt["id"],
    )

    page = (await authenticated_client.get(f"/treatment/{apt['patient_id']}/2026-03-12/10-00")).text
    assert "dl_kind_confirmation" not in page and "dl_channel_whatsapp" not in page
    assert "WhatsApp" in page


# ── the kinds a database written before 8.3 allows ──
def test_an_older_table_learns_the_relay_kind(db, tmp_path) -> None:
    import sqlite3

    from web.repositories import message_log_repo as repo

    old = sqlite3.connect(tmp_path / "old.db")
    old.row_factory = sqlite3.Row
    old.execute("""CREATE TABLE message_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'out' CHECK (direction IN ('out','in')),
            channel TEXT NOT NULL CHECK (channel IN ('telegram','whatsapp','email')),
            patient_id INTEGER, therapist_id TEXT NOT NULL DEFAULT '', appointment_id INTEGER,
            kind TEXT NOT NULL CHECK (kind IN ('recommendations','followup','confirmation')),
            status TEXT NOT NULL CHECK (status IN ('sent','failed')),
            provider_message_id TEXT, error TEXT)""")
    old.execute("""INSERT INTO message_log (ts, channel, kind, status)
           VALUES ('2026-03-01T10:00:00Z', 'telegram', 'followup', 'sent')""")

    repo.create_schema(old)
    repo.create_schema(old)  # a second start-up changes nothing

    old.execute("""INSERT INTO message_log (ts, direction, channel, kind, status)
           VALUES ('2026-03-02T10:00:00Z', 'in', 'telegram', 'relay', 'sent')""")
    kinds = [r["kind"] for r in old.execute("SELECT kind FROM message_log ORDER BY id")]
    assert kinds == ["followup", "relay"], "the old row survived the rebuild"
    migrations = old.execute(
        "SELECT COUNT(*) c FROM schema_migrations WHERE name=?", (repo.RELAY_MIGRATION,)
    ).fetchone()
    assert migrations["c"] == 1
