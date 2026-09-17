"""End-to-end fixtures: the real app served over HTTP, driven by the installed Chrome.

Playwright uses the system browser (`ZF_E2E_BROWSER`, default `chrome`; `msedge` works too),
so no browser download is needed. Without playwright or a browser the tests skip.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as pw:
        try:
            launched = pw.chromium.launch(channel=os.environ.get("ZF_E2E_BROWSER", "chrome"))
        except Exception as exc:  # no such browser on this machine
            pytest.skip(f"no browser for the e2e tests: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def live_server(db: Any, fake_redis: Any) -> Iterator[str]:
    """web.app on a free local port, in a thread, sharing this test's database and fakeredis."""
    import uvicorn

    from web.app import app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the live server did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=15)
