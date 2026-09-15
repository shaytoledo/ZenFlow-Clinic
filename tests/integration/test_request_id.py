"""Phase 0 task 0.5 — request-id middleware: every response carries X-Request-ID, the id is bound
into the log context for the whole request (including background tasks), and the access log
line carries request_id + therapist_id + duration_ms."""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi import BackgroundTasks  # module level: FastAPI must resolve the annotation

from zenflow import logging as zlog

pytestmark = pytest.mark.integration


async def test_every_response_carries_a_request_id(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz")
    rid = resp.headers.get("x-request-id")
    assert rid and 8 <= len(rid) <= 64


async def test_caller_supplied_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert resp.headers["x-request-id"] == "trace-abc-123"


async def test_hostile_request_id_is_not_echoed_verbatim(client: httpx.AsyncClient) -> None:
    resp = await client.get("/healthz", headers={"X-Request-ID": "x" * 500})
    assert len(resp.headers["x-request-id"]) <= 64


async def test_access_log_carries_request_and_therapist_context(
    authenticated_client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="web.access"):
        resp = await authenticated_client.get(
            "/api/admin/flags", headers={"X-Request-ID": "req-log"}
        )
    assert resp.status_code == 200
    access = [r for r in caplog.records if r.name == "web.access"]
    assert access, "no access-log record emitted"
    rec = access[-1]
    attrs = {
        k: getattr(rec, k)
        for k in ("request_id", "therapist_id", "duration_ms", "status_code", "path", "method")
    }
    assert attrs["request_id"] == "req-log"
    assert attrs["therapist_id"] == authenticated_client.headers["X-Test-Therapist-Id"]
    assert attrs["duration_ms"] is not None and attrs["duration_ms"] >= 0
    assert attrs["status_code"] == 200
    assert attrs["path"] == "/api/admin/flags"
    assert attrs["method"] == "GET"


async def test_request_id_propagates_into_background_tasks(client: httpx.AsyncClient) -> None:
    from web.app import app

    seen: list[str | None] = []

    def _job() -> None:
        seen.append(zlog.get_context().get("request_id"))

    async def _handler(background_tasks: BackgroundTasks) -> dict[str, bool]:
        background_tasks.add_task(_job)
        return {"ok": True}

    app.add_api_route("/__test/background", _handler, methods=["GET"])
    try:
        resp = await client.get("/__test/background", headers={"X-Request-ID": "req-bg"})
        assert resp.status_code == 200
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/__test/background"
        ]
    assert seen == ["req-bg"]


async def test_context_does_not_leak_between_requests(client: httpx.AsyncClient) -> None:
    await client.get("/healthz", headers={"X-Request-ID": "req-first"})
    assert zlog.get_context().get("request_id") != "req-first"
