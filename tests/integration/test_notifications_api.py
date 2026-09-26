"""Phase 11.2 — the notification-bell REST API.

The five endpoints behind the topbar bell (list, unread-count, mark-one-read, mark-all-read, resolve)
were only partially covered (~72%). These tests drive each through the signed-in client, and confirm
an anonymous caller is refused.
"""

from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.integration


def _seed_notification(therapist_id: str, title: str = "Ping") -> int:
    from bot.db import get_db

    cur = get_db().execute(
        "INSERT INTO notifications (therapist_id, kind, severity, title, body) "
        "VALUES (?, 'test', 'info', ?, '')",
        (therapist_id, title),
    )
    return int(cur.lastrowid or 0)


def _tid(client: httpx.AsyncClient) -> str:
    return client.headers["X-Test-Therapist-Id"]


async def test_anonymous_caller_is_refused(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/notifications")
    assert resp.status_code == 401


async def test_list_returns_items_and_unread_count(authenticated_client: httpx.AsyncClient) -> None:
    tid = _tid(authenticated_client)
    _seed_notification(tid, "One")
    _seed_notification(tid, "Two")
    resp = await authenticated_client.get("/api/notifications")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["unread"] == 2


async def test_unread_count_endpoint(authenticated_client: httpx.AsyncClient) -> None:
    _seed_notification(_tid(authenticated_client))
    resp = await authenticated_client.get("/api/notifications/unread-count")
    assert resp.status_code == 200 and resp.json() == {"unread": 1}


async def test_mark_one_read_decrements_the_count(authenticated_client: httpx.AsyncClient) -> None:
    tid = _tid(authenticated_client)
    nid = _seed_notification(tid)
    _seed_notification(tid)  # a second, left unread
    resp = await authenticated_client.post(f"/api/notifications/{nid}/read")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    after = await authenticated_client.get("/api/notifications/unread-count")
    assert after.json() == {"unread": 1}, "only the marked notification is now read"


async def test_mark_all_read_clears_the_count(authenticated_client: httpx.AsyncClient) -> None:
    tid = _tid(authenticated_client)
    _seed_notification(tid)
    _seed_notification(tid)
    resp = await authenticated_client.post("/api/notifications/read-all")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    after = await authenticated_client.get("/api/notifications/unread-count")
    assert after.json() == {"unread": 0}


async def test_resolve_marks_a_notification_resolved(
    authenticated_client: httpx.AsyncClient,
) -> None:
    tid = _tid(authenticated_client)
    nid = _seed_notification(tid)
    resp = await authenticated_client.post(f"/api/notifications/{nid}/resolve")
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    from bot.db import get_db

    row = get_db().execute("SELECT resolved_at FROM notifications WHERE id=?", (nid,)).fetchone()
    assert row["resolved_at"], "the notification is stamped resolved"
