"""Phase 6.4 — a patient who cannot be messaged: the 24h follow-up becomes the therapist's call.

Plan test: complete a session for a manual patient → exactly one persistent notification, no send
attempt, and the manual form is present in the page. Plus: the alert links to the session, it is
never raised twice, recording the outcome settles it, and the dashboard's call list comes from the
database.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

import pytest
from freezegun import freeze_time

import bot.db as dbmod
from web.repositories import followup_repo
from zenflow import queue as q
from zenflow import worker as w

pytestmark = pytest.mark.integration

FROZEN = "2026-03-01T12:00:00Z"
PW = "pw-Test-123"


def _worker() -> w.Worker:
    import bot.services.followup_jobs  # noqa: F401  (registers the handlers)

    return w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="test")


def _alerts(kind: str = "followup_no_channel") -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in dbmod.get_db().execute(
            "SELECT * FROM notifications WHERE kind=? ORDER BY id", (kind,)
        )
    ]


def _slug(apt: dict[str, Any]) -> str:
    return f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"


@pytest.fixture
def manual_session(authenticated_client, make_appointment, make_patient, make_treatment_notes):
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(
        therapist={"id": tid},
        patient=make_patient("Ruth <b>Cohen</b>", manual=True),
        apt_date="2026-03-01",
        apt_time="10:00",
    )
    make_treatment_notes(apt)
    return authenticated_client, apt, tid


async def _complete(client: Any, apt: dict[str, Any]) -> None:
    resp = await client.post(f"/api/treatment-notes/{_slug(apt)}/complete", json={})
    assert resp.status_code == 200, resp.text


async def test_completing_a_manual_session_raises_one_call_alert_and_sends_nothing(
    manual_session, fake_telegram
) -> None:
    client, apt, _tid = manual_session
    worker = _worker()
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        (alert,) = _alerts()
        assert alert["persistent"] == 1 and alert["severity"] == "warning"
        assert alert["appointment_id"] == apt["id"]
        assert alert["title"] == "Follow-up due for Ruth <b>Cohen</b> — no messaging channel"
        assert "Due 2026-03-02T12:00:00Z" in alert["body"]

        frozen.tick(timedelta(hours=24, minutes=1))
        await worker.run_once()
        assert fake_telegram.calls == [], "no send attempt to a patient without a channel"

        page = await client.get(f"/treatment/{_slug(apt)}")
    assert 'id="manual-feedback-card"' in page.text and 'id="mf-save-btn"' in page.text
    assert 'id="mf-due"' in page.text
    row = followup_repo.get(apt["id"])
    assert row is not None and row["status"] == "no_channel"


async def test_the_alert_is_raised_once_ever(manual_session) -> None:
    from bot.services.followup_scheduler import reconcile

    client, apt, tid = manual_session
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=1))
        await _complete(client, apt)  # completed again
        reconcile()
        assert len(_alerts()) == 1

        (alert,) = _alerts()
        resp = await client.post(f"/api/notifications/{alert['id']}/resolve")
        assert resp.status_code == 200
        frozen.tick(timedelta(minutes=30))
        reconcile()  # the sweep must not bring back an alert the therapist dealt with
    assert len(_alerts()) == 1 and _alerts()[0]["resolved_at"] is not None


async def test_the_bell_links_to_the_session_form(manual_session) -> None:
    client, apt, _tid = manual_session
    await _complete(client, apt)
    items = (await client.get("/api/notifications")).json()["items"]
    call = next(i for i in items if i["kind"] == "followup_no_channel")
    assert call["link"] == f"/treatment/{_slug(apt)}#manual-feedback-card"
    others = [i for i in items if i["kind"] != "followup_no_channel" and i["appointment_id"]]
    assert others and all(i["link"] == f"/treatment/{_slug(apt)}" for i in others)
    assert not any(k.startswith("apt_") for i in items for k in i), "join columns stay internal"


async def test_an_alert_without_a_session_of_ours_has_no_link(authenticated_client) -> None:
    from web.services import notification_service

    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    notification_service.alert_send_failed(tid, 999_999, 1, "Ghost", "boom")  # no such session
    dbmod.get_db().execute(
        "INSERT INTO notifications (therapist_id, kind, severity, title, created_at) "
        "VALUES (?, 'info', 'info', 'hello', '2026-03-01T00:00:00Z')",
        (tid,),
    )
    items = (await authenticated_client.get("/api/notifications")).json()["items"]
    assert items and all(i["link"] is None for i in items)


def test_session_links_are_encoded() -> None:
    from web.services.notification_service import session_link

    assert session_link(-5, "2026-03-01", "10:00") == "/treatment/-5/2026-03-01/10-00"
    assert session_link(5, "2026/03?x", "10:00", "#a") == "/treatment/5/2026%2F03%3Fx/10-00#a"
    assert session_link(None, "2026-03-01", "10:00") is None
    assert session_link(5, "", "10:00") is None


async def test_recording_the_outcome_settles_the_call(manual_session) -> None:
    client, apt, _tid = manual_session
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        frozen.tick(timedelta(hours=25))
        due = (await client.get("/api/my/alerts")).json()
        assert due["count"] == 1
        resp = await client.post(
            f"/api/treatment-notes/{_slug(apt)}/manual-feedback",
            json={"rating": 4, "notes": "Called — much better"},
        )
        assert resp.status_code == 200
        assert (await client.get("/api/my/alerts")).json() == {"alerts": [], "count": 0}
    (alert,) = _alerts()
    assert alert["resolved_at"] is not None
    row = followup_repo.get(apt["id"])
    assert row is not None
    assert (row["status"], row["source"], row["improvement_rating"]) == (
        "completed",
        "therapist_manual",
        4,
    )


async def test_the_dashboard_call_list_is_due_calls_of_this_therapist_only(
    manual_session, make_therapist, make_appointment, make_patient, make_treatment_notes, login_as
) -> None:
    client, apt, _tid = manual_session
    other = make_therapist(email="other-nc@example.com", password=PW)
    other_apt = make_appointment(
        therapist=other, patient=make_patient("Other", manual=True), apt_time="11:00"
    )
    make_treatment_notes(other_apt)
    other_client = await login_as(other)
    with freeze_time(FROZEN, ignore=["itsdangerous"]) as frozen:
        await _complete(client, apt)
        await _complete(other_client, other_apt)
        assert (await client.get("/api/my/alerts")).json()["count"] == 0, "not due yet"
        frozen.tick(timedelta(hours=24))
        body = (await client.get("/api/my/alerts")).json()
    assert body["count"] == 1
    (alert,) = body["alerts"]
    assert alert == {
        "appointment_id": apt["id"],
        "patient_name": "Ruth <b>Cohen</b>",
        "due_at": "2026-03-02T12:00:00Z",
        "message": "follow-up due — call them and record the outcome",
        "link": f"/treatment/{_slug(apt)}#manual-feedback-card",
    }


async def test_the_notes_tell_the_page_the_checkin_state(manual_session) -> None:
    client, apt, _tid = manual_session
    before = (await client.get(f"/api/treatment-notes/{_slug(apt)}")).json()
    assert before["followup"] is None
    with freeze_time(FROZEN, ignore=["itsdangerous"]):  # the session cookie lives in this clock
        await _complete(client, apt)
        after = (await client.get(f"/api/treatment-notes/{_slug(apt)}")).json()
    assert after["followup"] == {
        "status": "no_channel",
        "channel": "none",
        "scheduled_for": "2026-03-02T12:00:00Z",
        "source": "patient",
    }


def test_the_pages_never_render_alert_text_as_markup() -> None:
    """Patient names come from Telegram profiles (see the <b> in the fixture above)."""
    from tests.integration import treatment_source as ts

    dashboard = (ts.WEB / "templates/dashboard.html").read_text(encoding="utf-8")
    script = dashboard[dashboard.index("async function loadAlerts()") :]
    script = script[: script.index("async function dismissAlerts()")]
    assert "innerHTML" not in script and "replaceChildren" in script
    base = (ts.WEB / "templates/base.html").read_text(encoding="utf-8")
    link = re.search(r"function _notifLinkHtml\(link\) \{.*?\n  \}", base, re.DOTALL)
    assert link is not None
    assert "startsWith('/treatment/')" in link.group(0) and "&quot;" in link.group(0)
    load = (ts.WEB / "static/js/treatment/load.js").read_text(encoding="utf-8")
    due = load[load.index("function showFollowupDue(") :]
    assert "innerHTML" not in due and "textContent" in due
