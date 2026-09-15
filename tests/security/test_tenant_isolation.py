"""Phase 0.5 / F6 — multi-tenant isolation.

Therapist A (signed in) attacks therapist B's data: clinical notes, appointments, patient
records, relay conversations, availability slots. Every attempt must be refused (403 or 404)
and must leave B's rows untouched.
"""

from __future__ import annotations

import json

import pytest

from web.repositories import availability_repo, treatment_repo

pytestmark = pytest.mark.security

A_EMAIL, B_EMAIL = "a@example.com", "b@example.com"
PW = "pw-Test-123"


@pytest.fixture
async def two_tenants(make_therapist, make_appointment, make_treatment_notes, login_as):
    """A and B signed in; B owns an appointment with saved notes; returns everything needed."""
    a = make_therapist(name="Dr A", email=A_EMAIL, password=PW)
    b = make_therapist(name="Dr B", email=B_EMAIL, password=PW)
    apt_b = make_appointment(therapist=b, apt_date="2026-03-02", apt_time="10:00")
    notes_b = make_treatment_notes(apt_b, session_notes="B's private notes")
    client_a = await login_as(a)
    client_b = await login_as(b)
    return {"a": a, "b": b, "apt_b": apt_b, "notes_b": notes_b, "ca": client_a, "cb": client_b}


def _triplet(apt: dict) -> str:
    return f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"


REFUSED = (403, 404)

ATTACKS = [
    ("GET", "", None),
    ("POST", "", {"session_notes": "A overwrote this"}),
    ("POST", "/complete", {"session_notes": "A completed this"}),
    (
        "POST",
        "/send-recommendations",
        {"items": [{"enabled": True, "text": "x"}], "schedule_hours": 0},
    ),
    ("POST", "/manual-feedback", {"rating": 5, "notes": "A"}),
    ("POST", "/rediagnose", {"tongue_observation": "red", "pulse_observation": "wiry"}),
    ("POST", "/generate-points", {}),
    ("POST", "/regenerate-points", {}),
]


@pytest.mark.parametrize(("method", "suffix", "body"), ATTACKS)
async def test_a_cannot_touch_b_treatment_notes(
    two_tenants, method: str, suffix: str, body
) -> None:
    t = two_tenants
    resp = await t["ca"].request(method, _triplet(t["apt_b"]) + suffix, json=body)
    assert resp.status_code in REFUSED, f"{method} {suffix}: {resp.status_code} {resp.text[:120]}"
    after = treatment_repo.get_by_appointment(t["apt_b"]["id"])
    assert after is not None
    assert after["session_notes"] == "B's private notes"
    assert after["completed_at"] is None
    assert after["tcm_pattern"] == t["notes_b"]["tcm_pattern"]


async def test_b_can_read_and_complete_its_own_appointment(two_tenants) -> None:
    t = two_tenants
    resp = await t["cb"].get(_triplet(t["apt_b"]))
    assert resp.status_code == 200
    assert resp.json()["session_notes"] == "B's private notes"
    resp = await t["cb"].post(_triplet(t["apt_b"]) + "/complete", json={"session_notes": "done"})
    assert resp.status_code == 200
    after = treatment_repo.get_by_appointment(t["apt_b"]["id"])
    assert after is not None and after["completed_at"]


async def test_debug_endpoint_cannot_be_enumerated_across_tenants(two_tenants) -> None:
    t = two_tenants
    resp = await t["ca"].get(f"/api/treatment-notes/{t['apt_b']['id']}/debug")
    assert resp.status_code in REFUSED
    resp = await t["cb"].get(f"/api/treatment-notes/{t['apt_b']['id']}/debug")
    assert resp.status_code == 200


async def test_treatment_page_is_scoped(two_tenants) -> None:
    t = two_tenants
    apt = t["apt_b"]
    url = f"/treatment/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    resp_a = await t["ca"].get(url)
    assert resp_a.status_code in (302, 303, 307, 403, 404)  # HTML route: bounced, never rendered
    assert "private notes" not in resp_a.text
    assert (await t["cb"].get(url)).status_code == 200


