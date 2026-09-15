"""ZenFlow test harness (Phase 0.3 of docs/MASTER_PLAN_EN.md).

Order matters: `bot.config` opens the SQLite database and reads every env var AT IMPORT TIME, so
the environment is pinned at the top of this module, before any project import can happen.

Fixtures
--------
db                    fresh SQLite file per test (schema from bot.db.init_db)        [autouse]
fake_redis            fakeredis sync + async clients patched into bot.redis_client   [autouse]
client                httpx.AsyncClient over ASGITransport against web.app:app
authenticated_client  same, signed in through the real /register/signin form
frozen_clock          freezegun at 2026-03-01T12:00:00 (tick() to advance)
fake_telegram         records every outbound Telegram sendMessage instead of calling the API
fake_llm              canned question / diagnosis / points answers instead of Ollama
make_therapist / make_patient / make_appointment / make_treatment_notes / make_completed_session
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── 1. Pin the environment BEFORE any project import ─────────────────────────────────────────
_SESSION_TMP = Path(tempfile.mkdtemp(prefix="zenflow-tests-"))
_TEST_ENV = {
    "ENV": "test",
    "ZENFLOW_DOTENV": "0",  # never read the developer's real .env in tests
    "ZENFLOW_DB_PATH": str(_SESSION_TMP / "bootstrap.db"),
    "SESSION_SECRET": "test-only-session-secret-0123456789abcdef0123456789abcdef",
    "TELEGRAM_TOKEN": "1000000000:TEST-PATIENT-BOT-TOKEN-xxxxxxxxxxxxxxx",
    "THERAPIST_BOT_TOKEN": "2000000000:TEST-THERAPIST-BOT-TOKEN-xxxxxxxxxxxxx",
    "USE_AI": "ollama",
    "OLLAMA_HOST": "http://127.0.0.1:9",  # discard port: connection refused instantly
    "OLLAMA_MODEL": "fake-model",
    "REDIS_URL": "redis://127.0.0.1:9/0",  # never reached — fakeredis is patched in
    "MESSAGING_CHANNEL": "telegram",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "ANTHROPIC_API_KEY": "",
}
os.environ.update(_TEST_ENV)

import fakeredis  # noqa: E402
import httpx  # noqa: E402
import pytest  # noqa: E402
from freezegun import freeze_time  # noqa: E402

import bot.db as dbmod  # noqa: E402  (imports bot.db only — bot.config is imported lazily below)

_REAL_DB = (Path(dbmod.__file__).resolve().parent.parent / "data" / "zenflow.db").resolve()


def _assert_not_real_db() -> None:
    assert dbmod.db_path().resolve() != _REAL_DB, "tests must never touch data/zenflow.db"


_assert_not_real_db()


# ── 2. Database ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A fresh, fully-migrated SQLite file for every test."""
    path = tmp_path / "zenflow.db"
    monkeypatch.setenv("ZENFLOW_DB_PATH", str(path))
    dbmod.close_db()
    _assert_not_real_db()
    dbmod.init_db()
    # bot.config caches the therapist registry at import time — refresh it for the new file.
    from bot import config as botcfg

    botcfg.reload_therapists()
    yield path
    dbmod.close_db()


@pytest.fixture(autouse=True, scope="session")
def _structured_logging_installed() -> None:
    """Install zenflow's log-record factory once, so record context fields (request_id, job, …)
    exist in every test regardless of which module imported web.app first."""
    from zenflow import logging as zlog

    zlog.configure_logging("test", fmt="console", install_file_handler=False)


@pytest.fixture(autouse=True)
def _fresh_settings_after_each_test() -> Iterator[None]:
    """A test that monkeypatches env + reset_settings() must not leak its Settings into the next."""
    yield
    from zenflow.settings import reset_settings

    reset_settings()


# ── 3. Redis ─────────────────────────────────────────────────────────────────────────────────
class FakeRedisPair:
    def __init__(self) -> None:
        self.server = fakeredis.FakeServer()
        self.sync = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        self.async_ = fakeredis.FakeAsyncRedis(server=self.server, decode_responses=True)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedisPair:
    """Sync and async fakeredis clients sharing one in-memory server, patched into the singletons."""
    import bot.redis_client as rc

    pair = FakeRedisPair()
    monkeypatch.setattr(rc, "_sync_client", pair.sync)
    monkeypatch.setattr(rc, "_async_client", pair.async_)
    return pair


# ── 4. Web client ────────────────────────────────────────────────────────────────────────────
@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    from web.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=False
    ) as c:
        yield c


@pytest.fixture
async def authenticated_client(client: httpx.AsyncClient, make_therapist) -> httpx.AsyncClient:
    """`client` signed in as a seeded, active therapist via the real sign-in form."""
    therapist = make_therapist(email="therapist@example.com", password="pw-Test-123", active=True)
    resp = await client.post(
        "/register/signin",
        data={"email": therapist["email"], "password": therapist["password"]},
    )
    assert resp.status_code in (
        302,
        303,
        307,
    ), f"sign-in failed: {resp.status_code} {resp.text[:200]}"
    assert "zf_session" in client.cookies, "sign-in did not set the session cookie"
    client.headers["X-Test-Therapist-Id"] = therapist["id"]  # convenience for assertions
    return client


