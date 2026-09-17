"""Plan 7.3 — `POST /api/v1/appointments` and friends: the one path that creates an appointment.

Machine clients (the WhatsApp bridge of 7.4, a clinic website) authenticate with an API key; the
dashboard keeps using its session cookie. Booking is idempotent, availability-aware, and safe
under a race for the same hour.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest

import bot.db as dbmod
from web.repositories import api_client_repo, patient_repo

pytestmark = pytest.mark.integration

TG = 920_000_101
DAY = "2026-03-12"
HOURS = ["09:00", "10:00", "11:00"]


def _q(sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute(sql, args)]


def _jobs() -> list[dict[str, Any]]:
    return _q("SELECT * FROM jobs ORDER BY id")


@pytest.fixture(autouse=True)
def availability(monkeypatch):
    """Three free hours on DAY, and a calendar that accepts the booking."""
    from web.services import booking_service

    booked: list[tuple[Any, ...]] = []

    async def _hours(day: date, therapist_id: str | None = None) -> list[str]:
        return list(HOURS) if day.isoformat() == DAY else []

    async def _book(*a: Any, **k: Any) -> str:
        booked.append((a, k))
        return "gcal-evt-1"

    async def _restore(*a: Any, **k: Any) -> None:
        booked.append(("restored", a, k))

    monkeypatch.setattr(booking_service, "get_available_hours", _hours)
    monkeypatch.setattr(booking_service, "book_slot", _book)
    monkeypatch.setattr(booking_service, "restore_slot", _restore)
    return booked


@pytest.fixture
def therapist(db, make_therapist):
    from bot import config as botcfg

    t = make_therapist(
        name="Dr Api", therapist_id="t1", email="api@example.com", password="pw-Test-123"
    )
    botcfg.reload_therapists()
    return t


@pytest.fixture
def api_key(therapist) -> str:
    _client_id, key = api_client_repo.create("whatsapp-bridge")
    return key


@pytest.fixture
async def api(api_key):
    """A client of its own (never the dashboard's) carrying a machine client's key."""
    import httpx

    from web.app import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        follow_redirects=False,
        headers={"Authorization": f"Bearer {api_key}"},
    ) as machine:
        yield machine


def _body(**over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "therapist_id": "t1",
        "start_at": f"{DAY}T08:00:00Z",  # 10:00 clinic time (UTC+2)
        "duration_min": 60,
        "patient": {"channel": "telegram", "external_id": str(TG), "name": "Dana Levi"},
        "summary": "neck pain",
    }
    body.update(over)
    return body


# ── authentication ──
async def test_a_key_is_required(client) -> None:
    resp = await client.post("/api/v1/appointments", json=_body())
    assert resp.status_code == 401
    assert resp.json()["code"] == "unauthenticated"
    assert _q("SELECT id FROM appointments") == []


@pytest.mark.parametrize("header", ["Bearer nope", "Bearer ", "Basic zf_live_x", "zf_live_x"])
async def test_a_bad_key_is_refused(client, api_key: str, header: str) -> None:
    client.headers["Authorization"] = header
    assert (await client.post("/api/v1/appointments", json=_body())).status_code == 401


async def test_a_revoked_key_stops_working(api, api_key: str) -> None:
    assert (await api.post("/api/v1/appointments", json=_body())).status_code == 201
    api_client_repo.revoke("whatsapp-bridge")
    resp = await api.post("/api/v1/appointments", json=_body(start_at=f"{DAY}T07:00:00Z"))
    assert resp.status_code == 401


async def test_the_key_is_stored_hashed(api_key: str) -> None:
    rows = _q("SELECT * FROM api_clients")
    assert len(rows) == 1
    assert api_key.startswith("zf_") and len(api_key) > 32
    assert api_key not in str(rows[0])
    assert rows[0]["key_hash"] != api_key


async def test_a_therapist_session_may_book_for_itself_only(
    authenticated_client, make_therapist
) -> None:
    from bot import config as botcfg

    mine = authenticated_client.headers["X-Test-Therapist-Id"]
    make_therapist(therapist_id="t9", name="Other")
    botcfg.reload_therapists()

    ok = await authenticated_client.post("/api/v1/appointments", json=_body(therapist_id=mine))
    assert ok.status_code == 201, ok.text

    forbidden = await authenticated_client.post(
        "/api/v1/appointments", json=_body(therapist_id="t9", start_at=f"{DAY}T07:00:00Z")
    )
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "forbidden"
    assert len(_q("SELECT id FROM appointments")) == 1


# ── creating ──
async def test_a_booking_creates_the_patient_and_the_appointment(api, availability) -> None:
    resp = await api.post("/api/v1/appointments", json=_body())

    assert resp.status_code == 201, resp.text
    appointment = resp.json()
    assert resp.headers["location"] == f"/api/v1/appointments/{appointment['id']}"
    pid = patient_repo.find_by_channel("telegram", TG)
    assert appointment == {
        "id": appointment["id"],
        "therapist_id": "t1",
        "patient_id": pid,
        "patient_name": "Dana Levi",
        "start_at": f"{DAY}T08:00:00Z",
        "local_date": DAY,
        "local_time": "10:00",
        "duration_min": 60,
        "status": "active",
        "source": "api",
        "summary": "neck pain",
        "created_at": appointment["created_at"],
    }
    row = _q("SELECT * FROM appointments")[0]
    assert (row["date"], row["time"], row["patient_id"]) == (DAY, "10:00", pid)
    assert row["gcal_apt_event_id"] == "gcal-evt-1", "the calendar event is linked"
    assert availability, "the hour was taken out of the availability calendar"


async def test_an_existing_patient_is_reused(api) -> None:
    pid = patient_repo.for_channel("telegram", TG, "Dana Levi")
    resp = await api.post("/api/v1/appointments", json=_body())
    assert resp.status_code == 201
    assert resp.json()["patient_id"] == pid
    assert len(_q("SELECT id FROM patients")) == 1


async def test_booking_by_patient_id(api) -> None:
    pid = patient_repo.create("Noa Manual", phone="052-1")
    resp = await api.post(
        "/api/v1/appointments", json=_body(patient={"patient_id": pid, "name": "Noa Manual"})
    )
    assert resp.status_code == 201 and resp.json()["patient_id"] == pid


async def test_an_unknown_patient_id_is_refused(api) -> None:
    resp = await api.post(
        "/api/v1/appointments", json=_body(patient={"patient_id": 4242, "name": "Ghost"})
    )
    assert resp.status_code == 404 and resp.json()["code"] == "unknown_patient"
    assert _q("SELECT id FROM appointments") == []


async def test_an_unknown_therapist_is_refused(api) -> None:
    resp = await api.post("/api/v1/appointments", json=_body(therapist_id="nope"))
    assert resp.status_code == 404 and resp.json()["code"] == "unknown_therapist"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"start_at": "2026-03-12 10:00"}, "not an instant"),
        ({"start_at": f"{DAY}T08:00:00"}, "no timezone"),
        ({"duration_min": 30}, "only whole hours"),
        ({"patient": {"name": ""}}, "a name is required"),
        ({"patient": {"channel": "sms", "external_id": "1", "name": "X"}}, "unknown channel"),
        ({"patient": {"channel": "telegram", "name": "X"}}, "channel without an id"),
        ({"therapist_id": ""}, "a therapist is required"),
    ],
)
async def test_bad_requests_are_refused(api, over: dict, reason: str) -> None:
    resp = await api.post("/api/v1/appointments", json=_body(**over))
    assert resp.status_code == 422, reason
    assert resp.json()["code"] == "validation_error"
    assert _q("SELECT id FROM appointments") == []


async def test_a_slot_outside_availability_is_refused(api) -> None:
    resp = await api.post("/api/v1/appointments", json=_body(start_at=f"{DAY}T12:00:00Z"))
    assert resp.status_code == 409 and resp.json()["code"] == "slot_unavailable"
    assert _q("SELECT id FROM appointments") == []


async def test_a_taken_hour_is_refused(api) -> None:
    assert (await api.post("/api/v1/appointments", json=_body())).status_code == 201
    resp = await api.post(
        "/api/v1/appointments",
        json=_body(patient={"channel": "telegram", "external_id": "920000999", "name": "Other"}),
    )
    assert resp.status_code == 409 and resp.json()["code"] == "slot_taken"
    assert len(_q("SELECT id FROM appointments")) == 1


async def test_two_requests_for_the_same_hour_race_safely(api) -> None:
    first, second = await asyncio.gather(
        api.post("/api/v1/appointments", json=_body()),
        api.post(
            "/api/v1/appointments",
            json=_body(patient={"channel": "telegram", "external_id": "920000998", "name": "B"}),
        ),
    )
    assert sorted([first.status_code, second.status_code]) == [201, 409]
    taken = first if first.status_code == 409 else second
    assert taken.json()["code"] == "slot_taken"
    assert len(_q("SELECT id FROM appointments WHERE status='active'")) == 1


# ── idempotency ──
async def test_the_same_key_returns_the_first_answer(api) -> None:
    body = _body(idempotency_key="abc-123")
    first = await api.post("/api/v1/appointments", json=body)
    second = await api.post("/api/v1/appointments", json=body)

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert second.headers.get("idempotent-replay") == "true"
    assert len(_q("SELECT id FROM appointments")) == 1, "no duplicate row"
    assert len([j for j in _jobs() if j["name"] == "booking.confirm"]) == 1


async def test_the_key_may_come_from_the_header(api) -> None:
    body = _body()
    first = await api.post("/api/v1/appointments", json=body, headers={"Idempotency-Key": "h-1"})
    second = await api.post("/api/v1/appointments", json=body, headers={"Idempotency-Key": "h-1"})
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


async def test_a_reused_key_with_a_different_body_is_refused(api) -> None:
    assert (
        await api.post("/api/v1/appointments", json=_body(idempotency_key="k9"))
    ).status_code == 201
    resp = await api.post(
        "/api/v1/appointments", json=_body(idempotency_key="k9", start_at=f"{DAY}T07:00:00Z")
    )
    assert resp.status_code == 422 and resp.json()["code"] == "idempotency_key_reused"
    assert len(_q("SELECT id FROM appointments")) == 1


async def test_a_refusal_is_replayed_too(api) -> None:
    body = _body(start_at=f"{DAY}T12:00:00Z", idempotency_key="k-bad")
    first = await api.post("/api/v1/appointments", json=body)
    second = await api.post("/api/v1/appointments", json=body)
    assert first.status_code == second.status_code == 409
    assert first.json() == second.json()


async def test_keys_are_scoped_to_the_client(api, client, therapist) -> None:
    assert (
        await api.post("/api/v1/appointments", json=_body(idempotency_key="same"))
    ).status_code == 201
    _id, other_key = api_client_repo.create("website")
    client.headers["Authorization"] = f"Bearer {other_key}"
    resp = await client.post(
        "/api/v1/appointments",
        json=_body(idempotency_key="same", start_at=f"{DAY}T07:00:00Z"),
    )
    assert resp.status_code == 201, "another client's key is a different key"


# ── the confirmation message ──
async def test_a_confirmation_is_queued_and_sent(api, fake_telegram) -> None:
    from zenflow import queue as q
    from zenflow import worker as w

    resp = await api.post("/api/v1/appointments", json=_body())
    assert resp.status_code == 201
    (job,) = (j for j in _jobs() if j["name"] == "booking.confirm")
    assert job["idempotency_key"] == f"booking-confirm:{resp.json()['id']}"

    import bot.services.booking_jobs  # noqa: F401

    await w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="t").run_once()

    (call,) = fake_telegram.calls
    assert call["chat_id"] == TG
    assert "12 March" in call["text"] or DAY in call["text"]
    assert "10:00" in call["text"]
    logged = _q("SELECT kind, channel, status, appointment_id FROM message_log")
    assert logged == [
        {
            "kind": "confirmation",
            "channel": "telegram",
            "status": "sent",
            "appointment_id": resp.json()["id"],
        }
    ]


async def test_a_patient_without_a_channel_gets_no_confirmation(api, fake_telegram) -> None:
    from zenflow import queue as q
    from zenflow import worker as w

    pid = patient_repo.create("Noa Manual")
    resp = await api.post(
        "/api/v1/appointments", json=_body(patient={"patient_id": pid, "name": "Noa Manual"})
    )
    assert resp.status_code == 201

    import bot.services.booking_jobs  # noqa: F401

    await w.Worker(q.SqliteTaskQueue(), w.default_registry, worker_id="t").run_once()
    assert fake_telegram.api_calls == []
    assert _q("SELECT id FROM message_log") == []


async def test_confirmation_can_be_turned_off(api) -> None:
    resp = await api.post("/api/v1/appointments", json=_body(send_confirmation=False))
    assert resp.status_code == 201
    assert [j for j in _jobs() if j["name"] == "booking.confirm"] == []


# ── reading ──
async def test_listing_is_scoped_and_filtered(api, authenticated_client, make_therapist) -> None:
    from bot import config as botcfg

    make_therapist(therapist_id="t9", name="Other")
    botcfg.reload_therapists()
    await api.post("/api/v1/appointments", json=_body())
    await api.post("/api/v1/appointments", json=_body(start_at=f"{DAY}T07:00:00Z"))

    listing = await api.get(f"/api/v1/appointments?therapist_id=t1&from={DAY}&to={DAY}")
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] == 2
    assert [item["local_time"] for item in body["items"]] == ["09:00", "10:00"]

    others = await api.get("/api/v1/appointments?therapist_id=t9")
    assert others.status_code == 200 and others.json()["count"] == 0

    # a therapist session sees only its own, whatever it asks for
    mine = await authenticated_client.get("/api/v1/appointments?therapist_id=t1")
    assert mine.status_code == 403


async def test_one_appointment_by_id(api) -> None:
    created = (await api.post("/api/v1/appointments", json=_body())).json()
    one = await api.get(f"/api/v1/appointments/{created['id']}")
    assert one.status_code == 200 and one.json() == created
    assert (await api.get("/api/v1/appointments/4242")).status_code == 404


async def test_availability_is_listed_as_instants(api) -> None:
    resp = await api.get(f"/api/v1/availability?therapist_id=t1&from={DAY}&to={DAY}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 3
    assert body["items"][0] == {
        "start_at": f"{DAY}T07:00:00Z",
        "local_date": DAY,
        "local_time": "09:00",
        "duration_min": 60,
    }


async def test_availability_refuses_a_silly_range(api) -> None:
    far = (date.fromisoformat(DAY) + timedelta(days=90)).isoformat()
    resp = await api.get(f"/api/v1/availability?therapist_id=t1&from={DAY}&to={far}")
    assert resp.status_code == 422 and resp.json()["code"] == "validation_error"


# ── cancelling ──
async def test_cancelling_frees_the_hour_and_keeps_the_row(api, availability) -> None:
    created = (await api.post("/api/v1/appointments", json=_body())).json()
    resp = await api.delete(f"/api/v1/appointments/{created['id']}")

    assert resp.status_code == 200 and resp.json()["status"] == "cancelled"
    assert _q("SELECT status FROM appointments") == [{"status": "cancelled"}], "kept, soft-deleted"
    assert any(entry[0] == "restored" for entry in availability if isinstance(entry, tuple))

    again = await api.delete(f"/api/v1/appointments/{created['id']}")
    assert again.status_code == 200 and again.json()["status"] == "cancelled", "idempotent"


async def test_cancelling_someone_elses_appointment(api, authenticated_client) -> None:
    created = (await api.post("/api/v1/appointments", json=_body())).json()
    resp = await authenticated_client.delete(f"/api/v1/appointments/{created['id']}")
    assert resp.status_code == 403
    assert _q("SELECT status FROM appointments") == [{"status": "active"}]


# ── rate limiting ──
async def test_too_many_requests(api, monkeypatch) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("ZF_API_RATE_PER_MINUTE", "2")
    S.reset_settings()
    first = await api.get("/api/v1/appointments?therapist_id=t1")
    second = await api.get("/api/v1/appointments?therapist_id=t1")
    third = await api.get("/api/v1/appointments?therapist_id=t1")
    assert [first.status_code, second.status_code] == [200, 200]
    assert third.status_code == 429
    assert third.json()["code"] == "rate_limited"
    assert int(third.headers["retry-after"]) >= 1
    S.reset_settings()
