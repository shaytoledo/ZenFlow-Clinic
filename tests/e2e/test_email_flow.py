"""Phase 5.3 — emailing an email-only patient from the treatment page, in the browser.

Google itself is faked at its edges (the consent URL, the code exchange, the Gmail client); the
page, the API, `/auth/login?next=` and the OAuth callback are the real ones.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e.test_visual_snapshots import PW, STEADY_CSS, assert_matches_baseline

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

EMAIL = "dana@example.com"
#: aria-disabled controls stay clickable for people ("try anyway" explains the problem), but
#: Playwright treats them as disabled — so those clicks skip its actionability wait
TRY_ANYWAY = {"force": True}


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def users(self) -> FakeGmail:
        return self

    def messages(self) -> FakeGmail:
        return self

    def send(self, userId: str, body: dict[str, Any]) -> FakeGmail:  # noqa: N803 - Google's name
        self.sent.append(body)
        return self

    def execute(self) -> dict[str, str]:
        return {"id": "m1"}


def _connect(therapist_id: str) -> None:
    from bot.db import get_db

    get_db().execute(
        """INSERT OR REPLACE INTO google_tokens (therapist_id, encrypted_token, scopes, updated_at)
           VALUES (?, 'x', '', '2026-01-01T00:00:00Z')""",
        (therapist_id,),
    )


@pytest.fixture
def email_page(make_therapist, make_appointment, make_patient, make_treatment_notes, monkeypatch):
    """A signed-in treatment page of a manual (email-only) patient; Google is faked."""
    import web.routers.auth as auth

    async def _no_prefetch(_tid: str) -> None:
        return None

    monkeypatch.setattr(auth, "GOOGLE_CLIENT_ID", "client-id")
    monkeypatch.setattr(auth, "prefetch_calendar", _no_prefetch)

    def _open(browser: Any, base: str, lang: str = "en") -> tuple[Any, dict[str, Any]]:
        therapist = make_therapist(
            name="Dr Preview", email=f"mail-{lang}@example.com", password=PW, language=lang
        )
        apt = make_appointment(
            therapist=therapist,
            patient=make_patient("Dana Levi", manual=True),
            apt_date="2026-09-20",
            apt_time="09:00",
            summary="Headache",
        )
        make_treatment_notes(apt)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800}, reduced_motion="reduce"
        )
        context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
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
        path = f"/treatment/{apt['patient_id']}/2026-09-20/09-00"
        page.goto(base + path)
        page.wait_for_selector("#advice-list .zf-advice-item >> nth=3")
        page.add_style_tag(content=STEADY_CSS)
        return page, {"therapist": therapist, "apt": apt, "path": path}

    return _open


def _jobs() -> int:
    from bot.db import get_db

    return int(get_db().execute("SELECT COUNT(*) FROM jobs").fetchone()[0])


@pytest.mark.parametrize("lang", ["en", "he"])
def test_email_controls_explain_a_missing_google_account(
    browser, live_server, email_page, lang: str
) -> None:
    from web.i18n import get_t

    t = get_t(lang)
    page, s = email_page(browser, live_server, lang)
    try:
        page.wait_for_selector("#google-hint:not([hidden])")
        assert t["email_connect_google_btn"] in page.inner_text("#google-hint")
        for button in ("#send-advice-btn", "#send-later-btn"):
            assert page.get_attribute(button, "aria-disabled") == "true"
            assert page.get_attribute(button, "aria-describedby") == "google-hint"
            assert page.get_attribute(button, "title") == t["email_not_connected_body"]

        # "Send now" for a patient without Telegram asks for an address — and says why it won't go
        page.click("#send-advice-btn", **TRY_ANYWAY)
        page.wait_for_selector("#email-dialog[open] #email-address")
        assert page.evaluate("document.activeElement.id") == "email-address"
        assert page.get_attribute("#email-send", "aria-disabled") == "true"
        assert t["email_not_connected_title"] in page.inner_text("#email-dialog .ed-notice")
        assert_matches_baseline(f"email-blocked-desktop-{lang}", page.screenshot())

        # copy the text instead
        page.click('#email-dialog [data-action="show-email-copy"]')
        page.wait_for_selector("#email-copy-text")
        text = page.input_value("#email-copy-text")
        assert "Hi Dana," in text and "Lights out by 23:00" in text
        page.click('#email-dialog [data-action="copy-email-text"]')
        page.wait_for_function(
            "label => document.querySelector('[data-action=\"copy-email-text\"]')"
            ".textContent === label",
            arg=t["email_dialog_copied"],
        )
        clipboard = page.evaluate("navigator.clipboard.readText()")
        assert clipboard.replace("\r\n", "\n") == text, "Windows adds CR to the clipboard"
        page.keyboard.press("Escape")
        assert not page.evaluate("document.getElementById('email-dialog').open")
        # the close event (queued after close()) empties it
        page.wait_for_function("() => document.getElementById('email-dialog').innerHTML === ''")

        # pressing the marked-disabled "Send in 24h" explains instead of queueing a doomed send
        page.click("#send-later-btn", **TRY_ANYWAY)
        link = page.wait_for_selector('#email-dialog[open] [data-action="connect-google"]')
        assert link.get_attribute("href") == "/auth/login?next=" + s["path"].replace("/", "%2F")
        assert _jobs() == 0
    finally:
        page.context.close()


def test_connecting_google_restores_the_send(browser, live_server, email_page, monkeypatch) -> None:
    import email as email_lib
    from base64 import urlsafe_b64decode

    import web.gcal as gcal
    import web.routers.auth as auth

    fake = FakeGmail()
    monkeypatch.setattr(gcal, "get_gmail_service", lambda _tid: fake)
    monkeypatch.setattr(
        auth,
        "get_auth_url",
        lambda: (f"{live_server}/auth/callback?code=abc&state=e2e-st8", "e2e-st8"),
    )
    monkeypatch.setattr(auth, "exchange_code", lambda code, tid: _connect(tid))
    page, s = email_page(browser, live_server)
    try:
        page.click("#advice-list .zf-toggle >> nth=0")  # leave "sleep" out of this send
        page.click("#send-advice-btn", **TRY_ANYWAY)
        page.fill("#email-address", EMAIL)
        page.click('#email-dialog [data-action="connect-google"]')

        page.wait_for_selector("#email-dialog[open] .ed-status")
        assert "google=" not in page.url, "the outcome is read once, then dropped from the URL"
        assert page.input_value("#email-address") == EMAIL
        assert "Google is connected" in page.inner_text("#email-dialog .ed-status")
        assert page.get_attribute("#email-send", "aria-disabled") is None
        assert page.is_hidden("#google-hint")
        assert page.get_attribute("#send-later-btn", "aria-disabled") is None
        assert not page.eval_on_selector(
            "#advice-list .zf-toggle", "b => b.classList.contains('on')"
        ), "the switch made before connecting is kept"

        page.click("#email-send")
        page.wait_for_function("() => !document.getElementById('email-dialog').open")
        assert len(fake.sent) == 1
        mime = email_lib.message_from_bytes(urlsafe_b64decode(fake.sent[0]["raw"]))
        assert mime["To"] == EMAIL
        body = mime.get_payload(decode=True)
        assert isinstance(body, bytes)
        assert "Avoid alcohol" in body.decode("utf-8")
        assert "Lights out" not in body.decode("utf-8"), "the switch made before connecting"
        assert "Sent via email" in page.inner_text("#send-advice-btn")
    finally:
        page.context.close()


def test_cancelling_google_keeps_the_explanation(
    browser, live_server, email_page, monkeypatch
) -> None:
    import web.routers.auth as auth

    monkeypatch.setattr(
        auth,
        "get_auth_url",
        lambda: (f"{live_server}/auth/callback?error=access_denied&state=e2e-st8", "e2e-st8"),
    )
    page, _ = email_page(browser, live_server)
    try:
        page.click("#send-advice-btn", **TRY_ANYWAY)
        page.fill("#email-address", EMAIL)
        page.click('#email-dialog [data-action="connect-google"]')
        page.wait_for_selector("#email-dialog[open] .ed-status")
        assert "nothing was sent" in page.inner_text("#email-dialog .ed-status")
        assert page.input_value("#email-address") == EMAIL
        assert page.get_attribute("#email-send", "aria-disabled") == "true"
    finally:
        page.context.close()


def test_a_refused_send_shows_the_servers_explanation(browser, live_server, email_page) -> None:
    """Phase 5.5: no token → the server's 409 → the dialog explains it and offers its text."""
    page, s = email_page(browser, live_server)
    try:
        page.click("#send-advice-btn", **TRY_ANYWAY)
        page.fill("#email-address", "not an address")
        page.keyboard.press("Enter")  # the form submits on Enter
        page.wait_for_selector("#email-error:not([hidden])")
        assert "valid email address" in page.inner_text("#email-error")

        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        page.fill("#email-address", EMAIL)
        page.keyboard.press("Enter")
        # the blocked form has a Connect link too: wait for the refusal view itself
        page.wait_for_selector("#email-dialog[open] .ed-icon-caution")
        assert page.inner_text("#email-title") == "Not connected to Google"
        assert "cannot send emails on your behalf" in page.inner_text("#email-dialog .ed-text")
        page.click('#email-dialog [data-action="show-email-copy"]')
        text = page.input_value("#email-copy-text")
        assert text.startswith("Your post-treatment recommendations") and "Hi Dana," in text
        assert any(u.endswith("/send-recommendations") for u in requests)
        assert not any(u.endswith("/recommendations-text") for u in requests), "the 409 had it"
        assert s["path"] in page.url
    finally:
        page.context.close()
