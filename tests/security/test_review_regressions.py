"""Regression tests for the findings of the Phase 0 code review (PR #3, 2026-09-15).

Each test was red against the reviewed code and is green after the fix it names.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from dotenv import dotenv_values

from web.repositories import treatment_repo
from zenflow import settings as S

pytestmark = pytest.mark.security

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── .env.example must never parse a comment as a value (would sign sessions with public text) ──
def test_env_example_parses_clean_and_boots() -> None:
    values = dotenv_values(REPO_ROOT / ".env.example")
    polluted = {k: v for k, v in values.items() if v and v.lstrip().startswith("#")}
    assert polluted == {}, f"inline comments became values: {polluted}"
    assert values["SESSION_SECRET"] in ("", None)
    assert values["ZF_AI_PROVIDER"] in ("", None)
    # The template itself must construct a valid Settings object (os.environ still wins for
    # ENV etc. — the harness pins ENV=test; what matters is that nothing in the file rejects).
    flags = S.FeatureFlags(_env_file=REPO_ROOT / ".env.example")  # type: ignore[call-arg]
    s = S.Settings(_env_file=REPO_ROOT / ".env.example", flags=flags)  # type: ignore[call-arg]
    assert s.ai_provider == "ollama"  # empty ZF_AI_PROVIDER falls back to USE_AI
    assert s.google_redirect_uri.startswith("http://localhost:8080/")


def test_empty_zf_ai_provider_means_legacy_use_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZF_AI_PROVIDER", "")
    monkeypatch.setenv("USE_AI", "anthropic")
    S.reset_settings()
    try:
        assert S.get_settings().ai_provider == "anthropic"
    finally:
        S.reset_settings()


def test_zenflow_dotenv_zero_disables_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OLLAMA_MODEL=from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("ZENFLOW_DOTENV", str(env_file))
    S.reset_settings()
    try:
        assert S.get_settings().ollama_model == "from-dotenv"
        monkeypatch.setenv("ZENFLOW_DOTENV", "0")
        S.reset_settings()
        assert S.get_settings().ollama_model == "gemma3:latest"
    finally:
        S.reset_settings()


#: The one kind of /api endpoint without a session or key: a provider webhook, which proves
#: itself with a signature over the body and is absent while its channel is off. The test below
#: pins that it really does verify, so this is an exception with a reason, not a hole.
SIGNATURE_AUTHENTICATED = {"/api/webhooks/whatsapp"}


async def test_a_webhook_without_a_signature_is_refused(client, monkeypatch) -> None:
    """The exception above, enforced: no signature, no entry — and nothing while the flag is off."""
    from zenflow import settings as S

    monkeypatch.setenv("ZF_CHANNEL_WHATSAPP", "1")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "secret-for-this-test-0123456789")
    S.reset_settings()
    try:
        resp = await client.post("/api/webhooks/whatsapp", content=b'{"entry": []}')
        assert resp.status_code == 401
        resp = await client.post(
            "/api/webhooks/whatsapp",
            content=b'{"entry": []}',
            headers={"X-Hub-Signature-256": "sha256=" + "0" * 64},
        )
        assert resp.status_code == 401, "a wrong signature is no signature"
    finally:
        S.reset_settings()


# ── every /api route declares an authentication dependency (default-deny, not opt-in) ──
def test_every_api_route_declares_its_authentication() -> None:
    """The dashboard's endpoints take the router-level session dependency; the booking API
    (Phase 7.3) takes its own guard, which accepts an API key or that same session."""
    from web.app import app
    from web.deps import require_signed_in
    from web.routers.api.v1 import _guard

    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        if path in SIGNATURE_AUTHENTICATED:
            continue
        dependant = getattr(route, "dependant", None)
        guards = {d.call for d in dependant.dependencies} if dependant else set()
        if path.startswith("/api/v1/"):
            if _guard not in guards:
                missing.append(path)
        elif require_signed_in not in guards:
            missing.append(path)
    assert missing == [], f"/api routes without authentication: {missing}"


# ── send-recommendations must act on the caller's appointment even when another therapist has
#    a row for the same patient/date/time (the immediate-send path used to re-query unscoped) ──
async def test_send_recommendations_uses_the_callers_row_not_a_newer_twin(
    make_therapist, make_appointment, make_treatment_notes, login_as, fake_telegram
) -> None:
    a = make_therapist(email="a2@example.com", password="pw-Test-123")
    b = make_therapist(email="b2@example.com", password="pw-Test-123")
    patient = {"patient_id": 700_000_123, "name": "Shared Patient", "source": "telegram"}
    apt_a = make_appointment(therapist=a, patient=patient, apt_date="2026-03-03", apt_time="11:00")
    apt_b = make_appointment(therapist=b, patient=patient, apt_date="2026-03-03", apt_time="11:00")
    make_treatment_notes(apt_a)
    make_treatment_notes(apt_b)
    client_a = await login_as(a)
    url = f"/api/treatment-notes/{apt_a['patient_id']}/2026-03-03/11-00/send-recommendations"
    resp = await client_a.post(
        url, json={"items": [{"enabled": True, "text": "Drink warm water"}], "schedule_hours": 0}
    )
    assert resp.status_code == 200, resp.text
    assert len(fake_telegram.calls) == 1
    notes_a = treatment_repo.get_by_appointment(apt_a["id"])
    notes_b = treatment_repo.get_by_appointment(apt_b["id"])
    assert notes_a and notes_a["recommendations_sent_at"], "A's own row must be stamped"
    assert notes_b and not notes_b["recommendations_sent_at"], "B's row must be untouched"


# ── Redis outage on the messages endpoints degrades, it does not 500 ──
async def test_conversation_endpoints_survive_redis_outage(
    make_therapist, make_appointment, login_as, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = make_therapist(email="a3@example.com", password="pw-Test-123")
    apt = make_appointment(therapist=a)
    client_a = await login_as(a)

    import bot.redis_client as rc

    class _Down:
        async def get(self, *_: object, **__: object) -> None:
            raise ConnectionError("redis down")

        async def keys(self, *_: object, **__: object) -> list[str]:
            raise ConnectionError("redis down")

    monkeypatch.setattr(rc, "_async_client", _Down())
    resp = await client_a.get(f"/api/messages/history/{apt['patient_id']}")
    # Ownership cannot be verified without Redis, so access is refused — never granted on a
    # guess (Phase 2.4 removed the appointment fallback, SF-008) and never a 500.
    assert resp.status_code == 404


# ── a corrupt relay blob must not be read as "owned by someone else", nor crash ──
# (Phase 2.4 replaced the appointment fallback with per-therapist history, SF-008.)
async def test_corrupt_relay_blob_does_not_hide_the_therapists_own_conversation(
    make_therapist, make_appointment, login_as, fake_redis
) -> None:
    from bot.patient_bot.services.relay import append_history

    a = make_therapist(email="a4@example.com", password="pw-Test-123")
    apt = make_appointment(therapist=a)
    append_history(apt["patient_id"], "patient", "hello", a["id"])
    await fake_redis.async_.set(f"zenflow:relay:active:{apt['patient_id']}", "not json {")
    client_a = await login_as(a)
    resp = await client_a.get(f"/api/messages/history/{apt['patient_id']}")
    assert resp.status_code == 200


async def test_corrupt_relay_blob_without_a_conversation_is_not_found(
    make_therapist, make_appointment, login_as, fake_redis
) -> None:
    a = make_therapist(email="a5@example.com", password="pw-Test-123")
    apt = make_appointment(therapist=a)
    await fake_redis.async_.set(f"zenflow:relay:active:{apt['patient_id']}", "not json {")
    client_a = await login_as(a)
    resp = await client_a.get(f"/api/messages/history/{apt['patient_id']}")
    assert resp.status_code == 404


# ── Google tokens encrypted before Phase 0.4 keep decrypting after TOKEN_ENCRYPTION_KEY is set ──
def test_legacy_encrypted_google_token_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    import bot.db as dbmod
    from web import gcal
    from zenflow.token_key import fernet_for

    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", "new-dedicated-key-0123456789abcdefghij")
    S.reset_settings()
    try:
        legacy = fernet_for(S.get_settings().session_secret)
        creds = {
            "token": "ya29.legacy",
            "refresh_token": "1//0legacy",
            "client_id": "cid",
            "client_secret": "csecret",
            "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": list(gcal.SCOPES),
        }
        dbmod.get_db().execute(
            "INSERT INTO google_tokens (therapist_id, encrypted_token, scopes) VALUES (?, ?, ?)",
            ("t9", legacy.encrypt(json.dumps(creds).encode()).decode(), "x"),
        )
        loaded = gcal._load_token_db("t9")
        assert loaded is not None and loaded.refresh_token == "1//0legacy"
    finally:
        S.reset_settings()


# ── HTML treatment page for another tenant's appointment bounces, never returns JSON ──
async def test_treatment_page_for_foreign_appointment_redirects(
    make_therapist, make_appointment, login_as
) -> None:
    a = make_therapist(email="a5@example.com", password="pw-Test-123")
    b = make_therapist(email="b5@example.com", password="pw-Test-123")
    apt_b = make_appointment(therapist=b, apt_date="2026-03-04", apt_time="09:00")
    client_a = await login_as(a)
    resp: httpx.Response = await client_a.get(
        f"/treatment/{apt_b['patient_id']}/2026-03-04/09-00", follow_redirects=False
    )
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/patients"
    assert "application/json" not in resp.headers.get("content-type", "")
