"""Phase 11.4 — E2E journey J5: a completed 24h follow-up shows in the session view.

After the 24h check-in conversation completes, its results (pain, change, side effects, summary) must
render on the read-only session archive the therapist reviews. This seeds a completed follow-up with a
red flag and drives Chrome to the session-archive page, asserting the follow-up card and its summary
render. (`test_followup_card` covers the card on the live treatment page; this is the archive view.)
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PW = "pw-Test-123"

CONVERSATION = [
    {"role": "ai", "content": "How much pain do you have right now, on a scale of *0–10*?"},
    {"role": "user", "content": "9"},
    {"role": "ai", "content": "Overall, how has your condition *changed* since the treatment?"},
    {"role": "user", "content": "1 — Much worse"},
]


def _complete_followup(apt_id: int) -> None:
    from web.repositories import followup_repo

    followup_repo.schedule(apt_id, "2026-09-21T09:00:00Z")
    followup_repo.mark_sent(apt_id, CONVERSATION[:1])
    followup_repo.save_progress(apt_id, step=3, conversation=CONVERSATION, answers={})
    followup_repo.complete(
        apt_id,
        conversation=CONVERSATION,
        answers={
            "pain_level": 9,
            "improvement_rating": 1,
            "side_effects": ["soreness", "fainting"],
            "sleep_quality": "worse",
            "adherence": "partly",
            "free_text": "Felt faint on the bus",
        },
        needs_attention=True,
        ai_summary="Pain 9/10 · Much worse (1/5) · sleep Worse\nside effects: Soreness, Fainting",
    )


def test_j5_completed_followup_renders_in_the_session_view(
    browser, live_server, make_therapist, make_appointment, make_patient, make_treatment_notes
) -> None:
    therapist = make_therapist(name="Dr Journey", email="j5@example.com", password=PW, active=True)
    apt = make_appointment(
        therapist=therapist,
        patient=make_patient("Dana Levi"),
        apt_date="2026-09-20",
        apt_time="09:00",
    )
    make_treatment_notes(apt, completed_at="2026-09-20T10:00:00Z")
    _complete_followup(apt["id"])

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
        page.goto(f"{base}/patients/{apt['patient_id']}/session/{apt['id']}")

        # The follow-up card renders in the archive with its summary.
        page.wait_for_selector("#followup-card", timeout=15000)
        page.wait_for_selector("text=Much worse", timeout=15000)
    finally:
        context.close()
