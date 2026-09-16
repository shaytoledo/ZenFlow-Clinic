"""Named, expiring locks in the database (Phase 3.1).

    if leases.acquire("generation:42", holder="job-7", ttl_seconds=360): ...
    with leases.held("generation:42", "job-7", ttl_seconds=360) as got: ...

A lease belongs to one holder until it is released or expires. Acquire is a single atomic
statement, so two worker processes on the same database cannot both win. Expiry is what keeps a
crashed holder from blocking the name forever — choose a TTL longer than the work it guards.
The same holder may re-acquire (renew) its own lease.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from zenflow import clock


def _conn() -> Any:
    from bot.db import get_db

    return get_db()


def acquire(name: str, holder: str, *, ttl_seconds: float) -> bool:
    """Take (or renew) the lease. False when someone else holds an unexpired one."""
    now = clock.now_utc()
    expires_at = clock.to_iso(now + timedelta(seconds=ttl_seconds))
    cur = _conn().execute(
        """INSERT INTO leases (name, holder, expires_at) VALUES (?, ?, ?)
           ON CONFLICT(name) DO UPDATE SET holder=excluded.holder, expires_at=excluded.expires_at
           WHERE leases.holder = excluded.holder OR leases.expires_at < ?""",
        (name, holder, expires_at, clock.to_iso(now)),
    )
    return bool(cur.rowcount)


def release(name: str, holder: str) -> None:
    """Give the lease up. A no-op unless `holder` holds it."""
    _conn().execute("DELETE FROM leases WHERE name=? AND holder=?", (name, holder))


@contextmanager
def held(name: str, holder: str, *, ttl_seconds: float) -> Iterator[bool]:
    """Yield whether the lease was acquired; release it on exit if it was."""
    got = acquire(name, holder, ttl_seconds=ttl_seconds)
    try:
        yield got
    finally:
        if got:
            release(name, holder)
