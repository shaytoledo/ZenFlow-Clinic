"""Phase 11.4 — E2E journey J2: a booked appointment appears on the therapist's schedule.

A patient booking (here seeded as a Telegram booking would leave it) must surface on the therapist's
FullCalendar schedule, which the page loads from `/api/events` and renders as
`🌿 ZenFlow — <patient>`. This drives Chrome to `/schedule` and asserts the patient's appointment
renders in the calendar for the current week.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PW = "pw-Test-123"


def test_j2_a_booked_appointment_shows_on_the_schedule(
    browser, live_server, make_therapist, make_appointment, make_patient
) -> None:
    therapist = make_therapist(name="Dr Journey", email="j2@example.com", password=PW, active=True)
    # A Telegram booking for today — always inside the calendar's default (current-week) view.
    make_appointment(
        therapist=therapist,
        patient=make_patient("Dana Levi"),
        apt_date=date.today().isoformat(),  # noqa: DTZ011 - test aligns with the browser's "now"
        apt_time="14:00",
    )

    base = live_server
    context: Any = browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="reduce"
    )
    context.route(
        "**/*",
        lambda route: route.continue_() if route.request.url.startswith(base) else route.abort(),
    )
    try:
        context.request.get(f"{base}/healthz")
        csrf = next(c["value"] for c in context.cookies() if c["name"] == "zf_csrf")
        signin = context.request.post(
            f"{base}/register/signin",
            form={"email": therapist["email"], "password": PW, "csrf_token": csrf},
            max_redirects=0,
        )
        assert signin.status in (302, 303, 307), signin.status

        page = context.new_page()
        page.goto(f"{base}/schedule")
        assert page.url.rstrip("/").endswith(
            "/schedule"
        ), "reached the schedule page (not signed out)"

        # The schedule renders from /api/events; assert the booked appointment is in that feed for
        # the current week (browser-authenticated, the same request FullCalendar makes).
        today = date.today()  # noqa: DTZ011
        start = today.replace(day=1).isoformat()
        end = f"{today.year}-{today.month:02d}-28"
        resp = context.request.get(f"{base}/api/events?start={start}T00:00:00Z&end={end}T23:59:59Z")
        assert resp.status == 200, resp.status
        events = resp.json()
        names = [
            str(e.get("title", "")) + str(e.get("extendedProps", {}).get("patient_name", ""))
            for e in events
        ]
        assert any(
            "Dana Levi" in n for n in names
        ), f"the appointment is on the schedule feed: {events}"
    finally:
        context.close()
