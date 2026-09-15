"""Phase 0.3 — web smoke tests.

Ten questions every later phase relies on: does the app boot, do pages answer, do API routes
refuse anonymous callers, does the signed-in path work, and do the fakes actually fake.
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

pytestmark = pytest.mark.integration

PAGE_ROUTES = [
    "/",
    "/schedule",
    "/patients",
    "/messages",
    "/sessions",
    "/settings",
    "/onboarding",
    "/register",
    "/register/done",
    "/register/activate",
    "/logout",
    "/treatment/1/2026-01-01/10-00",
    "/patients/1",
]

# Routes reachable WITHOUT a session as of Phase 0.3 (docs/SECURITY_FINDINGS.md SF-005 / F11).
# Phase 0.5 must close every one of them; each is a *strict* xfail so the test flips to a
# failure the moment a route is fixed and someone forgets to delete it here.
KNOWN_OPEN_API_ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/status"): "F11",
    ("GET", "/api/smtp-status"): "F11",
    ("GET", "/api/appointments/today"): "SF-005",
    ("POST", "/api/appointments"): "SF-005",
    ("GET", "/api/patients"): "SF-005",
    ("GET", "/api/patients/search"): "SF-005",
    ("GET", "/api/patients/{patient_id}"): "SF-005",
    ("GET", "/api/appointment/{patient_id}/{apt_date}/{apt_time}"): "SF-005",
    (
        "POST",
        "/api/treatment-notes/{patient_id}/{apt_date}/{apt_time}/send-recommendations",
    ): "SF-005",
    ("POST", "/api/calendars/refresh"): "SF-005",
    ("GET", "/api/events"): "SF-005",
    ("POST", "/api/availability"): "SF-005",
    ("DELETE", "/api/availability/{event_id}"): "SF-005",
    ("GET", "/api/messages/active"): "SF-005",
    ("POST", "/api/messages/send"): "SF-005",
}


async def test_app_boots_and_root_redirects_anonymous_to_register(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/")
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"].startswith("/register")


@pytest.mark.parametrize("path", PAGE_ROUTES)
async def test_every_page_route_returns_200_or_redirect(
    client: httpx.AsyncClient, path: str
) -> None:
    resp = await client.get(path)
    assert resp.status_code in (200, 302, 303, 307), f"{path} -> {resp.status_code}"


def _api_routes():
    from web.app import app

    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api/"):
            continue
        for method in sorted(methods - {"HEAD", "OPTIONS"}):
            finding = KNOWN_OPEN_API_ROUTES.get((method, path))
            if finding:
                yield pytest.param(
                    method,
                    path,
                    marks=pytest.mark.xfail(
                        strict=True, reason=f"open until Phase 0.5 ({finding})"
                    ),
                )
            else:
                yield method, path


def _fill(path: str) -> str:
    return (
        path.replace("{patient_id}", "1")
        .replace("{apt_date}", "2026-01-01")
        .replace("{apt_time}", "10-00")
        .replace("{appointment_id}", "1")
        .replace("{notification_id}", "1")
        .replace("{event_id}", "evt1")
    )


@pytest.mark.parametrize(("method", "path"), list(_api_routes()))
async def test_every_api_route_rejects_anonymous_access(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    resp = await client.request(method, _fill(path), json={})
    assert resp.status_code in (401, 403, 302, 303, 307), f"{method} {path} -> {resp.status_code}"


async def test_authenticated_client_reaches_dashboard_and_settings(
    authenticated_client: httpx.AsyncClient,
) -> None:
    for path in ("/", "/settings", "/sessions"):
        resp = await authenticated_client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"


async def test_signin_with_wrong_password_does_not_create_a_session(
    client: httpx.AsyncClient, make_therapist
) -> None:
    make_therapist(email="doc@example.com", password="correct-horse")
    resp = await client.post(
        "/register/signin", data={"email": "doc@example.com", "password": "wrong"}
    )
    assert resp.status_code == 200  # re-rendered form
    assert "zf_session" not in resp.cookies
    home = await client.get("/")
    assert home.status_code in (302, 303, 307)


async def test_fake_redis_is_shared_between_sync_and_async_clients(fake_redis) -> None:
    from bot.redis_client import get_async_redis, get_sync_redis

    get_sync_redis().set("zenflow:test:k", "v")
    assert await get_async_redis().get("zenflow:test:k") == "v"


def test_frozen_clock_pins_now(frozen_clock) -> None:
    assert datetime.now().isoformat().startswith("2026-03-01T12:00:00")
    frozen_clock.tick(60)
    assert datetime.now().isoformat().startswith("2026-03-01T12:01:00")


async def test_fake_telegram_records_outbound_messages(fake_telegram) -> None:
    from web.services.telegram_service import send_to_patient

    result = await send_to_patient(4242, "hello *patient*")
    assert result["ok"] is True
    assert fake_telegram.calls == [
        {"bot": "patient", "chat_id": 4242, "text": "hello *patient*", "parse_mode": "Markdown"}
    ]


async def test_fake_llm_yields_a_parsed_diagnosis(fake_llm, fake_redis) -> None:
    from bot.patient_bot.services import ai_intake

    diag = await ai_intake.generate_tcm_diagnosis(user_id=1, clinical_summary="headache x 3 days")
    assert diag["tcm_pattern"] == "Liver Yang Rising"
    assert diag["diagnosis_certainty"] == 72
    assert fake_llm.calls, "the fake LLM was not invoked"


def test_factories_build_a_completed_session(make_completed_session) -> None:
    from web.repositories import treatment_repo

    apt = make_completed_session()
    notes = treatment_repo.get_by_appointment(apt["id"])
    assert notes is not None
    assert notes["completed_at"]
    assert notes["tcm_pattern"]
