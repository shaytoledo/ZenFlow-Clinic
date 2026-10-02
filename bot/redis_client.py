"""
Redis client singletons for ZenFlow.

- get_async_redis() — asyncio-compatible client (for FastAPI + async bot handlers)
- get_sync_redis()  — sync client (for LangChain history backend + sync helpers)

One connection pool per process and per kind, bounded, with timeouts and health checks (12.2.4):
a Redis that stops answering costs a request a few seconds, not a hung worker, and many containers
cannot open unbounded connections to one ElastiCache node. Server memory policy (`maxmemory`,
eviction) is the server's configuration — docker-compose / the ElastiCache parameter group —
never set from here: managed Redis refuses `CONFIG SET`.
"""

from typing import Any

import redis as syncredis
import redis.asyncio as aioredis

from bot.config import REDIS_URL

#: per process and per client kind (sync / async); the bots, web and worker each have their own
MAX_CONNECTIONS = 50
POOL_OPTIONS: dict[str, Any] = {
    "decode_responses": True,
    "max_connections": MAX_CONNECTIONS,
    "socket_connect_timeout": 5,
    # reads: pub/sub waits pass their own timeout (zenflow.events), so this only bounds a stuck reply
    "socket_timeout": 10,
    "health_check_interval": 30,  # a connection idle that long is PINGed before reuse
    "retry_on_timeout": True,
}

_async_client: aioredis.Redis | None = None
_sync_client: syncredis.Redis | None = None


def build_async(url: str = REDIS_URL) -> aioredis.Redis:
    return aioredis.from_url(url, **POOL_OPTIONS)


def build_sync(url: str = REDIS_URL) -> syncredis.Redis:
    return syncredis.from_url(url, **POOL_OPTIONS)


def get_async_redis() -> aioredis.Redis:
    global _async_client
    if _async_client is None:
        _async_client = build_async()
    return _async_client


def get_sync_redis() -> syncredis.Redis:
    global _sync_client
    if _sync_client is None:
        _sync_client = build_sync()
    return _sync_client
