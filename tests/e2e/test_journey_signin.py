"""Phase 11.4 — E2E journey J1 (sign-in): a therapist signs in through the browser → dashboard.

The existing e2e tests sign in with Playwright's APIRequestContext, which bypasses the browser DOM
and `csrf.js`. This drives the *real* sign-in form in Chrome — switch to the Sign-In tab, type the
credentials, submit (the double-submit CSRF token is filled by `csrf.js`, exactly as for a person),
and land on the dashboard. The activation-via-bot half of J1 is a Telegram step covered by the
integration tests; here the therapist is already active.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PW = "pw-Test-123"


def test_j1_therapist_signs_in_and_sees_the_dashboard(browser, live_server, make_therapist) -> None:
    therapist = make_therapist(
        name="Dr Journey", email="journey@example.com", password=PW, active=True
    )
    context: Any = browser.new_context(
        viewport={"width": 1280, "height": 800}, reduced_motion="reduce"
    )
    # Keep the browser on our local server only.
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(live_server) else route.abort()
        ),
    )
    try:
        page = context.new_page()
        page.goto(live_server + "/register")

        page.click("button.tab-btn:has-text('Sign In')")  # reveal the sign-in panel
        page.fill("#panel-signin input[name='email']", therapist["email"])
        page.fill("#panel-signin input[name='password']", PW)
        page.click("#panel-signin button[type='submit']")

        # Landing on the dashboard proves the whole browser sign-in path worked (form + csrf.js +
        # session cookie + the redirect to "/").
        page.wait_for_selector("#stats-row", timeout=15000)
        assert page.query_selector("#stat-today") is not None, "the dashboard's stat cards rendered"
        assert page.url.rstrip("/") == live_server.rstrip("/"), "redirected to the dashboard root"
    finally:
        context.close()
