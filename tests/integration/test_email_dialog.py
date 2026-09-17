"""Phase 5.3 — the treatment page's email dialog: preflight, explanation, copy fallback, return.

The markup and the pending-send helpers in static/js/treatment/email-dialog.js run in node here;
the dialog's behaviour in a real browser is covered by tests/e2e/test_email_flow.py.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from tests.integration import treatment_source as ts

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
JS = ts.WEB / "static/js/treatment"
PAGE = "/treatment/-5/2026-03-02/10-00"
ADVICE = [
    {"id": "sleep", "category": "Sleep", "text": "Sleep before 23:00", "enabled": True},
    {"id": "diet", "category": "Diet", "text": "Warm food", "enabled": False},
]
node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _strings(lang: str = "en") -> dict[str, str]:
    from web.i18n import get_t
    from web.routers.pages import EMAIL_DIALOG_KEYS

    t = get_t(lang)
    return {key: t[key] for key in EMAIL_DIALOG_KEYS}


def run_dialog(
    expression: str,
    *,
    google: dict[str, Any] | None = None,
    lang: str = "en",
    manual: bool = True,
    path: str = PAGE,
    storage: dict[str, str] | None = None,
) -> Any:
    main = (JS / "main.js").read_text(encoding="utf-8")
    esc = re.search(r"function escHtml\(value\) \{.*?\n\}", main, re.DOTALL)
    assert esc is not None
    config = {"google": google or {"connected": False, "reason": "not_connected"}}
    config["email_text"] = _strings(lang)
    script = "\n".join(
        [
            "const document = { addEventListener() {}, getElementById() { return null; } };",
            f"const window = {{ location: {{ pathname: {json.dumps(path)} }} }};",
            f"const _store = new Map(Object.entries({json.dumps(storage or {})}));",
            "const sessionStorage = { getItem: (k) => (_store.has(k) ? _store.get(k) : null),"
            " setItem: (k, v) => _store.set(k, String(v)), removeItem: (k) => _store.delete(k) };",
            f"const ZF_CONFIG = {json.dumps(config)};",
            f"let _isManual = {json.dumps(manual)};",
            f"const patientId = {json.dumps(path.split('/')[2])};",
            f"let advice = {json.dumps(ADVICE)};",
            esc.group(0),
            (JS / "email-dialog.js").read_text(encoding="utf-8"),
            f"process.stdout.write(JSON.stringify({expression}));",
        ]
    )
    out = subprocess.run(
        ["node", "-"], input=script, capture_output=True, text=True, timeout=30, encoding="utf-8"
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── markup ──
@node
def test_the_address_form_is_plain_while_google_works() -> None:
    html = run_dialog('emailAskHtml({ email: "p@example.com" })', google={"connected": True})
    assert 'value="p@example.com"' in html
    assert "ed-notice" not in html and "aria-disabled" not in html
    assert '<label class="ed-label" for="email-address">Patient email address</label>' in html
    assert "This patient has no Telegram account." in html, "a translated explanation"


@node
def test_a_blocked_form_explains_and_still_lets_the_therapist_try() -> None:
    html = run_dialog('emailAskHtml({ blocked: true, reason: "not_connected" })')
    assert 'type="submit" id="email-send"' in html
    assert 'aria-disabled="true" aria-describedby="email-blocked"' in html
    assert '<p class="ed-notice-title">Not connected to Google</p>' in html
    assert 'id="email-blocked">Your Google account is not connected' in html
    assert (
        'href="/auth/login?next=%2Ftreatment%2F-5%2F2026-03-02%2F10-00" '
        'data-action="connect-google" data-kind="email">Connect Google</a>' in html
    )
    assert 'data-action="show-email-copy">Copy the text instead</button>' in html


@node
def test_an_expired_token_asks_to_reconnect() -> None:
    html = run_dialog('emailAskHtml({ blocked: true, reason: "token_invalid" })')
    assert "Google connection expired" in html and ">Reconnect Google</a>" in html


@node
def test_the_refusal_view_prefers_the_servers_words_and_escapes_them() -> None:
    html = run_dialog(
        'emailGoogleHtml({ reason: "not_connected", title: "<b>T</b>", message: "M & N" })'
    )
    assert "&lt;b&gt;T&lt;/b&gt;" in html and "<b>T" not in html
    assert "M &amp; N" in html
    assert "You will go to Google and come back to this session afterwards." in html
    assert 'data-action="show-email-copy"' in html and 'data-action="close-email-dialog"' in html


@node
def test_the_copy_view_escapes_the_text() -> None:
    html = run_dialog('emailCopyHtml("Hi </textarea><script>x()</script>")')
    assert "</textarea><script>" not in html
    assert "Hi &lt;/textarea&gt;&lt;script&gt;" in html
    assert 'readonly aria-labelledby="email-title"' in html


@node
def test_the_hint_names_the_problem_and_the_fix() -> None:
    html = run_dialog('googleHintHtml("not_connected")')
    assert html.startswith("This patient can only be reached by email")
    assert "Not connected to Google." in html
    assert 'data-kind="later">Connect Google</a>' in html


@node
def test_hebrew_strings() -> None:
    html = run_dialog('emailAskHtml({ blocked: true, reason: "not_connected" })', lang="he")
    assert "אין חיבור לחשבון Google" in html and "שלח/י באימייל" in html


@node
def test_only_email_only_patients_are_blocked() -> None:
    assert run_dialog("[isEmailOnly(), isGoogleBlocked()]") == [True, True]
    assert run_dialog("isEmailOnly()", manual=False, path="/treatment/7/2026-03-02/10-00") is False
    assert run_dialog("isEmailOnly()", manual=False) is True, "a negative id is a manual booking"
    assert run_dialog("isGoogleBlocked()", google={"connected": None}) is False, "unknown ≠ blocked"


# ── the round trip ──
@node
def test_a_pending_send_survives_the_trip_to_google() -> None:
    result = run_dialog(
        "(() => { savePendingEmail({ kind: 'email', email: 'p@example.com' });"
        " const back = takePendingEmail(); return [back, takePendingEmail()]; })()"
    )
    back, again = result
    assert back["kind"] == "email" and back["email"] == "p@example.com"
    assert back["path"] == PAGE
    assert back["advice"] == [
        {"id": "sleep", "enabled": True, "text": "Sleep before 23:00"},
        {"id": "diet", "enabled": False, "text": "Warm food"},
    ]
    assert again is None, "used once"


@node
@pytest.mark.parametrize(
    "saved",
    [
        {"path": "/treatment/9/2026-03-02/10-00", "at": "NOW", "kind": "email"},  # another page
        {"path": PAGE, "at": "OLD", "kind": "email"},  # older than 30 minutes
        "not json",
    ],
)
def test_a_stale_or_foreign_pending_send_is_ignored(saved: Any) -> None:
    raw = saved if isinstance(saved, str) else json.dumps(saved)
    expression = (
        "(() => { const v = sessionStorage.getItem('zf:pending-email')"
        ".replace('\"NOW\"', Date.now()).replace('\"OLD\"', Date.now() - 31 * 60 * 1000);"
        " sessionStorage.setItem('zf:pending-email', v);"
        " return [takePendingEmail(), sessionStorage.getItem('zf:pending-email')]; })()"
    )
    assert run_dialog(expression, storage={"zf:pending-email": raw}) == [None, None]


@node
def test_restoring_keeps_known_items_only() -> None:
    restored = run_dialog(
        "restoredAdvice(advice, [{ id: 'diet', enabled: true, text: 'Warm, cooked food' },"
        " { id: 'sleep', enabled: false, text: '  ' }, { id: 'ghost', enabled: true }, null])"
    )
    assert restored == [
        {"id": "sleep", "category": "Sleep", "text": "Sleep before 23:00", "enabled": False},
        {"id": "diet", "category": "Diet", "text": "Warm, cooked food", "enabled": True},
    ]
    assert run_dialog("restoredAdvice(advice, 'x')") == ADVICE


def test_every_string_the_dialog_uses_is_served_in_both_languages() -> None:
    from web.routers.pages import EMAIL_DIALOG_KEYS

    js = (JS / "email-dialog.js").read_text(encoding="utf-8")
    used = set(re.findall(r"emailText\(\s*'([a-z_]+)'", js))
    used |= set(re.findall(r"'(email_[a-z_]+)'", js))
    assert used, "no strings found — did the file change shape?"
    assert sorted(used - set(EMAIL_DIALOG_KEYS)) == []
    for lang in ("en", "he"):
        strings = _strings(lang)
        assert all(strings[key] and strings[key] != key for key in EMAIL_DIALOG_KEYS), lang


def test_the_page_wires_a_native_dialog() -> None:
    markup = ts.markup()
    assert (
        '<dialog id="email-dialog" class="tp ed" aria-labelledby="email-title"></dialog>' in markup
    )
    js = ts.javascript()
    assert "dialog.showModal()" in js and "openEmailFallback" not in js
    names = [p.name for p in ts.scripts()]
    assert names.index("email-dialog.js") < names.index("main.js")


# ── server side ──
@pytest.fixture
async def email_session(make_therapist, make_appointment, make_patient, login_as):
    async def _make(lang: str = "en") -> dict[str, Any]:
        therapist = make_therapist(email=f"ed-{lang}@example.com", password=PW, language=lang)
        apt = make_appointment(therapist=therapist, patient=make_patient("Dana Levi", manual=True))
        return {"therapist": therapist, "client": await login_as(therapist), "apt": apt}

    return _make


def _base(apt: dict[str, Any]) -> str:
    return f"/api/treatment-notes/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"


async def test_the_text_to_copy_holds_the_enabled_items_and_sends_nothing(
    email_session, monkeypatch
) -> None:
    import web.services.email_service as es

    def _must_not_send(*_a: Any, **_k: Any) -> None:
        raise AssertionError("recommendations-text must not send")

    monkeypatch.setattr(es, "send_email", _must_not_send)
    s = await email_session()
    resp = await s["client"].post(
        _base(s["apt"]) + "/recommendations-text",
        json={
            "items": [
                {"enabled": True, "category": "Sleep", "text": "Sleep before 23:00"},
                {"enabled": False, "category": "Diet", "text": "Warm food"},
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    text = resp.json()["text"]
    assert text.startswith("Your post-treatment recommendations")
    assert "Hi Dana," in text and "• Sleep: Sleep before 23:00" in text
    assert "Warm food" not in text


async def test_the_text_needs_an_item_and_the_callers_session(
    email_session, make_therapist, login_as
) -> None:
    s = await email_session()
    url = _base(s["apt"]) + "/recommendations-text"
    empty = await s["client"].post(url, json={"items": [{"enabled": False, "text": "x"}]})
    assert empty.status_code == 400
    other = make_therapist(email="other-ed@example.com", password=PW)
    stranger = await login_as(other)
    resp = await stranger.post(url, json={"items": [{"enabled": True, "text": "x"}]})
    assert resp.status_code == 404


async def test_the_page_carries_the_dialog_strings(email_session) -> None:
    from web.routers.pages import EMAIL_DIALOG_KEYS

    s = await email_session("he")
    apt = s["apt"]
    page = await s["client"].get(
        f"/treatment/{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    )
    island = re.search(
        r'<script type="application/json" id="treatment-config">(.*?)</script>', page.text
    )
    assert island is not None
    config = json.loads(island.group(1))
    assert set(config["email_text"]) == set(EMAIL_DIALOG_KEYS)
    assert config["email_text"]["email_dialog_send"] == "שלח/י באימייל"
    assert '<dialog id="email-dialog"' in page.text and 'id="google-hint"' in page.text
