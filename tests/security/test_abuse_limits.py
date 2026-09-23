"""Plan 9.5 (part 2) — volume limits: the AI endpoints and sign-up.

An authenticated therapist could hammer the diagnosis/point-generation endpoints and pin the shared
Ollama box; anyone could script the public sign-up form. Both are now capped per minute — the AI
endpoints per account, sign-up per source IP — reusing the booking API's fixed-window limiter.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.security

PW = "pw-Test-123"
REDIAGNOSE = "/api/treatment-notes/1/2026-01-01/09-00/rediagnose"


async def test_the_ai_endpoints_are_capped_per_account(authenticated_client, monkeypatch) -> None:
    from web.services import rate_limit

    monkeypatch.setattr(rate_limit, "ai_per_minute", lambda: 2)
    first = await authenticated_client.post(REDIAGNOSE, json={})
    second = await authenticated_client.post(REDIAGNOSE, json={})
    third = await authenticated_client.post(REDIAGNOSE, json={})

    assert first.status_code != 429 and second.status_code != 429, "within budget is not throttled"
    assert third.status_code == 429, "over budget the AI endpoint is refused before doing work"
    assert third.headers.get("retry-after"), "a throttled response says how long to wait"


async def test_the_ai_cap_is_off_when_set_to_zero(authenticated_client, monkeypatch) -> None:
    from web.services import rate_limit

    monkeypatch.setattr(rate_limit, "ai_per_minute", lambda: 0)
    for _ in range(6):
        resp = await authenticated_client.post(REDIAGNOSE, json={})
        assert resp.status_code != 429, "0 means no AI limit"


async def _signup(client: httpx.AsyncClient, email: str) -> httpx.Response:
    return await client.post(
        "/register/signup",
        data={"name": "Dr Flood", "email": email, "password": PW},
        follow_redirects=False,
    )


async def test_signup_is_capped_per_ip(client, monkeypatch) -> None:
    from web.services import rate_limit

    monkeypatch.setattr(rate_limit, "signup_per_minute", lambda: 2)
    first = await _signup(client, "flood1@example.com")
    second = await _signup(client, "flood2@example.com")
    third = await _signup(client, "flood3@example.com")

    assert first.status_code in (302, 303, 307), "a first sign-up goes through"
    assert second.status_code in (302, 303, 307)
    assert third.status_code == 429, "the third rapid sign-up from one IP is throttled"
    assert third.headers.get("retry-after")


async def test_signup_cap_is_off_when_set_to_zero(client, monkeypatch) -> None:
    from web.services import rate_limit

    monkeypatch.setattr(rate_limit, "signup_per_minute", lambda: 0)
    for i in range(5):
        resp = await _signup(client, f"nolimit{i}@example.com")
        assert resp.status_code != 429, "0 means no sign-up limit"


def test_the_limiter_helpers_read_their_flags() -> None:
    from web.services import rate_limit

    assert isinstance(rate_limit.ai_per_minute(), int)
    assert isinstance(rate_limit.signup_per_minute(), int)