async def test_patient_lists_and_lookups_only_show_own_patients(two_tenants) -> None:
    t = two_tenants
    pid = t["apt_b"]["patient_id"]
    ca, cb = t["ca"], t["cb"]

    assert pid not in {p["id"] for p in (await ca.get("/api/patients")).json()}
    assert pid in {p["id"] for p in (await cb.get("/api/patients")).json()}

    search_a = (await ca.get("/api/patients/search", params={"q": ""})).json()["results"]
    assert pid not in {r["patient_id"] for r in search_a}

    assert (await ca.get(f"/api/patients/{pid}")).status_code == 404
    assert (await cb.get(f"/api/patients/{pid}")).status_code == 200

    apt = t["apt_b"]
    detail = f"/api/appointment/{pid}/{apt['date']}/{apt['time']}"
    assert (await ca.get(detail)).status_code == 404
    assert (await cb.get(detail)).status_code == 200

    # HTML patient profile: A is bounced back to the list, B sees the page
    assert (await ca.get(f"/patients/{pid}")).status_code in (302, 303, 307, 404)
    assert (await cb.get(f"/patients/{pid}")).status_code == 200


async def test_dashboard_today_is_scoped(frozen_clock, two_tenants, make_appointment) -> None:
    # frozen_clock is requested FIRST: a session cookie signed at real "now" is rejected by
    # itsdangerous once the clock is frozen in the past (negative signature age).
    t = two_tenants
    make_appointment(therapist=t["b"], apt_date="2026-03-01", apt_time="09:00")  # today (frozen)
    today_a = (await t["ca"].get("/api/appointments/today")).json()
    today_b = (await t["cb"].get("/api/appointments/today")).json()
    assert today_a["today_count"] == 0 and today_a["session_count"] == 0
    assert today_b["today_count"] == 1


async def test_relay_conversations_are_scoped(two_tenants, fake_redis, fake_telegram) -> None:
    t = two_tenants
    pid = 777_000_001
    await fake_redis.async_.set(
        f"zenflow:relay:active:{pid}", json.dumps({"patient_id": pid, "therapist_id": t["b"]["id"]})
    )
    await fake_redis.async_.set(
        f"zenflow:relay:history:{pid}",
        json.dumps([{"role": "patient", "text": "private to B", "ts": 1.0}]),
    )
    ca, cb = t["ca"], t["cb"]
    assert pid not in {
        s["patient_id"] for s in (await ca.get("/api/messages/conversations")).json()
    }
    assert pid in {s["patient_id"] for s in (await cb.get("/api/messages/conversations")).json()}
    assert (await ca.get("/api/messages/active")).json()["count"] == 0
    assert (await cb.get("/api/messages/active")).json()["count"] == 1

    assert (await ca.get(f"/api/messages/history/{pid}")).status_code in REFUSED
    assert (await ca.post(f"/api/messages/unread/{pid}")).status_code in REFUSED
    assert (await ca.delete(f"/api/messages/history/{pid}")).status_code in REFUSED
    sent = await ca.post("/api/messages/send", json={"patient_id": pid, "text": "hi from A"})
    assert sent.status_code in REFUSED
    assert fake_telegram.calls == [], "no Telegram message may leave on a refused request"
    assert await fake_redis.async_.get(f"zenflow:relay:history:{pid}") is not None

    assert (await cb.get(f"/api/messages/history/{pid}")).status_code == 200


async def test_availability_slots_are_scoped(two_tenants) -> None:
    t = two_tenants
    slot_id = availability_repo.insert(t["b"]["id"], "2026-03-05T09:00:00", "2026-03-05T10:00:00")
    resp = await t["ca"].delete(f"/api/availability/{slot_id}")
    assert resp.status_code in REFUSED
    assert any(s["id"] == slot_id for s in availability_repo.list_for_therapist(t["b"]["id"]))
    resp = await t["cb"].delete(f"/api/availability/{slot_id}")
    assert resp.status_code == 200
    assert not any(s["id"] == slot_id for s in availability_repo.list_for_therapist(t["b"]["id"]))


async def test_second_login_fixture_isolates_cookies(two_tenants) -> None:
    t = two_tenants
    assert t["ca"].cookies.get("zf_session") != t["cb"].cookies.get("zf_session")
