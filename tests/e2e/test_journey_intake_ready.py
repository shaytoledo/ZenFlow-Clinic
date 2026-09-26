"""Phase 11.4 — E2E journey J3: the AI diagnosis + points are ready before the therapist opens.

After a patient finishes intake, the summary→diagnosis→points pipeline runs in the background, so by
the time the therapist opens the treatment page the TCM diagnosis and the point formula are already
there — the page never generates on load. This drives Chrome to a treatment page whose notes were
produced ahead of time and asserts the diagnosis pattern and an AI-suggested point render.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import CANNED_DIAGNOSIS, CANNED_POINTS

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PW = "pw-Test-123"


def test_j3_diagnosis_and_points_render_when_the_therapist_opens_the_session(
    browser, live_server, make_therapist, make_appointment, make_patient, make_treatment_notes
) -> None:
    therapist = make_therapist(name="Dr Journey", email="j3@example.com", password=PW, active=True)
    apt = make_appointment(
        therapist=therapist,
        patient=make_patient("Dana Levi"),
        apt_date="2026-09-20",
        apt_time="09:00",
        summary="Headaches and disturbed sleep",
    )
    # The generation pipeline has already finished for this session.
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
        # Sign in via the API context (the browser sign-in path is covered by J1).
        context.request.get(f"{base}/healthz")
        csrf = next(c["value"] for c in context.cookies() if c["name"] == "zf_csrf")
        signin = context.request.post(
            f"{base}/register/signin",
            form={"email": therapist["email"], "password": PW, "csrf_token": csrf},
            max_redirects=0,
        )
        assert signin.status in (302, 303, 307), signin.status

        page = context.new_page()
        page.goto(f"{base}/treatment/{apt['patient_id']}/2026-09-20/09-00")

        # The diagnosis and at least one AI-suggested point are already on the page.
        page.wait_for_selector(f"text={CANNED_DIAGNOSIS['tcm_pattern']}", timeout=15000)
        page.wait_for_selector(f"text={CANNED_POINTS[0]['code']}", timeout=15000)
    finally:
        context.close()
