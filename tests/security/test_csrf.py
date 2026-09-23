"""Plan 9.3 — CSRF: a cross-site page cannot act as a logged-in therapist.

The dashboard authenticates with an ambient cookie, so before this a page on another origin could
make a signed-in therapist's browser POST to it. Now every unsafe, cookie-authenticated request has
to echo a token that a cross-site attacker cannot read.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.security

PW = "pw-Test-123"


def _token(client: httpx.AsyncClient) -> str:
    tok = client.cookies.get("zf_csrf")
    assert tok, "no CSRF cookie"
    return tok


async def _bare_signed_in(make_therapist) -> httpx.AsyncClient:
    """A client signed in but WITHOUT the auto-attached CSRF header the fixtures add."""
    from web.app import app

    therapist = make_therapist(email="csrf@example.com", password=PW, active=True)
    c = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
    )
    await c.get("/healthz")  # obtain the csrf cookie
    resp = await c.post(
        "/register/signin",
        data={"email": therapist["email"], "password": PW, "csrf_token": _token(c)},
    )
    assert resp.status_code in (302, 303, 307), resp.text
    c.headers["X-Test-Therapist-Id"] = therapist["id"]
    return c


# ── the cookie ──
async def test_a_visitor_is_given_a_readable_csrf_cookie(client) -> None:
    resp = await client.get("/register")
    cookie = resp.headers.get("set-cookie", "")
    if "zf_csrf" not in cookie:  # the shared fixture may have armed it on an earlier request
        assert client.cookies.get("zf_csrf")
        return
    assert "zf_csrf=" in cookie
    assert "httponly" not in cookie.lower(), "same-origin script must be able to read it"
    assert "samesite=lax" in cookie.lower()


async def test_the_cookie_is_stable_across_requests(client) -> None:
    first = _token(client)
    await client.get("/register")
    await client.get("/healthz")
    assert _token(client) == first, "a rotating cookie would never match a submitted token"


# ── the guard ──
async def test_a_post_without_the_token_is_refused(make_therapist) -> None:
    c = await _bare_signed_in(make_therapist)
    resp = await c.post("/api/messages/send", json={"patient_id": 1, "text": "hi"})
    assert resp.status_code == 403
    assert "csrf" in resp.text.lower()
    await c.aclose()


async def test_a_post_with_a_wrong_token_is_refused(make_therapist) -> None:
    c = await _bare_signed_in(make_therapist)
    resp = await c.post(
        "/api/messages/send",
        json={"patient_id": 1, "text": "hi"},
        headers={"X-CSRF-Token": "not-the-real-token"},
    )
    assert resp.status_code == 403
    await c.aclose()


async def test_the_matching_token_lets_the_request_through(make_therapist) -> None:
    c = await _bare_signed_in(make_therapist)
    resp = await c.post(
        "/api/messages/send",
        json={"patient_id": 999_999, "text": "hi"},
        headers={"X-CSRF-Token": _token(c)},
    )
    assert resp.status_code != 403, "a request with the right token is not a CSRF failure"
    await c.aclose()


async def test_a_native_form_post_may_carry_the_token_in_a_field(make_therapist) -> None:
    """The 3 HTML forms cannot set a header; they submit the token as a field instead."""
    c = await _bare_signed_in(make_therapist)
    resp = await c.post("/auth/disconnect", data={"csrf_token": _token(c)})
    assert resp.status_code != 403
    await c.aclose()


async def test_a_form_post_without_the_field_is_refused(make_therapist) -> None:
    c = await _bare_signed_in(make_therapist)
    resp = await c.post("/auth/disconnect", data={})
    assert resp.status_code == 403
    await c.aclose()


# ── what is exempt, and why it stays safe ──
async def test_get_requests_never_need_a_token(client) -> None:
    assert (await client.get("/api/my/status")).status_code in (200, 401)


async def test_the_booking_api_with_an_api_key_does_not_need_a_token(
    client, make_therapist
) -> None:
    """An Authorization header is not something a browser attaches on its own — no CSRF risk."""
    from bot import config as botcfg
    from web.repositories import api_client_repo

    make_therapist(therapist_id="t1")
    botcfg.reload_therapists()
    _id, key = api_client_repo.create("csrf-bridge")

    resp = await client.post(
        "/api/v1/appointments",
        json={
            "therapist_id": "t1",
            "start_at": "2026-03-12T08:00:00Z",
            "patient": {"name": "Dana", "channel": "telegram", "external_id": "900000123"},
        },
        headers={"Authorization": f"Bearer {key}"},
    )
    assert resp.status_code != 403, "an API-key request must not be treated as a CSRF failure"


async def test_the_booking_api_with_a_session_still_needs_a_token(make_therapist) -> None:
    """The booking API also takes a session — and a session call is exactly what CSRF must stop."""
    c = await _bare_signed_in(make_therapist)
    resp = await c.post(
        "/api/v1/appointments",
        json={
            "therapist_id": c.headers["X-Test-Therapist-Id"],
            "start_at": "2026-03-12T08:00:00Z",
            "patient": {"name": "Dana", "channel": "telegram", "external_id": "900000124"},
        },
    )
    assert resp.status_code == 403, "a cookie-authenticated booking POST needs the token"
    await c.aclose()


async def test_the_whatsapp_webhook_is_exempt(client) -> None:
    """It has no cookie and cannot carry a token; Meta's signature is its authentication."""
    resp = await client.post("/api/webhooks/whatsapp", json={"object": "whatsapp_business_account"})
    assert resp.status_code != 403, "the webhook must not be judged by CSRF"


# ── every unsafe dashboard route is behind the guard ──
def test_every_unsafe_dashboard_route_is_csrf_protected() -> None:
    """A new POST/DELETE/PATCH route that authenticates by cookie must be covered."""
    import inspect

    from fastapi import Depends

    from web import csrf
    from web.app import app

    unprotected: list[str] = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods: set[str] = set(getattr(route, "methods", set()) or set())
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None or not path.startswith(("/api/", "/auth/", "/register/")):
            continue
        if not (methods - csrf.SAFE_METHODS):
            continue
        if path.startswith(csrf.EXEMPT_PREFIXES):
            continue
        dependant = getattr(route, "dependant", None)
        guarded = False
        if dependant is not None:
            guarded = any(
                getattr(d, "call", None) is csrf.protect
                for d in getattr(dependant, "dependencies", [])
            )
        if not guarded:
            src = inspect.getsource(inspect.getmodule(endpoint) or endpoint)
            guarded = "csrf.protect" in src or "csrf_protect" in src
        if not guarded:
            unprotected.append(f"{sorted(methods - csrf.SAFE_METHODS)} {path}")
    assert unprotected == [], f"these unsafe routes are not CSRF-protected: {unprotected}"
    _ = Depends  # imported for the reader; the check reads the route's dependants