@pytest.fixture
async def login_as():
    """Factory: a brand-new client (own cookie jar) signed in as the given therapist dict.

    The therapist must have been created with `make_therapist(email=..., password=...)`.
    Used by multi-tenant tests that need two sessions side by side.
    """
    from web.app import app

    clients: list[httpx.AsyncClient] = []

    async def _login(therapist: dict[str, Any]) -> httpx.AsyncClient:
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            follow_redirects=False,
        )
        clients.append(c)
        resp = await c.post(
            "/register/signin",
            data={"email": therapist["email"], "password": therapist["password"]},
        )
        assert resp.status_code in (302, 303, 307), f"sign-in failed: {resp.status_code}"
        assert "zf_session" in c.cookies
        return c

    yield _login
    for c in clients:
        await c.aclose()


# ── 5. Clock ─────────────────────────────────────────────────────────────────────────────────
FROZEN_AT = "2026-03-01T12:00:00"


@pytest.fixture
def frozen_clock() -> Iterator[Any]:
    # itsdangerous keeps real time so session cookies signed before/after freezing stay valid
    # regardless of fixture order.
    with freeze_time(FROZEN_AT, ignore=["itsdangerous"]) as freezer:
        yield freezer


# ── 6. Telegram ──────────────────────────────────────────────────────────────────────────────
class FakeTelegram:
    """Records outbound sendMessage calls made through web.services.telegram_service."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._next_id = 100

    def _label(self, token: str) -> str:
        if token == os.environ["TELEGRAM_TOKEN"]:
            return "patient"
        if token == os.environ["THERAPIST_BOT_TOKEN"]:
            return "therapist"
        return "unknown"

    async def send(self, token: str, chat_id: int, text: str, parse_mode: str) -> dict[str, Any]:
        self.calls.append(
            {"bot": self._label(token), "chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        )
        self._next_id += 1
        return {"ok": True, "result": {"message_id": self._next_id, "chat": {"id": chat_id}}}


@pytest.fixture(autouse=True)
def block_real_telegram(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may reach api.telegram.org. Request `fake_telegram` to record sends instead."""
    import web.services.telegram_service as ts

    async def _blocked(token: str, chat_id: int, text: str, parse_mode: str) -> dict[str, Any]:
        raise RuntimeError(
            "real Telegram send attempted in a test — use the `fake_telegram` fixture"
        )

    monkeypatch.setattr(ts, "_send", _blocked)


@pytest.fixture
def fake_telegram(monkeypatch: pytest.MonkeyPatch) -> FakeTelegram:
    import web.services.telegram_service as ts

    fake = FakeTelegram()
    monkeypatch.setattr(ts, "_send", fake.send)
    return fake


# ── 7. LLM ───────────────────────────────────────────────────────────────────────────────────
CANNED_QUESTION = "How long have you had the headache, and where exactly is the pain?"
CANNED_SUMMARY = "Patient reports a 3-day right-sided headache, worse with stress, sleep disturbed."
CANNED_DIAGNOSIS = {
    "tcm_pattern": "Liver Yang Rising",
    "treatment_principles": "Subdue Liver Yang, nourish Kidney Yin, calm the Shen",
    "diagnosis_certainty": 72,
    "recommendations": {
        "diet": "Avoid alcohol and greasy food; add leafy greens.",
        "sleep": "Lights out by 23:00; no screens in bed.",
        "exercise": "Gentle walking or qigong daily.",
        "stress": "Two short breathing breaks per day.",
    },
}
CANNED_POINTS = [
    {"code": "LR3", "name": "Taichong", "reason": "Subdues Liver Yang, moves Liver Qi"},
    {"code": "GB20", "name": "Fengchi", "reason": "Clears the head, relieves headache"},
    {"code": "LI4", "name": "Hegu", "reason": "Command point for the face and head"},
    {"code": "KI3", "name": "Taixi", "reason": "Nourishes Kidney Yin to anchor Yang"},
    {"code": "GB34", "name": "Yanglingquan", "reason": "Soothes the Liver, relieves tension"},
    {"code": "SP6", "name": "Sanyinjiao", "reason": "Nourishes Yin and Blood"},
]


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChatModel:
    """Stands in for a LangChain chat model: `ainvoke(messages)` returns a canned reply."""

    def __init__(self, reply: str, calls: list[dict[str, Any]], role: str) -> None:
        self.reply = reply
        self.calls = calls
        self.role = role

    async def ainvoke(self, messages: Any, **_: Any) -> _FakeMessage:
        self.calls.append({"model": self.role, "messages": messages})
        return _FakeMessage(self.reply)


