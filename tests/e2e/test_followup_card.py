"""Phase 6.5 — the 24h follow-up card in the browser: each of its five states, and RTL.

The card is rendered on the server; these tests open the real treatment page and check what a
therapist sees, plus a snapshot of the completed card with a red flag in both languages.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.test_visual_snapshots import PW, STEADY_CSS, assert_matches_baseline

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

CONVERSATION = [
    {"role": "ai", "content": "How much pain do you have right now, on a scale of *0–10*?"},
    {"role": "user", "content": "9"},
    {"role": "ai", "content": "Overall, how has your condition *changed* since the treatment?"},
    {"role": "user", "content": "1 — Much worse"},
]


def _arrange(state: str, apt_id: int) -> None:
    from bot.db import get_db
    from web.repositories import followup_repo

    followup_repo.schedule(apt_id, "2026-09-21T09:00:00Z")
    if state == "scheduled":
        return
    followup_repo.mark_sent(apt_id, CONVERSATION[:1])
    if state == "awaiting":
        followup_repo.save_progress(
            apt_id,
            step=3,
            conversation=CONVERSATION,
            answers={"pain_level": 4, "improvement_rating": 3},
        )
    elif state == "completed":
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
        # a fixed time, so the snapshot does not change with the clock
        get_db().execute(
            "UPDATE followups SET completed_at='2026-09-21T09:30:00Z' WHERE appointment_id=?",
            (apt_id,),
        )
    elif state == "expired":
        get_db().execute("UPDATE followups SET status='expired' WHERE appointment_id=?", (apt_id,))


@pytest.fixture
def card_page(make_therapist, make_appointment, make_patient, make_treatment_notes):
    from web.repositories.treatment_repo import set_points_status

    def _open(browser: Any, base: str, state: str, lang: str = "en") -> Any:
        therapist = make_therapist(
            name="Dr Preview", email=f"card-{state}-{lang}@example.com", password=PW, language=lang
        )
        patient = make_patient("Noa Katz", manual=state == "no_channel")
        apt = make_appointment(
            therapist=therapist, patient=patient, apt_date="2026-09-20", apt_time="09:00"
        )
        make_treatment_notes(apt)
        set_points_status(apt["id"], "COMPLETED")
        _arrange(state, apt["id"])
        context = browser.new_context(
            viewport={"width": 1280, "height": 900}, reduced_motion="reduce"
        )
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base) else route.abort()
            ),
        )
        # Playwright's APIRequestContext bypasses the browser DOM (and csrf.js), so it must
        # fetch the CSRF cookie and submit the token as a field itself (9.3).
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
        page.wait_for_selector("#followup-card")
        page.add_style_tag(content=STEADY_CSS)
        return page

    return _open


EXPECTED = {
    "scheduled": ("Scheduled", "The check-in goes out 2026-09-21 12:00."),
    "awaiting": ("Awaiting reply", "2 of 6 questions answered so far."),
    "completed": ("Completed", "Answered 2026-09-21 12:30."),
    "expired": ("No reply", "the patient did not finish within 48 hours."),
    "no_channel": ("Phone follow-up", "This patient cannot be messaged"),
}


@pytest.mark.parametrize("state", list(EXPECTED))
def test_each_state_reads_clearly(browser, live_server, card_page, state: str) -> None:
    page = card_page(browser, live_server, state)
    try:
        card = page.locator("#followup-card")
        assert card.is_visible()
        badge, detail = EXPECTED[state]
        assert card.locator(".fu-badge").inner_text() == badge
        assert detail in card.locator(".fu-detail").inner_text()
        assert card.locator(".fu-alert").count() == (1 if state == "completed" else 0)
        if state == "awaiting":
            assert card.locator("meter").evaluate("m => m.value") == 4
        if state == "completed":
            assert "pain 9/10" in card.locator(".fu-alert").inner_text()
            assert card.locator(".fu-chip").count() == 4
            transcript = card.locator("details.fu-transcript")
            assert not transcript.evaluate("d => d.open"), "the transcript starts collapsed"
            transcript.locator("summary").click()
            assert card.locator(".fu-msg").count() == 4
            assert card.locator(".fu-msg-patient").first.inner_text().endswith("9")
        if state == "no_channel":
            card.locator(".fu-link").click()
            page.wait_for_function("() => location.hash === '#manual-feedback-card'")
            assert page.locator("#manual-feedback-card").is_visible()
        if state == "scheduled":
            assert card.locator(".fu-chips, meter, details").count() == 0
    finally:
        page.context.close()


@pytest.mark.parametrize("lang", ["en", "he"])
def test_the_completed_card_with_a_red_flag(browser, live_server, card_page, lang: str) -> None:
    page = card_page(browser, live_server, "completed", lang)
    try:
        card = page.locator("#followup-card")
        card.scroll_into_view_if_needed()
        direction = page.evaluate(
            "getComputedStyle(document.getElementById('followup-card')).direction"
        )
        assert direction == ("rtl" if lang == "he" else "ltr")
        assert_matches_baseline(f"followup-card-{lang}", card.screenshot())
    finally:
        page.context.close()
