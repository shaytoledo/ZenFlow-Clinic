"""
web/services/health.py
───────────────────────
What the system can reach (Phase 8.4), for `/readyz` and `/api/admin/metrics`.

Two questions, deliberately kept apart:

**Can this process serve traffic?** — `readiness()`. Only the *required* dependencies count: the
database, and a configuration that loads. Redis, Ollama, Telegram and Google are checked and
reported, but the dashboard answers requests without them (sessions are signed cookies, the AI
degrades to fallbacks, a bot being down is a bot problem), so they never take the app out of a
load balancer's rotation.

**What is up right now?** — `dependencies()`, the operator's view, which adds the slow network
checks.

Every check catches its own failure and reports `ok: false` with a short reason. Nothing here may
raise: a health endpoint that 500s is worse than no health endpoint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

import httpx

logger = logging.getLogger(__name__)

#: how long a probe may wait on a network dependency before calling it unreachable
NETWORK_TIMEOUT_SECONDS = 3.0


class Check(NamedTuple):
    name: str
    ok: bool
    required: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "required": self.required, "detail": self.detail}


# ── the individual probes (patched in tests; each may raise, the caller catches) ──
def _database_ok() -> bool:
    from bot.db import get_db

    return bool(get_db().execute("SELECT 1").fetchone())


def _settings_ok() -> bool:
    from zenflow.settings import get_settings

    return bool(get_settings().env)


async def _redis_ok() -> tuple[bool, str]:
    from bot.redis_client import get_async_redis

    await get_async_redis().ping()
    return True, "reachable"


async def _ollama_ok() -> tuple[bool, str]:
    from bot.config import OLLAMA_HOST, OLLAMA_MODEL

    async with httpx.AsyncClient(timeout=NETWORK_TIMEOUT_SECONDS) as client:
        models = (await client.get(f"{OLLAMA_HOST}/api/tags")).json().get("models", [])
    names = [str(m.get("name", "")) for m in models]
    ready = any(OLLAMA_MODEL in name for name in names)
    return ready, f"model '{OLLAMA_MODEL}' ready" if ready else f"model '{OLLAMA_MODEL}' missing"


async def _telegram_ok() -> tuple[bool, str]:
    from bot.config import TELEGRAM_TOKEN
    from web.services.telegram_service import check_bot

    if not TELEGRAM_TOKEN:
        return False, "no token configured"
    ok, detail = await check_bot(TELEGRAM_TOKEN)
    return bool(ok), str(detail)[:80]


def _google_ok(therapist_id: str | None) -> tuple[bool, str]:
    from web.gcal import is_authenticated

    if not therapist_id:
        return False, "no therapist in this request"
    connected = bool(is_authenticated(therapist_id))
    return connected, "connected" if connected else "not connected"


# ── the two views ──
async def readiness() -> tuple[bool, list[Check]]:
    """(ready, checks) — ready is false only when a *required* dependency is unreachable."""
    checks = [
        _sync_check("database", _database_ok, required=True),
        _sync_check("configuration", _settings_ok, required=True),
        await _async_check("redis", _redis_ok, required=False),
    ]
    return all(check.ok for check in checks if check.required), checks


async def dependencies(therapist_id: str | None = None) -> list[Check]:
    """Everything the operator wants to see, including the slow network checks."""
    ready_checks = (await readiness())[1]
    slow = await asyncio.gather(
        _async_check("ollama", _ollama_ok, required=False),
        _async_check("telegram", _telegram_ok, required=False),
    )
    google_ok, google_detail = _guard(lambda: _google_ok(therapist_id), (False, "unavailable"))
    return [*ready_checks, *slow, Check("google_calendar", google_ok, False, google_detail)]


def _sync_check(name: str, probe: Any, *, required: bool) -> Check:
    try:
        ok = bool(probe())
        return Check(name, ok, required, "ok" if ok else "unavailable")
    except Exception as exc:
        logger.warning("health check %s failed: %s", name, exc)
        return Check(name, False, required, _reason(exc))


async def _async_check(name: str, probe: Any, *, required: bool) -> Check:
    try:
        ok, detail = await asyncio.wait_for(probe(), timeout=NETWORK_TIMEOUT_SECONDS + 1)
        return Check(name, bool(ok), required, str(detail)[:80])
    except Exception as exc:
        logger.warning("health check %s failed: %s", name, exc)
        return Check(name, False, required, _reason(exc))


def _guard(probe: Any, fallback: tuple[bool, str]) -> tuple[bool, str]:
    try:
        ok, detail = probe()
        return bool(ok), str(detail)
    except Exception:
        return fallback


def _reason(exc: Exception) -> str:
    """A short, safe reason — the class and a trimmed message, never a stack or a URL with a key."""
    from zenflow.logging import redact

    return redact(f"{type(exc).__name__}: {exc}")[:80]
