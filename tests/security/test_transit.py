"""Plan 9.11 — data in transit (ADR-14).

Two guarantees, both enforced by code so they cannot silently regress:

1. **Nothing disables TLS verification.** No `verify=False`, no `ssl.CERT_NONE`, no
   `check_hostname = False`, no `ssl._create_unverified_context` anywhere in the source — a single
   such call would let a network attacker read clinical data off an outbound request.
2. **Every non-local URL must be TLS** outside dev — `https://` for HTTP endpoints and `rediss://`
   for Redis (which holds relay + LLM history, i.e. clinical data). This is enforced by
   `zenflow.settings` (ADR-14); this test pins that a plain `http://`/`redis://` to a non-local host
   is refused in a prod-like environment.

Cloud-side pieces — the HTTP→HTTPS redirect, ACM / Let's Encrypt certificates, a private network for
bot⇄web⇄worker — are Phase 12 infrastructure and are documented in `docs/TRANSPORT.md`.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from zenflow import settings as S

pytestmark = pytest.mark.security

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCANNED_DIRS = ("bot", "web", "zenflow", "startup")

# Patterns that disable TLS verification. If a legitimate need ever arises, the exception must be
# explicit and reviewed — add `# nosec transit` on the line and it is skipped here (and by bandit).
_INSECURE = re.compile(
    r"verify\s*=\s*False"
    r"|CERT_NONE"
    r"|check_hostname\s*=\s*False"
    r"|_create_unverified_context"
)


def _source_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for name in SCANNED_DIRS:
        files.extend((ROOT / name).rglob("*.py"))
    return files


def test_no_source_disables_tls_verification() -> None:
    offenders: list[str] = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _INSECURE.search(line) and "# nosec transit" not in line:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")
    assert offenders == [], "TLS verification must never be disabled:\n" + "\n".join(offenders)


def test_no_plain_http_to_a_non_local_host_in_source() -> None:
    """Outbound calls must be https. Localhost, example hosts, XML namespaces and comments are fine."""
    url = re.compile(r"http://([A-Za-z0-9.\-]+)")
    allowed = ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    offenders: list[str] = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for host in url.findall(line):
                if host.startswith(allowed) or "example" in host or "w3.org" in host:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}: {stripped}")
    assert offenders == [], "plain http:// to a non-local host:\n" + "\n".join(offenders)


# ── the settings TLS enforcement (ADR-14), pinned here too ──
_PROD = {
    "ENV": "prod",
    "SESSION_SECRET": "s" * 48,
    "TOKEN_ENCRYPTION_KEY": "k" * 48,
    "OLLAMA_HOST": "https://ollama.internal.example",
    "REDIS_URL": "rediss://:redis-secret@redis.internal.example:6380/0",
    "GOOGLE_REDIRECT_URI": "https://clinic.example/auth/callback",
    "GOOGLE_REG_REDIRECT_URI": "https://clinic.example/register/google/callback",
    "GOOGLE_GMAIL_REDIRECT_URI": "https://clinic.example/auth/gmail/callback",
}


def _build(env, **override):
    for key in list(S.ALL_ENV_VARS):
        env.delenv(key, raising=False)
    for key, value in {**_PROD, **override}.items():
        env.setenv(key, value)
    S.reset_settings()
    return S.get_settings()


def test_prod_refuses_plain_redis_to_a_non_local_host(monkeypatch) -> None:
    with pytest.raises(S.SettingsError, match="REDIS_URL"):
        _build(monkeypatch, REDIS_URL="redis://redis.internal.example:6379/0")
    S.reset_settings()


def test_prod_accepts_tls_everywhere(monkeypatch) -> None:
    settings = _build(monkeypatch)  # the all-TLS _PROD base boots cleanly
    assert settings.redis_url.startswith("rediss://")
    S.reset_settings()


# ── Redis AUTH (A9): TLS is not enough — a remote Redis holds clinical data ──
def test_prod_refuses_a_non_local_redis_without_auth(monkeypatch) -> None:
    with pytest.raises(S.SettingsError, match="REDIS_URL"):
        _build(monkeypatch, REDIS_URL="rediss://redis.internal.example:6380/0")  # TLS but no AUTH
    S.reset_settings()


def test_prod_accepts_a_non_local_redis_with_auth(monkeypatch) -> None:
    settings = _build(monkeypatch, REDIS_URL="rediss://:s3cret@redis.internal.example:6380/0")
    assert settings.redis_url.startswith("rediss://")
    S.reset_settings()


def test_local_redis_needs_no_auth(monkeypatch) -> None:
    settings = _build(monkeypatch, REDIS_URL="redis://localhost:6379/0", ENV="dev")
    assert settings.redis_url.startswith("redis://")
    S.reset_settings()
