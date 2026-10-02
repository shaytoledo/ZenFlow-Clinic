"""Phase 12.2.4 — the Redis clients are bounded, time out, and never reconfigure the server."""

from __future__ import annotations

from bot import redis_client


def test_the_pool_is_bounded_and_times_out() -> None:
    for client in (
        redis_client.build_sync("redis://localhost:6379/0"),
        redis_client.build_async("redis://localhost:6379/0"),
    ):
        pool = client.connection_pool
        assert pool.max_connections == redis_client.MAX_CONNECTIONS
        kwargs = pool.connection_kwargs
        assert kwargs["socket_connect_timeout"] == 5 and kwargs["socket_timeout"] == 10
        assert kwargs["health_check_interval"] == 30 and kwargs["decode_responses"] is True


def test_the_app_never_sends_config_set() -> None:
    """ElastiCache refuses CONFIG SET; the memory policy belongs to the server's configuration."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for folder in ("bot", "web", "zenflow"):
        for path in (root / folder).rglob("*.py"):
            assert "config_set(" not in path.read_text(encoding="utf-8"), path
