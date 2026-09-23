"""Phase 0.5 — F11 (/api/status needs auth, /healthz is public), SF-005 (every /api route
needs a session), session-cookie flags, and the fail-fast boot (F7) in a real subprocess."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from zenflow.settings import DEFAULT_SESSION_SECRET

pytestmark = pytest.mark.security

REPO_ROOT = Path(__file__).resolve().parents[2]


async def test_healthz_is_public_and_minimal(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


@pytest.mark.parametrize("path", ["/api/status", "/api/smtp-status"])
async def test_status_endpoints_require_a_session(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 401


async def test_status_works_when_signed_in(authenticated_client: httpx.AsyncClient) -> None:
    resp = await authenticated_client.get("/api/status")
    assert resp.status_code == 200
    assert "redis" in resp.json()


async def test_auth_is_checked_before_body_validation(client: httpx.AsyncClient) -> None:
    """An anonymous caller must not learn the request schema from a 422."""
    for method, path in [
        ("POST", "/api/appointments"),
        ("POST", "/api/messages/send"),
        ("POST", "/api/availability"),
        ("POST", "/api/treatment-notes/1/2026-01-01/10-00/send-recommendations"),
    ]:
        resp = await client.request(method, path, json={})
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


async def test_session_cookie_flags(client: httpx.AsyncClient, make_therapist) -> None:
    t = make_therapist(email="c@example.com", password="pw-Test-123")
    resp = await client.post(
        "/register/signin", data={"email": t["email"], "password": t["password"]}
    )
    set_cookie = resp.headers.get("set-cookie", "").lower()
    assert "zf_session=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "max-age=" in set_cookie
    # ENV=test counts as dev: the cookie must NOT be Secure-only, or local http would break
    assert "secure" not in set_cookie


def test_cookie_is_secure_only_outside_dev() -> None:
    from web.app import session_cookie_kwargs

    assert session_cookie_kwargs(is_dev=True)["https_only"] is False
    assert session_cookie_kwargs(is_dev=False)["https_only"] is True
    for kw in (session_cookie_kwargs(is_dev=True), session_cookie_kwargs(is_dev=False)):
        assert kw["same_site"] == "lax"
        # the cookie lifetime tracks the session policy's absolute limit (9.2), not a fixed 30 days
        from web.session_policy import max_hours

        assert kw["max_age"] == max_hours() * 3600


@pytest.mark.slow
def test_app_refuses_to_boot_with_default_secret_in_prod(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "ENV": "prod",
        "SESSION_SECRET": DEFAULT_SESSION_SECRET,
        "ZENFLOW_DB_PATH": str(tmp_path / "boot.db"),
    }
    env.pop("TOKEN_ENCRYPTION_KEY", None)
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-c", "import web.app"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode != 0
    assert "SettingsError" in proc.stderr
    assert "SESSION_SECRET" in proc.stderr
    assert "TOKEN_ENCRYPTION_KEY" in proc.stderr
