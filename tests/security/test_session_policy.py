"""Plan 9.2 — sessions and transport: a session that ends when it should.

The cookie was already hardened in 0.5 (HttpOnly, SameSite, Secure outside dev). What was missing
is everything about *time and identity*: a signed-in session inherited whatever an anonymous
visitor had planted, it never expired while the cookie lasted, and signing out only asked the
browser to forget a cookie that still verified.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from itsdangerous import TimestampSigner

from zenflow import clock

pytestmark = pytest.mark.security

PW = "pw-Test-123"
SECRET = "test-only-session-secret-0123456789abcdef0123456789abcdef"


def _session(client: httpx.AsyncClient) -> dict[str, Any]:
    """What is actually inside the session cookie (Starlette signs, it does not encrypt)."""
    import base64

    raw = client.cookies.get("zf_session")
    assert raw, "no session cookie"
    data = TimestampSigner(str(SECRET)).unsign(raw, max_age=None)
    return dict(json.loads(base64.urlsafe_b64decode(data)))


def _unsign(cookie: str) -> dict[str, Any]:
    import base64

    data = TimestampSigner(str(SECRET)).unsign(cookie, max_age=None)
    return dict(json.loads(base64.urlsafe_b64decode(data)))


def _plant(client: httpx.AsyncClient, data: dict[str, Any]) -> None:
    """Forge an anonymous session cookie the way Starlette's SessionMiddleware would sign one."""
    import base64

    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    signed = TimestampSigner(str(SECRET)).sign(payload).decode()
    client.cookies.set("zf_session", signed, domain="testserver", path="/")


def _set_cookie(resp: httpx.Response) -> dict[str, Any]:
    """The session the server just wrote, read from its own Set-Cookie (avoids a jar with two)."""
    raw = resp.headers["set-cookie"]
    value = raw.split("zf_session=", 1)[1].split(";", 1)[0]
    return _unsign(value)


async def _sign_in(client: httpx.AsyncClient, therapist: dict[str, Any]) -> None:
    resp = await client.post("/register/signin", data={"email": therapist["email"], "password": PW})
    assert resp.status_code in (200, 302, 303), resp.text


@pytest.fixture
def therapist(make_therapist):
    return make_therapist(email="session@example.com", password=PW, active=True)


# ── fixation ──
async def test_signing_in_starts_a_fresh_session(client, therapist) -> None:
    """Nothing a stranger planted may survive into a signed-in session."""
    _plant(client, {"reg_google": True, "sid": "attacker-chosen", "next": "/evil"})

    resp = await client.post("/register/signin", data={"email": therapist["email"], "password": PW})
    after = _set_cookie(resp)
    assert after["therapist_id"] == therapist["id"]
    assert "reg_google" not in after and "next" not in after, "the old session was carried over"
    assert after["sid"] != "attacker-chosen", "the session id is the server's, not the caller's"


# ── time ──
async def test_an_idle_session_stops_working(client, therapist, frozen_clock) -> None:
    from web import session_policy

    await _sign_in(client, therapist)
    assert (await client.get("/api/my/status")).status_code == 200

    frozen_clock.tick(timedelta(minutes=session_policy.idle_minutes() + 1))
    assert (await client.get("/api/my/status")).status_code == 401


async def test_working_keeps_a_session_alive(client, therapist, frozen_clock) -> None:
    from web import session_policy

    await _sign_in(client, therapist)
    for _ in range(3):
        frozen_clock.tick(timedelta(minutes=session_policy.idle_minutes() - 5))
        assert (await client.get("/api/my/status")).status_code == 200


async def test_a_session_cannot_outlive_the_absolute_limit(client, therapist, frozen_clock) -> None:
    """Even a therapist working all week signs in again eventually."""
    from web import session_policy

    await _sign_in(client, therapist)
    frozen_clock.tick(timedelta(hours=session_policy.max_hours() + 1))
    assert (await client.get("/api/my/status")).status_code == 401


async def test_the_cookie_does_not_outlive_the_policy(client, therapist) -> None:
    from web import session_policy
    from web.app import session_cookie_kwargs

    assert session_cookie_kwargs(is_dev=True)["max_age"] == session_policy.max_hours() * 3600


# ── logout ──
async def test_a_signed_out_cookie_is_refused_even_if_it_is_replayed(client, therapist) -> None:
    """A cookie session cannot be deleted from the client — so the server remembers the refusal."""
    await _sign_in(client, therapist)
    stolen = client.cookies.get("zf_session")
    assert (await client.get("/api/my/status")).status_code == 200

    assert (await client.get("/logout", follow_redirects=False)).status_code == 303

    client.cookies.set("zf_session", stolen)
    assert (await client.get("/api/my/status")).status_code == 401


async def test_signing_out_twice_is_harmless(client, therapist) -> None:
    await _sign_in(client, therapist)
    for _ in range(2):
        assert (await client.get("/logout", follow_redirects=False)).status_code == 303


async def test_a_revocation_is_forgotten_once_it_cannot_matter(client, therapist) -> None:
    """The denylist holds a session only as long as that session could still have been used."""
    from web import session_policy

    await _sign_in(client, therapist)
    sid = _session(client)["sid"]
    await client.get("/logout", follow_redirects=False)
    assert session_policy.is_revoked(sid) is True

    pruned = session_policy.prune(now_iso=clock.hours_ahead(session_policy.max_hours() + 1))
    assert pruned == 1 and session_policy.is_revoked(sid) is False


# ── transport ──
async def test_hsts_is_sent_only_where_https_is_real(client) -> None:
    from web.app import security_headers

    assert "Strict-Transport-Security" not in security_headers(is_dev=True)
    header = security_headers(is_dev=False)["Strict-Transport-Security"]
    assert "max-age=31536000" in header and "includeSubDomains" in header

    resp = await client.get("/healthz")
    assert "strict-transport-security" not in resp.headers, "ENV=test is dev: plain http is fine"


async def test_nothing_is_shared_across_origins(client, therapist) -> None:
    """No CORS middleware is installed, and a browser's default is to refuse. Keep it that way."""
    await _sign_in(client, therapist)
    resp = await client.get("/api/my/status", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in resp.headers
    assert "access-control-allow-credentials" not in resp.headers

    options = await client.request(
        "OPTIONS",
        "/api/appointments",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in options.headers
