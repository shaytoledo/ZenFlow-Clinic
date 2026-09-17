"""Plan 7.4b — WhatsApp's webhook: the patient's side of the channel.

Meta delivers every inbound message and delivery receipt to one endpoint. It is off with the
flag, refuses anything it cannot verify, answers the same check-in logic the Telegram handler
uses, and never acts on the same delivery twice (Meta retries).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod

pytestmark = pytest.mark.integration

WA = "972500000055"
APP_SECRET = "webhook-app-secret-0123456789"
VERIFY = "verify-me-please"
PHONE_ID = "106540352242922"
FROZEN = "2026-03-01T12:00:00Z"
PATH = "/api/webhooks/whatsapp"


def _q(sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute(sql, args)]


@pytest.fixture(autouse=True)
def whatsapp_on(db, monkeypatch):
    from zenflow import settings as S

    monkeypatch.setenv("ZF_CHANNEL_WHATSAPP", "1")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setenv("WHATSAPP_TOKEN", "EAAtest-token")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", VERIFY)
    S.reset_settings()
    yield
    S.reset_settings()


@pytest.fixture
def cloud(monkeypatch):
    """The Cloud API, offline — what the clinic sends back lands here."""
    import bot.interfaces.whatsapp_channel as wa
    from tests.contract.test_whatsapp_channel import FakeCloudApi

    api = FakeCloudApi()
    monkeypatch.setattr(wa, "_transport", api.transport())
    return api


def _signed(body: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    digest = hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return raw, {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def _inbound(text: str | None = None, *, message_id: str = "wamid.1", **extra: Any) -> dict:
    message: dict[str, Any] = {
        "from": WA,
        "id": message_id,
        "timestamp": "1772366400",
        "type": "text",
    }
    if text is not None:
        message["text"] = {"body": text}
    message.update(extra)
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": PHONE_ID},
                            "contacts": [{"wa_id": WA, "profile": {"name": "Dana Levi"}}],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


def _receipt(status: str, message_id: str = "wamid.out-1", error: str | None = None) -> dict:
    entry: dict[str, Any] = {
        "id": message_id,
        "status": status,
        "recipient_id": WA,
        "timestamp": "1772366400",
    }
    if error:
        entry["errors"] = [{"title": error}]
    return {
        "object": "whatsapp_business_account",
        "entry": [{"changes": [{"field": "messages", "value": {"statuses": [entry]}}]}],
    }


@pytest.fixture
async def open_checkin(db, make_therapist, make_appointment, make_patient, fake_redis):
    """A patient on WhatsApp whose check-in step 1 has gone out."""
    from web.repositories import followup_repo, patient_repo
    from zenflow import clock

    therapist = make_therapist(therapist_id="t1", name="Dr One")
    patient = make_patient("Dana Levi", manual=True)
    patient_repo.link_channel(patient["patient_id"], "whatsapp", WA)
    apt = make_appointment(therapist=therapist, patient=patient, apt_date="2026-02-28")
    followup_repo.schedule(apt["id"], clock.iso_now())
    followup_repo.mark_sent(apt["id"], [])
    return apt


# ── the endpoint is off without the flag ──
async def test_the_endpoint_is_absent_while_the_channel_is_off(client, monkeypatch) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("ZF_CHANNEL_WHATSAPP", "0")
    S.reset_settings()
    raw, headers = _signed(_inbound("hello"))
    assert (await client.post(PATH, content=raw, headers=headers)).status_code == 404
    assert (await client.get(f"{PATH}?hub.mode=subscribe")).status_code == 404


# ── Meta's subscription handshake ──
async def test_the_handshake_echoes_the_challenge(client) -> None:
    resp = await client.get(
        PATH,
        params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1158201444"},
    )
    assert resp.status_code == 200 and resp.text == "1158201444"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "1"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "1"},
        {"hub.challenge": "1"},
    ],
)
async def test_a_bad_handshake_is_refused(client, params: dict) -> None:
    assert (await client.get(PATH, params=params)).status_code == 403


# ── authenticity ──
async def test_an_unsigned_delivery_is_refused(client, open_checkin) -> None:
    raw = json.dumps(_inbound("5")).encode()
    resp = await client.post(PATH, content=raw, headers={"Content-Type": "application/json"})
    assert resp.status_code == 401
    from web.repositories import followup_repo

    assert (followup_repo.get(open_checkin["id"]) or {})["pain_level"] is None


async def test_a_tampered_delivery_is_refused(client, open_checkin) -> None:
    raw, headers = _signed(_inbound("5"))
    resp = await client.post(PATH, content=raw + b" ", headers=headers)
    assert resp.status_code == 401


# ── the check-in ──
async def test_a_typed_answer_advances_the_checkin_and_is_answered(
    client, open_checkin, cloud
) -> None:
    """The reply goes out inside the window the patient just opened by writing."""
    from web.repositories import followup_repo

    raw, headers = _signed(_inbound("5"))
    resp = await client.post(PATH, content=raw, headers=headers)

    assert resp.status_code == 200
    state = followup_repo.get(open_checkin["id"])
    assert state is not None and state["pain_level"] == 5 and state["step"] == 2
    sent = cloud.of("/messages")
    assert sent, "the next question went back to the patient"
    assert sent[-1]["to"] == WA


async def test_a_tapped_option_advances_the_checkin(client, open_checkin, cloud) -> None:
    from bot.services import followup_checkin as fc
    from web.repositories import followup_repo

    payload = _inbound(
        None,
        message_id="wamid.tap",
        type="interactive",
        interactive={
            "type": "list_reply",
            "list_reply": {"id": fc.callback(open_checkin["id"], 1, "7"), "title": "7"},
        },
    )
    raw, headers = _signed(payload)
    assert (await client.post(PATH, content=raw, headers=headers)).status_code == 200
    assert (followup_repo.get(open_checkin["id"]) or {})["pain_level"] == 7


async def test_meta_may_deliver_the_same_message_twice(client, open_checkin, cloud) -> None:
    """Meta retries until it gets a 200: the answer must count once."""
    raw, headers = _signed(_inbound("5"))
    first = await client.post(PATH, content=raw, headers=headers)
    second = await client.post(PATH, content=raw, headers=headers)

    assert first.status_code == second.status_code == 200
    from web.repositories import followup_repo

    state = followup_repo.get(open_checkin["id"])
    assert state is not None and state["step"] == 2, "the second delivery changed nothing"
    assert len(cloud.of("/messages")) == 1, "and asked nothing twice"


async def test_a_message_from_a_stranger_is_accepted_and_ignored(client, cloud) -> None:
    raw, headers = _signed(_inbound("hello?"))
    resp = await client.post(PATH, content=raw, headers=headers)
    assert resp.status_code == 200, "a 200 stops Meta retrying something we cannot use"
    assert cloud.of("/messages") == []


async def test_an_inbound_message_opens_the_service_window(
    client, open_checkin, fake_redis
) -> None:
    from bot.interfaces.whatsapp_channel import window_key

    raw, headers = _signed(_inbound("5"))
    await client.post(PATH, content=raw, headers=headers)

    remembered = json.loads(await fake_redis.async_.get(window_key(WA)))
    assert remembered["id"] == "wamid.1"


# ── delivery receipts ──
async def test_a_failed_receipt_is_recorded(client, open_checkin) -> None:
    from web.repositories import message_log_repo

    message_log_repo.record(
        channel="whatsapp",
        kind="followup",
        status="sent",
        therapist_id=open_checkin["therapist_id"],
        patient_id=open_checkin["patient_id"],
        appointment_id=open_checkin["id"],
        provider_message_id="wamid.out-1",
    )
    raw, headers = _signed(_receipt("failed", error="Message undeliverable"))

    assert (await client.post(PATH, content=raw, headers=headers)).status_code == 200

    rows = _q("SELECT channel, kind, status, error, provider_message_id FROM message_log")
    assert len(rows) == 2
    assert rows[-1]["status"] == "failed" and rows[-1]["channel"] == "whatsapp"
    assert rows[-1]["provider_message_id"] == "wamid.out-1"
    assert "undeliverable" in (rows[-1]["error"] or "")


async def test_a_delivered_receipt_is_not_noise(client, open_checkin) -> None:
    raw, headers = _signed(_receipt("delivered"))
    assert (await client.post(PATH, content=raw, headers=headers)).status_code == 200
    assert _q("SELECT id FROM message_log") == []


# ── the closed window uses a template ──
async def test_the_checkin_falls_back_to_a_template(
    db,
    make_therapist,
    make_appointment,
    make_patient,
    make_treatment_notes,
    cloud,
    fake_redis,
    monkeypatch,
) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("WHATSAPP_TEMPLATE_FOLLOWUP", "followup_checkin")
    S.reset_settings()

    from bot.services.followup_scheduler import _send_followup
    from web.repositories import patient_repo, treatment_repo

    therapist = make_therapist(therapist_id="t1", name="Dr One")
    patient = make_patient("Dana Levi", manual=True)
    patient_repo.link_channel(patient["patient_id"], "whatsapp", WA)
    apt = make_appointment(therapist=therapist, patient=patient, apt_date="2026-02-28")
    make_treatment_notes(apt, completed_at=FROZEN)

    from web.repositories import followup_repo
    from zenflow import clock

    with freeze_time(FROZEN) as frozen:
        followup_repo.schedule(apt["id"], clock.iso_now())  # as "Complete Session" does
        from bot.interfaces.whatsapp_channel import remember_inbound

        await remember_inbound(WA, "wamid.old")  # their last message…
        frozen.tick(timedelta(hours=24, minutes=1))  # …is a day old: the window has closed
        row = treatment_repo.get_followup_candidate(apt["id"])
        assert row is not None
        await _send_followup(row, raise_errors=True)

    (payload,) = cloud.of("/messages")
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "followup_checkin"
    assert (followup_repo.get(apt["id"]) or {})["status"] == "sent", "the check-in is open"
