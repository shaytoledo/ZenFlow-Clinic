"""Plan 9.4 — Security headers & Content-Security-Policy.

The dashboard renders HTML holding medical records. Before this it shipped no content-security
headers: a stray injected `<script>` would run, the page could be framed for clickjacking, MIME
sniffing was allowed, and a full URL (patient ids live in paths) leaked to any external site as a
referrer.

This closes the static headers hard (they break nothing) and ships a Content-Security-Policy. The
CSP starts **report-only** — the plan's "report-only first, then enforce" — because the pages still
carry inline `on*=` handlers and inline `style=` attributes that a strict policy would block; the
per-request script nonce is already wired, so the flip to enforce (`ZF_CSP_ENFORCE=1`) needs only
that template cleanup, not new plumbing.
"""

from __future__ import annotations

import re

import httpx
import pytest

pytestmark = pytest.mark.security

_CSP_REPORT = "content-security-policy-report-only"


def _csp(resp: httpx.Response) -> str:
    value = resp.headers.get(_CSP_REPORT)
    assert value, "a Content-Security-Policy-Report-Only header should be present"
    return value


def _nonce_of(resp: httpx.Response) -> str:
    match = re.search(r"'nonce-([^']+)'", _csp(resp))
    assert match, f"no script nonce in CSP: {_csp(resp)!r}"
    return match.group(1)


# ── the static headers (enforced, every environment) ──
async def test_static_security_headers_ride_every_response(client) -> None:
    resp = await client.get("/register")
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    assert "strict-origin" in resp.headers.get("referrer-policy", "")
    permissions = resp.headers.get("permissions-policy", "")
    for feature in ("geolocation=()", "camera=()", "microphone=()"):
        assert feature in permissions, f"{feature} should be disabled: {permissions!r}"


async def test_the_headers_are_on_api_responses_too(client) -> None:
    resp = await client.get("/api/my/status")  # 401 without a session — headers still ride
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert _CSP_REPORT in resp.headers


# ── the policy ──
async def test_the_policy_locks_the_obvious_holes(client) -> None:
    csp = _csp(await client.get("/register"))
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp


async def test_scripts_are_self_plus_a_nonce(client) -> None:
    csp = _csp(await client.get("/register"))
    script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "'self'" in script
    assert "'nonce-" in script
    assert "'unsafe-inline'" not in script, "a script nonce must not sit beside unsafe-inline"


async def test_styles_keep_unsafe_inline_for_now(client) -> None:
    """Inline `style=` attributes cannot carry a nonce; they need 'unsafe-inline' until moved."""
    csp = _csp(await client.get("/register"))
    style = next(d for d in csp.split(";") if d.strip().startswith("style-src"))
    assert "'unsafe-inline'" in style


async def test_the_policy_admits_the_fonts_and_calendar_the_pages_load(client) -> None:
    csp = _csp(await client.get("/register"))
    assert "https://fonts.googleapis.com" in csp, "Google Fonts stylesheet"
    assert "https://fonts.gstatic.com" in csp, "the font files themselves"


# ── the nonce ──
async def test_the_nonce_is_fresh_per_request(client) -> None:
    first = _nonce_of(await client.get("/register"))
    second = _nonce_of(await client.get("/register"))
    assert first and second and first != second, "a reused nonce is no nonce at all"


async def test_the_header_nonce_is_the_one_rendered_into_the_page(client) -> None:
    """Proves the plumbing end to end: the value in the header is the value inline scripts carry."""
    resp = await client.get("/register")
    nonce = _nonce_of(resp)
    assert f'nonce="{nonce}"' in resp.text, "the page's inline <script> must carry the sent nonce"


# ── report-only vs enforce ──
def test_report_only_by_default_enforce_by_flag() -> None:
    from web import csp

    assert csp.header_name(enforce=False) == "Content-Security-Policy-Report-Only"
    assert csp.header_name(enforce=True) == "Content-Security-Policy"


def test_the_policy_pins_exactly_the_nonce_it_is_given() -> None:
    from web import csp

    assert "'nonce-ABC.123_-'" in csp.policy("ABC.123_-")


def test_content_headers_present_in_dev_hsts_is_not() -> None:
    from web.app import security_headers

    dev = security_headers(is_dev=True)
    assert dev["X-Content-Type-Options"] == "nosniff"
    assert "Strict-Transport-Security" not in dev
    assert "Strict-Transport-Security" in security_headers(is_dev=False)
