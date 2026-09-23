"""Plan 9.5 — login brute-force: per-IP and per-account progressive lockout.

`/register/signin` verified a password on every request, so an attacker could try passwords as fast
as the network allowed — against one account (guessing its password) or across many (credential
stuffing) — and nothing slowed them down or told the account owner. Now repeated failures lock the
account *and* the source IP for a growing cooldown, and the owner is notified.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.security

PW = "pw-Test-123"


async def _signin(client: httpx.AsyncClient, email: str, password: str) -> httpx.Response:
    return await client.post("/register/signin", data={"email": email, "password": password})


# ── the service, directly ──
async def test_the_counter_locks_only_at_the_threshold() -> None:
    from web.services import login_guard

    n = login_guard.max_attempts()
    assert n >= 1
    for i in range(n - 1):
        await login_guard.record_failure("account", "svc@example.com")
        assert await login_guard.locked_for("account", "svc@example.com") is None, f"early at {i}"
    await login_guard.record_failure("account", "svc@example.com")
    locked = await login_guard.locked_for("account", "svc@example.com")
    assert isinstance(locked, int) and locked > 0, "the threshold failure should lock"


async def test_success_clears_the_counter() -> None:
    from web.services import login_guard

    for _ in range(login_guard.max_attempts()):
        await login_guard.record_failure("account", "clear@example.com")
    assert await login_guard.locked_for("account", "clear@example.com")
    await login_guard.record_success("account", "clear@example.com")
    assert await login_guard.locked_for("account", "clear@example.com") is None


async def test_the_lockout_grows_with_each_further_failure() -> None:
    from web.services import login_guard

    key = "grow@example.com"
    for _ in range(login_guard.max_attempts()):
        await login_guard.record_failure("account", key)
    first = await login_guard.locked_for("account", key)
    await login_guard.record_failure("account", key)
    second = await login_guard.locked_for("account", key)
    assert first and second and second >= first, "the cooldown must not shrink on more failures"


async def test_disabled_when_max_attempts_is_zero(monkeypatch) -> None:
    from web.services import login_guard

    monkeypatch.setattr(login_guard, "max_attempts", lambda: 0)
    for _ in range(20):
        await login_guard.record_failure("account", "off@example.com")
    assert await login_guard.locked_for("account", "off@example.com") is None


# ── through the sign-in endpoint ──
async def test_repeated_bad_passwords_lock_even_the_right_one(client, make_therapist) -> None:
    from web.services import login_guard

    make_therapist(email="lock@example.com", password=PW, active=True)
    for _ in range(login_guard.max_attempts()):
        resp = await _signin(client, "lock@example.com", "wrong")
        assert resp.status_code == 200, "an ordinary bad attempt re-renders the form"

    locked = await _signin(client, "lock@example.com", PW)  # the CORRECT password now
    assert locked.status_code == 429, "once locked, even valid credentials wait"
    assert locked.headers.get("retry-after"), "a locked response tells the client how long to wait"


async def test_one_ip_trying_many_accounts_is_throttled(client, make_therapist) -> None:
    """Credential stuffing: no single account is hit hard, but the source IP is."""
    from web.services import login_guard

    victim = make_therapist(email="fresh@example.com", password=PW, active=True)
    for i in range(login_guard.max_attempts()):
        await _signin(client, f"stuff{i}@nobody.example", "wrong")

    resp = await _signin(client, victim["email"], PW)  # a different, valid account
    assert resp.status_code == 429, "the IP is locked even for an account it never targeted"


async def test_the_owner_is_notified_of_repeated_failures(client, make_therapist) -> None:
    from web.repositories import notification_repo
    from web.services import login_guard

    t = make_therapist(email="notify@example.com", password=PW, active=True)
    for _ in range(login_guard.max_attempts()):
        await _signin(client, "notify@example.com", "wrong")

    notes = notification_repo.list_for_therapist(t["id"])
    assert any(
        "sign" in (n.get("title", "") + n.get("body", "")).lower() or n.get("kind") == "security"
        for n in notes
    ), f"the account owner should see a security alert, got {[n.get('kind') for n in notes]}"


async def test_a_stranger_hitting_an_unknown_email_notifies_nobody(client) -> None:
    """No account, no owner to alert — and it must not error."""
    from web.services import login_guard

    for _ in range(login_guard.max_attempts()):
        resp = await _signin(client, "ghost@nowhere.example", "wrong")
    assert resp.status_code in (200, 429)  # locked eventually, never a 500
