"""
Redis client singletons for ZenFlow.

- get_async_redis() — asyncio-compatible client (for FastAPI + async bot handlers)
- get_sync_redis()  — sync client (for LangChain history backend + sync helpers)
"""
import redis.asyncio as aioredis
import redis as syncredis

from bot.config import REDIS_URL

_async_client: aioredis.Redis | None = None
_sync_client: syncredis.Redis | None = None


def get_async_redis() -> aioredis.Redis:
    global _async_client
    if _async_client is None:
        _async_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _async_client


def get_sync_redis() -> syncredis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = syncredis.from_url(REDIS_URL, decode_responses=True)
    return _sync_client
