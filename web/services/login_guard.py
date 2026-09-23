"""
web/services/login_guard.py
────────────────────────────
Brute-force protection for the sign-in form (Phase 9.5).

`/register/signin` checked a password on every request, with nothing to slow an attacker guessing
one account's password or stuffing stolen credentials across many. This counts consecutive failures
in Redis and, once a threshold is crossed, **locks** the target for a cooldown that grows with each
further failure. Two independent scopes are tracked:

- **account** — the email being tried, so hammering one account locks it (whether or not it exists,
  so lockout timing never reveals which emails are real).
- **ip** — the source address, so spreading the same guesses across many accounts still trips a lock.

A successful sign-in clears both counters. Once an *existing* account crosses the threshold its
owner gets one security notification (they may need to change their password), sent best-effort so a
notification failure never affects the sign-in.

**Fail-open.** Every Redis call is guarded: if Redis is unavailable the guard allows the request. A
brute-force attack needs thousands of tries and Redis outages are rare and short; locking every
therapist out of the dashboard because the cache blinked is the worse failure.
"""

from __future__ import annotations

import logging

from fastapi import Request

from zenflow.settings import get_settings

logger = logging.getLogger(__name__)

BASE_LOCKOUT_SECONDS = 60  # the cooldown at the threshold failure
MAX_LOCKOUT_SECONDS = 1800  # capped at 30 minutes however many failures follow
COUNTER_TTL_SECONDS = 3600  # a run of failures is forgotten after an hour of quiet

_FAIL = "zenflow:loginguard:fail:{scope}:{key}"
_LOCK = "zenflow:loginguard:lock:{scope}:{key}"
_NOTIFIED = "zenflow:loginguard:notified:{key}"


def max_attempts() -> int:
    """Consecutive failures allowed before a lock; 0 disables the guard entirely."""
    return int(get_settings().flags.login_max_attempts)


def client_ip(request: Request) -> str:
    """The caller's address as the proxy in front of us reports it, else the socket peer."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _lockout_for(failures: int, limit: int) -> int:
    """Cooldown seconds for the n-th failure at/after the limit: base, then doubling, capped."""
    over = min(16, max(0, failures - limit))  # capped: the min() below dwarfs anything past a few
    return min(MAX_LOCKOUT_SECONDS, BASE_LOCKOUT_SECONDS << over)  # base, then doubling


async def locked_for(scope: str, key: str) -> int | None:
    """Seconds left on the lock for this scope/key, or None if it is not locked."""
    try:
        from bot.redis_client import get_async_redis

        ttl = await get_async_redis().ttl(_LOCK.format(scope=scope, key=key))
    except Exception as e:  # never lock (or unlock) on a Redis error
        logger.debug("login guard read skipped (%s:%s): %s", scope, key, type(e).__name__)
        return None
    return ttl if ttl and ttl > 0 else None


async def record_failure(scope: str, key: str) -> int:
    """Count one failed attempt; lock the scope/key once it reaches the threshold. Returns the
    running failure count (0 when the guard is disabled or Redis is unavailable)."""
    limit = max_attempts()
    if limit <= 0:
        return 0
    try:
        from bot.redis_client import get_async_redis

        redis = get_async_redis()
        fail_key = _FAIL.format(scope=scope, key=key)
        count = await redis.incr(fail_key)
        if count == 1:
            await redis.expire(fail_key, COUNTER_TTL_SECONDS)
        if count >= limit:
            await redis.set(_LOCK.format(scope=scope, key=key), "1", ex=_lockout_for(count, limit))
        return int(count)
    except Exception as e:  # a guard outage must not break sign-in
        logger.debug("login guard not counted (%s:%s): %s", scope, key, type(e).__name__)
        return 0


async def record_success(scope: str, key: str) -> None:
    """Clear the failure counter and any lock — a correct password ends the run."""
    try:
        from bot.redis_client import get_async_redis

        redis = get_async_redis()
        await redis.delete(
            _FAIL.format(scope=scope, key=key),
            _LOCK.format(scope=scope, key=key),
            _NOTIFIED.format(key=key),
        )
    except Exception as e:
        logger.debug("login guard not cleared (%s:%s): %s", scope, key, type(e).__name__)


# ── the three calls the sign-in endpoint makes ──
async def check_locked(request: Request, email: str) -> int | None:
    """The longest remaining lock across the source IP and this account, or None if clear."""
    if max_attempts() <= 0:
        return None
    waits = [
        await locked_for("ip", client_ip(request)),
        await locked_for("account", email),
    ]
    real = [w for w in waits if w]
    return max(real) if real else None


async def on_failure(request: Request, email: str, *, account_id: str | None) -> None:
    """Record a failed sign-in against both scopes; alert the owner of a real account once."""
    await record_failure("ip", client_ip(request))
    account_failures = await record_failure("account", email)
    limit = max_attempts()
    if account_id and limit > 0 and account_failures >= limit:
        await _notify_owner_once(account_id, email)


async def on_success(request: Request, email: str) -> None:
    """A correct password clears the account's and the IP's failure runs."""
    await record_success("account", email)
    await record_success("ip", client_ip(request))


async def _notify_owner_once(account_id: str, email: str) -> None:
    """One bell-icon security alert per lockout window, best-effort."""
    try:
        from bot.redis_client import get_async_redis

        # SET NX: only the first crossing in this window wins, so the owner is not spammed.
        first = await get_async_redis().set(
            _NOTIFIED.format(key=email), "1", ex=COUNTER_TTL_SECONDS, nx=True
        )
        if not first:
            return
    except Exception as e:
        logger.debug("login guard notify de-dupe skipped: %s", type(e).__name__)
        # fall through: better a possible duplicate alert than none

    try:
        from web.repositories import notification_repo

        notification_repo.create(
            therapist_id=account_id,
            kind="security",
            severity="warning",
            title="Repeated failed sign-ins",
            body=(
                "Several sign-ins to your account failed in a row and it was temporarily locked. "
                "If this was not you, change your password."
            ),
        )
    except Exception as e:  # never let an alert failure affect the sign-in
        logger.warning("could not record failed-sign-in alert: %s", type(e).__name__)
