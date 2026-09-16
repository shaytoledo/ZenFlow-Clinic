"""Wake-up notifications for live pages (Phase 3.4).

    notify_treatment(appointment_id)          # after any status write — best effort, never raises
    async with subscribe_treatment(apt) as wait:
        changed = await wait(timeout_seconds)  # True when a notification arrived

A notification carries no data: it only tells a listener to read the database again, which stays
the single source of truth. Delivery is best effort (Redis pub/sub has no replay), so listeners
also re-check on a timer; a missed message costs latency, never correctness.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

CHANNEL_PREFIX = "zenflow:treatment:"


def treatment_channel(appointment_id: int) -> str:
    return f"{CHANNEL_PREFIX}{int(appointment_id)}"


def notify_treatment(appointment_id: int) -> None:
    """Tell anyone watching this session that its notes changed. Never raises."""
    try:
        from bot.redis_client import get_sync_redis

        get_sync_redis().publish(treatment_channel(appointment_id), "changed")
    except Exception as e:  # a notification must never fail the write it follows
        logger.debug("treatment notification not sent for %s: %s", appointment_id, e)


@contextlib.asynccontextmanager
async def subscribe_treatment(
    appointment_id: int,
) -> AsyncIterator[Callable[[float], Awaitable[bool]]]:
    """Yield `wait(timeout) -> bool`: True when a notification arrived within `timeout` seconds.

    Without Redis, `wait` just sleeps for the timeout, so callers degrade to periodic re-checks.
    """
    channel = treatment_channel(appointment_id)
    pubsub: Any = None
    try:
        from bot.redis_client import get_async_redis

        pubsub = get_async_redis().pubsub()
        await pubsub.subscribe(channel)
    except Exception as e:
        logger.info(
            "live updates unavailable for %s, re-checking on a timer: %s", appointment_id, e
        )
        pubsub = None

    async def wait(timeout: float) -> bool:
        if pubsub is None:
            await asyncio.sleep(timeout)
            return False
        try:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=timeout)
        except Exception as e:
            logger.info("live update wait failed for %s: %s", appointment_id, e)
            await asyncio.sleep(timeout)
            return False
        return message is not None

    try:
        yield wait
    finally:
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
            with contextlib.suppress(Exception):
                await pubsub.aclose()
