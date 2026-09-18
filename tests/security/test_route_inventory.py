"""Plan 9.1 — authorization everywhere, enumerated and enforced.

`web/authz.py` declares, for every route, who may call it, what keeps one therapist out of
another's data, and which test proves it. This compares that table with the running application:
a new route that nobody classified fails the build, and so does a route whose refusal does not
match what it promises.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from web import authz

pytestmark = pytest.mark.security

REPO_ROOT = Path(__file__).resolve().parents[2]
#: stand-ins for path parameters — the value never matters, the refusal happens first
PLACEHOLDERS = {
    "{patient_id}": "1",
    "{appointment_id}": "1",
    "{apt_date}": "2026-03-12",
    "{apt_time}": "10-00",
    "{event_id}": "abc",
    "{notification_id}": "1",
    "{key:path}": "acupoints/li4/x.webp",
}


def _routes() -> set[tuple[str, str]]:
    from web.app import app

    found: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path.startswith("/static"):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method != "HEAD":
                found.add((method, path))
    return found


def _url(path: str) -> str:
    for token, value in PLACEHOLDERS.items():
        path = path.replace(token, value)
    return path


# ── the table is complete ──
def test_every_route_is_classified() -> None:
    """A new route has to say who may call it, how it is scoped, and where that is tested."""
    live = _routes()
    declared = set(authz.ROUTES)
    assert sorted(live - declared) == [], "add these to web/authz.py ROUTES"
    assert sorted(declared - live - authz.DEV_ONLY) == [], "these no longer exist — remove them"


def test_every_scoped_route_names_a_test_that_exists() -> None:
    """A scoping claim without a test is a comment. This makes it a build failure."""
    missing = []
    for (method, path), access in authz.ROUTES.items():
        if access.scope == authz.NONE:
            continue
        if not access.covered_by:
            missing.append(f"{method} {path}: no test named")
        elif not (REPO_ROOT / access.covered_by).exists():
            missing.append(f"{method} {path}: {access.covered_by} does not exist")
    assert missing == []


def test_the_levels_and_scopes_are_the_documented_ones() -> None:
    levels = {authz.PUBLIC, authz.SESSION, authz.ACTIVE, authz.MACHINE, authz.SIGNATURE}
    scopes = {
        authz.NONE,
        authz.OWN,
        authz.APPOINTMENT,
        authz.CONVERSATION,
        authz.PATIENT,
        authz.SELF,
    }
    for (method, path), access in authz.ROUTES.items():
        assert access.auth in levels, f"{method} {path}"
        assert access.scope in scopes, f"{method} {path}"


# ── the application behaves the way the table says ──
async def test_a_stranger_is_refused_by_every_route_that_needs_a_session(
    client: httpx.AsyncClient,
) -> None:
    wrong: list[str] = []
    for (method, path), access in sorted(authz.ROUTES.items()):
        if access.auth == authz.PUBLIC or access.auth == authz.SIGNATURE:
            continue
        resp = await client.request(method, _url(path), json={}, follow_redirects=False)
        expected = authz.ANONYMOUS_REFUSAL[access.auth]
        if resp.status_code not in expected:
            wrong.append(f"{method} {path} -> {resp.status_code}, expected one of {expected}")
    assert wrong == []


async def test_a_refused_page_sends_the_stranger_to_sign_in(client: httpx.AsyncClient) -> None:
    resp = await client.get("/schedule", follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert resp.headers["location"].startswith("/register")


async def test_a_refused_api_call_says_nothing_about_the_request(
    client: httpx.AsyncClient,
) -> None:
    """401 before body validation: an anonymous caller must not learn the schema from a 422."""
    resp = await client.post("/api/appointments", json={"nonsense": True})
    assert resp.status_code == 401 and "nonsense" not in resp.text


async def test_the_public_routes_really_are_public(client: httpx.AsyncClient) -> None:
    opened = []
    for (method, path), access in sorted(authz.ROUTES.items()):
        if access.auth != authz.PUBLIC:
            continue
        resp = await client.request(method, _url(path), json={}, follow_redirects=False)
        assert resp.status_code != 401, f"{method} {path} is declared public but refuses"
        opened.append(f"{method} {path}")
    assert len(opened) >= 8


async def test_the_webhook_is_the_only_route_without_a_session_or_a_signature(
    client: httpx.AsyncClient,
) -> None:
    signature_routes = {r for r, a in authz.ROUTES.items() if a.auth == authz.SIGNATURE}
    assert {path for _m, path in signature_routes} == {"/api/webhooks/whatsapp"}


# ── the findings this audit produced ──
async def test_google_consent_cannot_be_started_by_a_stranger(client: httpx.AsyncClient) -> None:
    """SF-015: `/auth/login` wrote to the session and redirected to Google for anyone."""
    resp = await client.get("/auth/login", follow_redirects=False)
    assert resp.status_code in (303, 307)
    assert resp.headers["location"].startswith("/register")


def test_the_api_documentation_is_not_served_outside_dev() -> None:
    """SF-014: /docs, /redoc and /openapi.json handed anyone the full route map."""
    from web.app import docs_urls

    assert docs_urls(is_dev=True) == {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }
    assert docs_urls(is_dev=False) == {"docs_url": None, "redoc_url": None, "openapi_url": None}


# ── the table is the documentation ──
def test_the_documented_table_is_up_to_date() -> None:
    from zenflow.export_routes import main

    assert main(["--check"]) == 0, "run: python -m zenflow.export_routes"
