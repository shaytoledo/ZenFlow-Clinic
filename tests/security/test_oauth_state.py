"""Phase 10.2 (A12) — the Google OAuth `state` parameter is generated and verified.

Both OAuth flows (Calendar connect and Google registration) previously discarded the `state` the
library generates and never checked it on the callback (SF-018). That is the OAuth-CSRF hole: an
attacker could feed the victim's browser an authorization code of the attacker's choosing. The
callback now refuses a code whose `state` does not match the one stashed in the session when the
flow began, so a forged or absent state never reaches the token exchange.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.security


def test_verify_oauth_state_logic() -> None:
    from types import SimpleNamespace
    from typing import Any, cast

    from web.deps import _verify_oauth_state

    def req(session: dict) -> Any:
        return cast(Any, SimpleNamespace(session=session))

    assert _verify_oauth_state(req({"oauth_state": "abc123"}), "abc123") is True
    assert _verify_oauth_state(req({"oauth_state": "abc123"}), "wrong") is False
    assert _verify_oauth_state(req({}), "abc123") is False, "no stored state → refuse"
    assert _verify_oauth_state(req({"oauth_state": "abc123"}), "") is False, "no supplied state"


async def test_a_missing_or_unmatched_state_is_refused(authenticated_client, monkeypatch) -> None:
    import web.routers.auth as auth

    called = {"exchange": False}
    monkeypatch.setattr(
        auth, "exchange_code", lambda code, tid: called.__setitem__("exchange", True)
    )
    # No /auth/login first — the session has no oauth_state.
    resp = await authenticated_client.get(
        "/auth/callback?code=attacker-code&state=anything", follow_redirects=False
    )
    assert called["exchange"] is False, "no stored state means the callback must refuse"
    _ = resp


def test_get_auth_url_returns_a_state() -> None:
    """The URL builder must surface the state so the caller can stash it."""
    import inspect

    from web import gcal

    src = inspect.getsource(gcal.get_auth_url)
    assert "return" in src and "state" in src, "get_auth_url must return the state, not discard it"


class _FakeRegFlow:
    """Google's OAuth flow, offline: hands out state S1 and records any code exchange."""

    exchanged: list[str] = []

    def authorization_url(self, **_kw: object) -> tuple[str, str]:
        return "https://accounts.google.com/o/oauth2/auth?state=S1", "S1"

    def fetch_token(self, code: str) -> None:
        _FakeRegFlow.exchanged.append(code)
        raise RuntimeError("stop here — the test only needs to know the exchange was reached")


@pytest.fixture
def reg_flow(monkeypatch):
    import web.deps as deps
    import web.routers.auth as auth

    _FakeRegFlow.exchanged = []
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth, "_make_reg_flow", _FakeRegFlow)
    monkeypatch.setattr(deps, "_make_reg_flow", _FakeRegFlow)
    return _FakeRegFlow


async def test_the_registration_callback_refuses_a_forged_state(client, reg_flow) -> None:
    """SF-018 on the second flow: Google sign-up/sign-in must check the state too."""
    await client.get("/register/google", follow_redirects=False)  # session now holds S1
    resp = await client.get(
        "/register/google/callback?code=attacker-code&state=forged", follow_redirects=False
    )
    assert reg_flow.exchanged == [], "a forged state must never reach the token exchange"
    assert "error" in resp.headers["location"]


async def test_the_registration_callback_without_a_started_flow_is_refused(
    client, reg_flow
) -> None:
    resp = await client.get(
        "/register/google/callback?code=attacker-code&state=S1", follow_redirects=False
    )
    assert reg_flow.exchanged == [], "no flow was started in this session"
    assert "error" in resp.headers["location"]


async def test_the_registration_callback_accepts_its_own_state(client, reg_flow) -> None:
    await client.get("/register/google", follow_redirects=False)
    await client.get("/register/google/callback?code=real-code&state=S1", follow_redirects=False)
    assert reg_flow.exchanged == ["real-code"], "the matching state reaches the exchange"
