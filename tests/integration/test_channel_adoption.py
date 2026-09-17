"""Plan 7.1 — every Telegram call the web layer makes goes through the channel adapter.

Before 7.1 the reply echo and the bot-name lookups posted to api.telegram.org themselves, so
tests reached the real network with a fake token and nothing recorded what was sent.
"""

from __future__ import annotations

import json

import pytest

from bot.patient_bot.services import relay

pytestmark = pytest.mark.integration

PATIENT = 930_000_001
THERAPIST_TG = 730_001
PW = "pw-Test-123"


@pytest.fixture
def therapist(db, make_therapist):
    from bot import config as botcfg

    t = make_therapist(
        name="Dr Echo",
        email="echo@example.com",
        password=PW,
        telegram_id=THERAPIST_TG,
        therapist_id="t1",
    )
    botcfg.reload_therapists()
    return t


@pytest.fixture
def bot_names(monkeypatch):
    """The page helpers cache the bot names per process — start each test without them."""
    import web.deps as deps

    monkeypatch.setattr(deps, "_therapist_bot_username", "")
    monkeypatch.setattr(deps, "_patient_bot_username", "")


async def test_a_web_reply_and_its_echo_both_go_through_the_channel(
    fake_redis, fake_telegram, therapist, login_as
) -> None:
    relay.save_relay_mapping(90, PATIENT, "t1", "Pat")
    client = await login_as(therapist)

    resp = await client.post(
        "/api/messages/send", json={"patient_id": PATIENT, "text": "see you_soon"}
    )

    assert resp.status_code == 200, resp.text
    to_patient, echo = fake_telegram.of("sendMessage")
    assert (to_patient.bot, to_patient.params) == (
        "patient",
        {"chat_id": PATIENT, "text": "👨‍⚕️ Dr Echo:\nsee you_soon"},
    )
    assert (echo.bot, echo.params) == (
        "therapist",
        {
            "chat_id": THERAPIST_TG,
            "text": "💬 Sent via web:\nsee you_soon",
            "reply_parameters": {"message_id": 90, "allow_sending_without_reply": True},
        },
    )


async def test_an_echo_failure_does_not_fail_the_reply(
    fake_redis, fake_telegram, therapist, login_as
) -> None:
    relay.save_relay_mapping(91, PATIENT, "t1", "Pat")
    client = await login_as(therapist)
    fake_telegram.fail_next("Forbidden: bot was blocked by the user", bot="therapist")

    resp = await client.post("/api/messages/send", json={"patient_id": PATIENT, "text": "b"})

    assert resp.status_code == 200
    assert [(c.bot, c.ok) for c in fake_telegram.api_calls] == [
        ("patient", True),
        ("therapist", False),
    ]
    stored = json.loads(fake_redis.sync.get(f"zenflow:relay:history:t1:{PATIENT}"))
    assert stored[-1]["text"] == "b", "the delivered reply is in the history"


async def test_a_rate_limited_delivery_says_why(
    fake_redis, fake_telegram, therapist, login_as
) -> None:
    relay.save_relay_mapping(93, PATIENT, "t1", "Pat")
    client = await login_as(therapist)
    fake_telegram.fail_next("Too Many Requests: retry after 3", status=429, retry_after=3)
    resp = await client.post("/api/messages/send", json={"patient_id": PATIENT, "text": "a"})
    assert resp.status_code == 500
    assert "Too Many Requests" in resp.json()["detail"]


async def test_a_failed_delivery_stores_nothing_and_echoes_nothing(
    fake_redis, fake_telegram, therapist, login_as
) -> None:
    relay.save_relay_mapping(92, PATIENT, "t1", "Pat")
    client = await login_as(therapist)
    before = fake_redis.sync.get(f"zenflow:relay:history:t1:{PATIENT}")
    fake_telegram.fail_next("Forbidden: bot was blocked by the user")

    resp = await client.post("/api/messages/send", json={"patient_id": PATIENT, "text": "hi"})

    assert resp.status_code == 500
    assert "blocked" in resp.json()["detail"]
    assert len(fake_telegram.api_calls) == 1, "no echo for an undelivered reply"
    assert fake_redis.sync.get(f"zenflow:relay:history:t1:{PATIENT}") == before
    assert "TEST-PATIENT-BOT-TOKEN" not in resp.text


async def test_pages_learn_the_bot_names_offline(
    fake_telegram, bot_names, authenticated_client
) -> None:
    resp = await authenticated_client.get("/api/my/activation-code")

    assert resp.status_code == 200
    body = resp.json()
    assert body["therapist_bot_username"] == body["patient_bot_username"] == "zf_bot"
    assert body["patient_bot_link"] == "https://t.me/zf_bot"
    assert {(c.bot, c.method) for c in fake_telegram.api_calls} == {
        ("patient", "getMe"),
        ("therapist", "getMe"),
    }


async def test_an_unreachable_bot_leaves_the_name_empty(
    fake_telegram, bot_names, authenticated_client
) -> None:
    fake_telegram.down = True
    body = (await authenticated_client.get("/api/my/activation-code")).json()
    assert body["therapist_bot_username"] == "" and body["therapist_bot_link"] == ""


async def test_status_checks_both_bots_through_the_channel(
    fake_telegram, authenticated_client
) -> None:
    body = (await authenticated_client.get("/api/status")).json()
    assert body["patient_bot"] == {"ok": True, "label": "Patient Bot", "detail": "@zf_bot"}
    assert body["therapist_bot"] == {"ok": True, "label": "Therapist Bot", "detail": "@zf_bot"}

    fake_telegram.fail_next("Unauthorized", status=401, bot="patient")
    body = (await authenticated_client.get("/api/status")).json()
    assert body["patient_bot"] == {"ok": False, "label": "Patient Bot", "detail": "Unauthorized"}
    assert body["therapist_bot"]["ok"] is True

    fake_telegram.down = True
    body = (await authenticated_client.get("/api/status")).json()
    assert body["therapist_bot"] == {
        "ok": False,
        "label": "Therapist Bot",
        "detail": "Unreachable",
    }
    assert "TEST-" not in json.dumps(body), "no token in the status payload"


async def test_without_fake_telegram_nothing_reaches_the_network(
    bot_names, authenticated_client
) -> None:
    """The autouse guard now sits under every Telegram call, getMe included."""
    body = (await authenticated_client.get("/api/my/activation-code")).json()
    assert body["patient_bot_username"] == ""
