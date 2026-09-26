"""Phase 11.4 — E2E journey J4: the therapist runs and completes a session.

The therapist opens the treatment page, records tongue + pulse, and completes the session. Completing
must persist the observations, mark the session done, and queue the 24h follow-up. This drives the
real page in Chrome (accepting the confirm dialog) and asserts the database reflects it.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PW = "pw-Test-123"


def test_j4_therapist_records_observations_and_completes_the_session(
    browser, live_server, make_therapist, make_appointment, make_patient, make_treatment_notes
) -> None:
    therapist = make_therapist(name="Dr Journey", email="j4@example.com", password=PW, active=True)
    apt = make_appointment(
        therapist=therapist,
        patient=make_patient("Dana Levi"),
        apt_date="2026-09-20",
        apt_time="09:00",
        summary="Headaches",
    )
    make_treatment_notes(apt, points_status="COMPLETED")

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
        page.on("dialog", lambda dialog: dialog.accept())  # accept the "mark complete?" confirm
        page.goto(f"{base}/treatment/{apt['patient_id']}/2026-09-20/09-00")
        page.wait_for_selector("#complete-btn", timeout=15000)

        page.fill("#tongue-input", "pale, thin white coat")
        page.fill("#pulse-input", "wiry, slightly rapid")
        page.click("#complete-btn")

        # The button flips to its success state once the POST returns.
        page.wait_for_selector("text=Session Complete", timeout=15000)

        from bot.db import get_db

        row = (
            get_db()
            .execute(
                "SELECT completed_at, tongue_observation, pulse_observation FROM treatment_notes "
                "WHERE appointment_id=?",
                (apt["id"],),
            )
            .fetchone()
        )
        assert row["completed_at"], "the session is marked complete"
        assert row["tongue_observation"] == "pale, thin white coat", "observations persisted"
        assert row["pulse_observation"] == "wiry, slightly rapid"

        jobs = (
            get_db()
            .execute("SELECT COUNT(*) AS n FROM jobs WHERE name='followup.send_step1'")
            .fetchone()["n"]
        )
        assert jobs >= 1, "the 24h follow-up was queued on completion"
    finally:
        context.close()
