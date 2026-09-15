"""Phase 0.4 — GET /api/admin/flags (auth required) renders current flag state."""

from __future__ import annotations

import httpx
import pytest

from zenflow import settings as S

pytestmark = pytest.mark.integration


async def test_admin_flags_requires_a_session(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/admin/flags")
    assert resp.status_code == 401


async def test_admin_flags_lists_every_flag_and_no_secrets(
    authenticated_client: httpx.AsyncClient,
) -> None:
    resp = await authenticated_client.get("/api/admin/flags")
    assert resp.status_code == 200
    body = resp.json()
    assert body["env"] == "test"
    assert set(body["flags"]) == set(S.FLAG_NAMES)
    dumped = resp.text.lower()
    for forbidden in ("session_secret", "token_encryption_key", "client_secret", "api_key"):
        assert forbidden not in dumped
    assert "TEST-PATIENT-BOT-TOKEN" not in resp.text


async def test_admin_flags_reflects_the_environment(
    authenticated_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZF_SSE_UPDATES", "1")
    monkeypatch.setenv("ZF_QUEUE_BACKEND", "temporal")
    S.reset_settings()
    try:
        resp = await authenticated_client.get("/api/admin/flags")
        assert resp.json()["flags"]["SSE_UPDATES"] is True
        assert resp.json()["flags"]["QUEUE_BACKEND"] == "temporal"
    finally:
        S.reset_settings()
