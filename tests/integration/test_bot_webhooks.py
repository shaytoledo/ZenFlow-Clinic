"""Phase 12.2.5 — the bots receive Telegram updates by webhook (ZF_WEBHOOK_MODE=1), safely.

The bots process serves its own small HTTP app (`bot/webhooks.py`): /healthz always, and one
POST route per bot in webhook mode. An update is accepted only with that bot's secret token
(derived per bot from TELEGRAM_WEBHOOK_SECRET); it is then queued exactly as polling would.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from telegram import Bot, Update

from bot.interfaces.telegram_channel import SECRET_HEADER
from bot.webhooks import MAX_BODY_BYTES, bot_secret, build_app

pytestmark = pytest.mark.integration

MASTER = "m" * 48
UPDATE = {
    "update_id": 1001,
    "message": {
        "message_id": 7,
        "date": 1_700_000_000,
        "chat": {"id": 555, "type": "private"},
        "from": {"id": 555, "is_bot": False, "first_name": "Pat"},
        "text": "hello",
    },
}


class FakeApplication:
    """The two things the endpoint uses: the bot (to bind the update) and the update queue."""

    def __init__(self, running: bool = True) -> None:
        self.bot = Bot("1000000000:TEST-PATIENT-BOT-TOKEN-xxxxxxxxxxxxxxx")
        self.update_queue: asyncio.Queue[Any] = asyncio.Queue()
        self.running = running


def _client(apps: dict[str, Any], *, webhook_mode: bool = True) -> httpx.AsyncClient:
    app = build_app(apps, webhook_mode=webhook_mode, master_secret=MASTER)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bots")


def _headers(name: str = "patient", master: str = MASTER) -> dict[str, str]:
    return {SECRET_HEADER: bot_secret(name, master)}


async def test_a_verified_update_is_queued_like_polling_would() -> None:
    patient = FakeApplication()
    async with _client({"patient": patient}) as c:
        r = await c.post("/telegram/patient", json=UPDATE, headers=_headers())
    assert r.status_code == 200
    update = patient.update_queue.get_nowait()
    assert isinstance(update, Update) and update.update_id == 1001
    assert update.message is not None and update.message.text == "hello"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {SECRET_HEADER: ""},
        {SECRET_HEADER: "guess"},
        {SECRET_HEADER: bot_secret("therapist", MASTER)},  # the OTHER bot's token
        {SECRET_HEADER: bot_secret("patient", "another-master-" + "x" * 40)},
    ],
)
async def test_without_this_bots_secret_nothing_is_queued(headers: dict[str, str]) -> None:
    patient = FakeApplication()
    async with _client({"patient": patient, "therapist": FakeApplication()}) as c:
        r = await c.post("/telegram/patient", json=UPDATE, headers=headers)
    assert r.status_code == 403
    assert patient.update_queue.empty()


async def test_polling_mode_has_no_webhook_routes() -> None:
    patient = FakeApplication()
    async with _client({"patient": patient}, webhook_mode=False) as c:
        r = await c.post("/telegram/patient", json=UPDATE, headers=_headers())
        health = await c.get("/healthz")
    assert r.status_code == 404 and patient.update_queue.empty()
    assert health.status_code == 200 and health.json() == {
        "ok": True,
        "mode": "polling",
        "down": [],
    }


async def test_an_unknown_bot_is_not_found() -> None:
    async with _client({"patient": FakeApplication()}) as c:
        r = await c.post("/telegram/admin", json=UPDATE, headers=_headers("admin"))
    assert r.status_code == 404


async def test_unreadable_and_oversized_bodies_are_refused() -> None:
    patient = FakeApplication()
    async with _client({"patient": patient}) as c:
        bad = await c.post("/telegram/patient", content=b"{not json", headers=_headers())
        big = await c.post(
            "/telegram/patient",
            content=json.dumps({**UPDATE, "pad": "x" * MAX_BODY_BYTES}).encode(),
            headers=_headers(),
        )
    assert bad.status_code == 400 and big.status_code == 413
    assert patient.update_queue.empty()


async def test_health_is_503_while_a_bot_is_down() -> None:
    async with _client({"patient": FakeApplication(), "therapist": FakeApplication(False)}) as c:
        r = await c.get("/healthz")
    assert r.status_code == 503 and r.json()["down"] == ["therapist"]


def test_each_bot_has_its_own_secret_token() -> None:
    patient, therapist = bot_secret("patient", MASTER), bot_secret("therapist", MASTER)
    assert patient != therapist and len(patient) == 64
    assert set(patient) <= set("0123456789abcdef"), "Telegram allows only A-Z a-z 0-9 _ -"
    assert MASTER not in patient


async def test_webhook_mode_registers_each_bot_with_its_url_and_secret(
    fake_telegram, monkeypatch
) -> None:
    from telegram.ext import Application

    from bot.main import _start_receiving
    from zenflow.settings import get_settings, reset_settings

    monkeypatch.setenv("ZF_WEBHOOK_MODE", "1")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", MASTER)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://bots.clinic.example/")
    reset_settings()
    apps = {
        name: Application.builder()
        .token(token)
        .request(fake_telegram.request())
        .get_updates_request(fake_telegram.request())
        .build()
        for name, token in (
            ("patient", "1000000000:TEST-PATIENT-BOT-TOKEN-xxxxxxxxxxxxxxx"),
            ("therapist", "2000000000:TEST-THERAPIST-BOT-TOKEN-xxxxxxxxxxxxx"),
        )
    }
    for app in apps.values():
        await app.initialize()
    try:
        await _start_receiving(apps, get_settings())
    finally:
        for app in apps.values():
            await app.shutdown()
    calls = [c for c in fake_telegram.api_calls if c.method == "setWebhook"]
    assert [c.params["url"] for c in calls] == [
        "https://bots.clinic.example/telegram/patient",
        "https://bots.clinic.example/telegram/therapist",
    ]
    assert [c.params["secret_token"] for c in calls] == [
        bot_secret("patient", MASTER),
        bot_secret("therapist", MASTER),
    ]
    assert not [c for c in fake_telegram.api_calls if c.method == "getUpdates"], "no polling"


async def test_the_bots_process_serves_health_and_shuts_down_cleanly(
    fake_telegram, monkeypatch
) -> None:
    """`_run` end to end, with uvicorn's serve() replaced by one /healthz request then a stop."""
    import uvicorn
    from telegram.ext import Application

    from bot import main
    from zenflow.settings import reset_settings

    monkeypatch.setenv("ZF_WEBHOOK_MODE", "1")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", MASTER)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_URL", "https://bots.clinic.example")
    reset_settings()
    seen: dict[str, Any] = {}

    async def serve(self: uvicorn.Server) -> None:  # instead of listening on a socket
        seen["host"], seen["port"] = self.config.host, self.config.port
        seen["access_log"] = self.config.access_log
        transport = httpx.ASGITransport(app=self.config.app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://bots") as c:
            seen["health"] = (await c.get("/healthz")).json()

    monkeypatch.setattr(uvicorn.Server, "serve", serve)

    def app(token: str) -> Application:
        return Application.builder().token(token).request(fake_telegram.request()).build()

    patient = app("1000000000:TEST-PATIENT-BOT-TOKEN-xxxxxxxxxxxxxxx")
    therapist = app("2000000000:TEST-THERAPIST-BOT-TOKEN-xxxxxxxxxxxxx")
    await main._run(patient, therapist)

    assert seen["health"] == {"ok": True, "mode": "webhook", "down": []}
    assert (seen["host"], seen["port"], seen["access_log"]) == ("127.0.0.1", 8081, False)
    assert len(fake_telegram.of("setWebhook")) == 2
    assert not patient.running and not therapist.running, "stopped after the server returned"
