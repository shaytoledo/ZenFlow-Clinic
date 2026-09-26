"""Phase 11.1 — the shared per-caller rate-limit primitive (9.5).

`rate_limit.hit(caller, per_minute)` is the one budget behind the booking API, the AI endpoints and
the public sign-up form. The behaviours that matter: it counts within a one-minute window and returns
a wait time once a caller is over budget, `per_minute<=0` disables it, and — the security-relevant one
— it **fails open** (returns None, allowing the request) if Redis is unavailable, so a monitoring
outage never takes the clinic down.
"""

from __future__ import annotations

import pytest

from web.services import rate_limit
from zenflow.settings import get_settings

pytestmark = pytest.mark.integration


async def test_under_budget_is_allowed_then_over_budget_waits(fake_redis) -> None:
    caller = "test:caller:1"
    assert await rate_limit.hit(caller, 3) is None  # 1st
    assert await rate_limit.hit(caller, 3) is None  # 2nd
    assert await rate_limit.hit(caller, 3) is None  # 3rd — still within budget
    wait = await rate_limit.hit(caller, 3)  # 4th — over
    assert isinstance(wait, int) and 1 <= wait <= rate_limit.WINDOW_SECONDS


async def test_separate_callers_have_separate_budgets(fake_redis) -> None:
    assert await rate_limit.hit("caller:a", 1) is None
    assert await rate_limit.hit("caller:a", 1) is not None, "a is now over its budget"
    assert await rate_limit.hit("caller:b", 1) is None, "b's budget is independent"


async def test_zero_means_no_limit(fake_redis) -> None:
    for _ in range(10):
        assert await rate_limit.hit("test:caller:unlimited", 0) is None


async def test_it_fails_open_when_redis_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom():
        raise RuntimeError("redis is down")

    # No fake_redis here — the real lookup is replaced with a failure.
    monkeypatch.setattr("bot.redis_client.get_async_redis", _boom)
    # Even far over any budget, a Redis outage must not block the request.
    assert await rate_limit.hit("test:caller:x", 1) is None
    assert await rate_limit.hit("test:caller:x", 1) is None


def test_window_key_is_namespaced_and_per_minute() -> None:
    assert rate_limit.window_key("t:1", 29_000_001) == "zenflow:ratelimit:v1:t:1:29000001"


def test_flag_helpers_read_the_configured_limits() -> None:
    flags = get_settings().flags
    assert rate_limit.ai_per_minute() == int(flags.ai_rate_per_minute)
    assert rate_limit.signup_per_minute() == int(flags.signup_per_minute)
