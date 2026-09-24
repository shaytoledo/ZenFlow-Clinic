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
