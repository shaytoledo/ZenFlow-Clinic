"""Phase 5.2 — "Connect Google" returns the therapist to the page they came from, and only there.

`/auth/login?next=<path>` keeps the path in the signed session; the OAuth callback redirects to it
with `?google=connected|cancelled`. Anything that is not a same-site path is dropped, so `next`
can never become an open redirect (the recovered prior work accepted `/\\evil.example`, which
browsers read as `//evil.example`).
"""

from __future__ import annotations

from typing import Any

import pytest

from bot import db as dbmod

pytestmark = pytest.mark.security

PW = "pw-Test-123"
PAGE = "/treatment/5/2026-03-02/10-00"
STATE = "test-oauth-state"  # the mocked get_auth_url returns this; the callback must echo it (A12)


@pytest.fixture
async def google_client(make_therapist, login_as, monkeypatch):
    import web.routers.auth as auth

    async def _no_prefetch(_tid: str) -> None:
        return None

    exchanged: list[tuple[str, str]] = []
    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(
        auth, "get_auth_url", lambda: ("https://accounts.google.com/o/oauth2/auth", STATE)
    )
    monkeypatch.setattr(auth, "exchange_code", lambda code, tid: exchanged.append((code, tid)))
    monkeypatch.setattr(auth, "prefetch_calendar", _no_prefetch)
    therapist = make_therapist(email="oauth@example.com", password=PW)
    client = await login_as(therapist)
    client.exchanged = exchanged
    client.therapist = therapist
    return client


async def _round_trip(client: Any, next_value: str | None, callback: str = "code=abc") -> str:
    params = {} if next_value is None else {"next": next_value}
    start = await client.get("/auth/login", params=params)
    assert start.status_code in (302, 307)
    assert start.headers["location"].startswith("https://accounts.google.com/")
    done = await client.get(f"/auth/callback?{callback}&state={STATE}")
    assert done.status_code in (302, 307), done.text
    return str(done.headers["location"])


async def test_connecting_returns_to_the_page(google_client) -> None:
    assert await _round_trip(google_client, PAGE) == f"{PAGE}?google=connected"
    assert google_client.exchanged == [("abc", google_client.therapist["id"])]


async def test_cancelling_returns_to_the_page_too(google_client) -> None:
    location = await _round_trip(google_client, PAGE, callback="error=access_denied")
    assert location == f"{PAGE}?google=cancelled"


async def test_the_return_path_is_used_once(google_client) -> None:
    await _round_trip(google_client, PAGE)
    await google_client.get("/auth/login")  # a fresh flow (no next) re-arms the state
    again = await google_client.get(f"/auth/callback?code=def&state={STATE}")
    assert again.headers["location"] == "/settings?connected=1"


async def test_a_login_without_next_forgets_an_old_one(google_client) -> None:
    await google_client.get("/auth/login", params={"next": PAGE})
    assert await _round_trip(google_client, None) == "/settings?connected=1"


async def test_query_and_fragment_are_kept(google_client) -> None:
    location = await _round_trip(google_client, "/settings?tab=mail&google=x#google")
    assert location == "/settings?tab=mail&google=connected#google"


@pytest.mark.parametrize(
    "next_value",
    [
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example/",
        "\\\\evil.example",
        "/\t/evil.example",
        "/%0d%0aSet-Cookie:x=1\r\n",
        "javascript:alert(1)",
        "evil.example",
        " //evil.example",
        "/" + "a" * 600,
    ],
)
async def test_anything_but_a_same_site_path_is_ignored(google_client, next_value: str) -> None:
    assert await _round_trip(google_client, next_value) == "/settings?connected=1"


async def test_reconnecting_resolves_the_reconnect_alert(google_client) -> None:
    from web.services import email_service

    tid = google_client.therapist["id"]
    email_service._notify_reconnect(tid)
    await _round_trip(google_client, PAGE)
    active = (
        dbmod.get_db()
        .execute(
            "SELECT COUNT(*) FROM notifications WHERE therapist_id=? "
            "AND kind='gmail_token_expired' AND resolved_at IS NULL",
            (tid,),
        )
        .fetchone()[0]
    )
    assert active == 0
