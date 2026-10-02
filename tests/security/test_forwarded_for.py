"""SF-022 — a client cannot choose its own IP address with `X-Forwarded-For`.

The sign-in lockout's `ip` scope, the sign-up cap and the audit trail took the caller's address
from the first `X-Forwarded-For` hop — a header the client writes. Rotating it per request made
every attempt come from a "new" address: credential stuffing across accounts never tripped the IP
lock, the sign-up cap never fired, and the audit row recorded a forged IP.

The header is now believed only when the connection comes from a proxy in `ZF_TRUSTED_PROXIES`, and
then the client is the right-most hop that is not a trusted proxy.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
from starlette.requests import Request

pytestmark = pytest.mark.security

PW = "pw-Test-123"


def _request(peer: str, forwarded: str | None = None) -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded is not None else []
    return Request({"type": "http", "headers": headers, "client": (peer, 5000)})


@pytest.fixture
def trusted(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import zenflow.settings as settings_mod

    monkeypatch.setenv("ZF_TRUSTED_PROXIES", "10.0.0.0/8, 192.168.1.4")
    settings_mod.reset_settings()
    yield
    settings_mod.reset_settings()


# ── the rule ──
def test_the_header_is_ignored_by_default() -> None:
    from web.client_ip import client_ip

    assert client_ip(_request("203.0.113.9", "6.6.6.6")) == "203.0.113.9"


def test_a_trusted_proxy_names_the_client(trusted) -> None:
    from web.client_ip import client_ip

    assert client_ip(_request("10.0.0.5", "203.0.113.9")) == "203.0.113.9"


def test_hops_the_client_wrote_are_ignored(trusted) -> None:
    """`X-Forwarded-For: <spoof>, <real>` — our proxy appended the real one on the right."""
    from web.client_ip import client_ip

    assert client_ip(_request("10.0.0.5", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert client_ip(_request("10.0.0.5", "6.6.6.6, 203.0.113.9, 10.0.0.7")) == "203.0.113.9"


def test_an_untrusted_peer_cannot_vouch_for_anyone(trusted) -> None:
    from web.client_ip import client_ip

    assert client_ip(_request("203.0.113.9", "1.2.3.4")) == "203.0.113.9"


def test_garbage_in_the_header_falls_back_to_the_peer(trusted) -> None:
    from web.client_ip import client_ip

    assert client_ip(_request("10.0.0.5", "not-an-ip")) == "10.0.0.5"
    assert client_ip(_request("10.0.0.5", "")) == "10.0.0.5"


def test_a_malformed_setting_refuses_to_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    import zenflow.settings as settings_mod

    monkeypatch.setenv("ZF_TRUSTED_PROXIES", "10.0.0.0/8, nonsense")
    settings_mod.reset_settings()
    try:
        with pytest.raises(settings_mod.SettingsError, match="TRUSTED_PROXIES"):
            settings_mod.get_settings()
    finally:
        monkeypatch.delenv("ZF_TRUSTED_PROXIES")
        settings_mod.reset_settings()


# ── the attacks it stops ──
async def test_rotating_the_header_does_not_dodge_the_ip_lockout(client, make_therapist) -> None:
    from web.services import login_guard

    victim = make_therapist(email="fresh@example.com", password=PW, active=True)
    for i in range(login_guard.max_attempts()):
        await client.post(
            "/register/signin",
            data={"email": f"stuff{i}@nobody.example", "password": "wrong"},
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},  # a "new address" every time
        )
    resp = await client.post(
        "/register/signin",
        data={"email": victim["email"], "password": PW},
        headers={"X-Forwarded-For": "198.51.100.200"},
    )
    assert resp.status_code == 429, "the real source is locked, whatever the header claims"


async def test_rotating_the_header_does_not_dodge_the_signup_cap(client, monkeypatch) -> None:
    from web.services import rate_limit

    monkeypatch.setattr(rate_limit, "signup_per_minute", lambda: 2)
    codes = []
    for i in range(3):
        resp: httpx.Response = await client.post(
            "/register/signup",
            data={"name": "Dr Flood", "email": f"flood{i}@example.com", "password": PW},
            headers={"X-Forwarded-For": f"198.51.100.{i + 1}"},
            follow_redirects=False,
        )
        codes.append(resp.status_code)
    assert codes[2] == 429, codes


def test_the_audit_trail_records_the_real_address() -> None:
    """The middleware stamps audit rows with web.app._client_ip — a forgery never lands there."""
    from web.app import _client_ip

    assert _client_ip(_request("203.0.113.9", "6.6.6.6")) == "203.0.113.9"
