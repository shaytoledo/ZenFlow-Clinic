"""SF-016 — every page works under an ENFORCING Content-Security-Policy, in a real browser.

The policy (web/csp.py) shipped report-only because the pages carried inline `on*=` handlers that an
enforcing `script-src` blocks. With those gone (static/js/actions.js), this drives Chrome through
every page with `ZF_CSP_ENFORCE=1` and fails on:

- any `securitypolicyviolation` the browser raises (an inline handler, a script without the nonce,
  an origin the policy does not name);
- any element in the rendered DOM — including markup the pages build from API data after load —
  that still carries an `on*` attribute;

and it clicks a few converted controls to prove the delegated handlers really run under the policy.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

PW = "pw-Test-123"
RECORD_VIOLATIONS = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__csp.push(`${e.violatedDirective} ${e.blockedURI} ${e.sample || ''}`.trim());
});
"""
INLINE_HANDLERS = """
() => Array.from(document.querySelectorAll('*')).flatMap((el) =>
  Array.from(el.attributes).filter((a) => /^on/i.test(a.name)).map((a) => `${el.tagName}.${a.name}`))
"""


@pytest.fixture
def enforcing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import zenflow.settings as settings_mod

    monkeypatch.setenv("ZF_CSP_ENFORCE", "1")
    settings_mod.reset_settings()
    yield
    settings_mod.reset_settings()


def _context(browser: Any, base: str) -> Any:
    context = browser.new_context(viewport={"width": 1280, "height": 800}, reduced_motion="reduce")
    # Stay offline: external fonts/CDN are refused here, which is not a CSP violation.
    context.route(
        "**/*",
        lambda route: route.continue_() if route.request.url.startswith(base) else route.abort(),
    )
    context.add_init_script(RECORD_VIOLATIONS)
    return context


def _sign_in(context: Any, base: str, email: str) -> None:
    context.request.get(f"{base}/healthz")
    csrf = next(c["value"] for c in context.cookies() if c["name"] == "zf_csrf")
    resp = context.request.post(
        f"{base}/register/signin",
        form={"email": email, "password": PW, "csrf_token": csrf},
        max_redirects=0,
    )
    assert resp.status in (302, 303, 307), resp.status


def _visit(context: Any, url: str) -> tuple[Any, list[str]]:
    page = context.new_page()
    page.on("dialog", lambda d: d.dismiss())
    page.goto(url)
    page.wait_for_load_state("networkidle")
    problems = [f"CSP: {v}" for v in page.evaluate("window.__csp")]
    problems += [f"inline handler: {h}" for h in page.evaluate(INLINE_HANDLERS)]
    return page, problems


@pytest.fixture
def clinic(make_therapist, make_patient, make_appointment, make_treatment_notes) -> dict[str, Any]:
    from web.services import notification_service, treatment_service

    therapist = make_therapist(name="Dr Csp", email="csp@example.com", password=PW, active=True)
    patient = make_patient("Noa <b>Bold</b>")  # markup in a name must render as text
    today = date.today().isoformat()  # noqa: DTZ011 - aligns with the browser's "now"
    apt = make_appointment(therapist=therapist, patient=patient, apt_date=today, apt_time="09:00")
    make_treatment_notes(apt, session_notes="needled LR3")
    treatment_service.complete_session(apt["id"], patient["patient_id"])
    # a persistent alert: the bell renders a row and a "resolve" button from API data
    notification_service.alert_send_failed(
        therapist["id"], apt["id"], patient["patient_id"], patient["name"], "telegram"
    )
    return {"therapist": therapist, "patient": patient, "apt": apt}


def test_every_signed_in_page_runs_under_an_enforcing_csp(
    browser, live_server, enforcing, clinic
) -> None:
    base, apt, pid = live_server, clinic["apt"], clinic["patient"]["patient_id"]
    pages = [
        "/",
        "/schedule",
        "/patients",
        f"/patients/{pid}",
        f"/patients/{pid}/session/{apt['id']}",
        "/sessions",
        "/messages",
        "/settings",
        f"/treatment/{pid}/{apt['date']}/{apt['time'].replace(':', '-')}",
    ]
    context = _context(browser, base)
    try:
        _sign_in(context, base, clinic["therapist"]["email"])
        problems = {}
        for path in pages:
            page, found = _visit(context, base + path)
            assert "/register" not in page.url, f"{path} signed us out"
            if found:
                problems[path] = found
            page.close()
        assert problems == {}, json.dumps(problems, indent=1)

        # The delegated handlers run under the policy: the bell opens its panel.
        page, _ = _visit(context, base + "/")
        page.click("#notif-bell")
        page.wait_for_selector("#notif-panel", state="visible")
        assert page.evaluate("window.__csp") == []
    finally:
        context.close()


def test_the_registration_pages_run_under_an_enforcing_csp(
    browser, live_server, enforcing, make_therapist
) -> None:
    base = live_server
    context = _context(browser, base)
    try:
        page, problems = _visit(context, base + "/register")
        assert problems == []
        page.click("button.tab-btn[data-tab='signin']")
        assert page.is_visible("#panel-signin"), "the tab switch ran under the policy"
        assert page.evaluate("window.__csp") == []

        _, problems = _visit(context, base + "/register/done?code=ABCD1234")
        assert problems == []

        make_therapist(name="Dr New", email="new@example.com", password=PW, active=False)
        _sign_in(context, base, "new@example.com")
        for path in ("/register/activate", "/onboarding"):
            _, problems = _visit(context, base + path)
            assert problems == [], path
    finally:
        context.close()