class FakeLLM:
    def __init__(self) -> None:
        import json

        self.calls: list[dict[str, Any]] = []
        self.short = FakeChatModel(CANNED_QUESTION, self.calls, "short")
        self.long = FakeChatModel(json.dumps(CANNED_DIAGNOSIS), self.calls, "long")
        self.points = FakeChatModel(json.dumps(CANNED_POINTS), self.calls, "points")


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Replace the three module-level chat models and the Redis-backed history in ai_intake."""
    from langchain_core.chat_history import InMemoryChatMessageHistory

    from bot.patient_bot.services import ai_intake

    fake = FakeLLM()
    monkeypatch.setattr(ai_intake, "_LLM", fake.short)
    monkeypatch.setattr(ai_intake, "_LLM_LONG", fake.long)
    monkeypatch.setattr(ai_intake, "_LLM_POINTS", fake.points)

    histories: dict[int, InMemoryChatMessageHistory] = {}

    def _get_history(user_id: int) -> InMemoryChatMessageHistory:
        return histories.setdefault(user_id, InMemoryChatMessageHistory())

    monkeypatch.setattr(ai_intake, "_get_history", _get_history)
    monkeypatch.setattr(ai_intake, "_history_cache", {})
    monkeypatch.setattr(ai_intake, "_rolling_summaries", {})
    return fake


# ── 8. Factories ─────────────────────────────────────────────────────────────────────────────
_counter = {"telegram": 900_000_000, "manual": 0}


@pytest.fixture
def make_therapist():
    from web.deps import _hash_password
    from web.repositories import therapist_repo

    def _make(
        name: str = "Dr Test",
        email: str | None = None,
        password: str | None = None,
        active: bool = True,
        telegram_id: int = 0,
        therapist_id: str | None = None,
        language: str = "en",
    ) -> dict[str, Any]:
        tid = therapist_id or therapist_repo.next_id()
        therapist_repo.insert(
            {
                "id": tid,
                "name": name,
                "telegram_id": telegram_id,
                "email": email,
                "password_hash": _hash_password(password) if password else None,
                "google_id": None,
                "calendar_name": "ZenFlow Availability",
                "active": active,
            }
        )
        dbmod.get_db().execute("UPDATE therapists SET language=? WHERE id=?", (language, tid))
        from bot import config as botcfg

        botcfg.reload_therapists()
        return {
            "id": tid,
            "name": name,
            "email": email,
            "password": password,
            "active": active,
            "telegram_id": telegram_id,
            "language": language,
        }

    return _make


@pytest.fixture
def make_patient():
    def _make(name: str = "Test Patient", *, manual: bool = False) -> dict[str, Any]:
        if manual:
            _counter["manual"] += 1
            pid = -(1_700_000_000_000 + _counter["manual"])  # negative like insert_manual()
        else:
            _counter["telegram"] += 1
            pid = _counter["telegram"]
        return {"patient_id": pid, "name": name, "source": "manual" if manual else "telegram"}

    return _make


@pytest.fixture
def make_appointment(make_therapist, make_patient):
    def _make(
        therapist: dict[str, Any] | None = None,
        patient: dict[str, Any] | None = None,
        apt_date: str = "2026-03-02",
        apt_time: str = "10:00",
        status: str = "active",
        summary: str = "",
        intake_history: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        therapist = therapist or make_therapist()
        patient = patient or make_patient()
        cur = dbmod.get_db().execute(
            """INSERT INTO appointments
               (patient_id, patient_name, therapist_id, date, time, status, summary, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                patient["patient_id"],
                patient["name"],
                therapist["id"],
                apt_date,
                apt_time,
                status,
                summary,
                patient["source"],
            ),
        )
        apt_id = int(cur.lastrowid or 0)
        if intake_history is not None:
            from web.repositories import intake_repo

            intake_repo.insert(apt_id, patient["patient_id"], therapist["id"], intake_history)
        return {
            "id": apt_id,
            "patient_id": patient["patient_id"],
            "patient_name": patient["name"],
            "therapist_id": therapist["id"],
            "date": apt_date,
            "time": apt_time,
            "status": status,
            "source": patient["source"],
        }

    return _make


@pytest.fixture
def make_treatment_notes():
    from web.repositories import treatment_repo

    def _make(appointment: dict[str, Any], **fields: Any) -> dict[str, Any]:
        notes: dict[str, Any] = {
            "tcm_pattern": CANNED_DIAGNOSIS["tcm_pattern"],
            "treatment_principles": CANNED_DIAGNOSIS["treatment_principles"],
            "diagnosis_certainty": CANNED_DIAGNOSIS["diagnosis_certainty"],
            "ai_suggested_points": CANNED_POINTS,
            "ai_recommendations": CANNED_DIAGNOSIS["recommendations"],
        }
        notes.update(fields)
        treatment_repo.upsert(appointment["id"], appointment["patient_id"], notes)
        saved = treatment_repo.get_by_appointment(appointment["id"])
        assert saved is not None
        return saved

    return _make


@pytest.fixture
def make_completed_session(make_appointment, make_treatment_notes):
    def _make(completed_at: str | None = None, **appointment_kwargs: Any) -> dict[str, Any]:
        apt = make_appointment(**appointment_kwargs)
        make_treatment_notes(
            apt,
            tongue_observation="pale, thin white coat",
            pulse_observation="wiry",
            used_points=CANNED_POINTS[:3],
            completed_at=completed_at or datetime.now(UTC).isoformat(),
        )
        return apt

    return _make
