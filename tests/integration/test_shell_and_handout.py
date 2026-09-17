"""Phase 4.2c — the app shell works on narrow screens, and the treatment page prints a handout.

- Below 900px the sidebar is a drawer behind a labelled menu button (static/js/shell.js,
  static/css/shell.css); printing any page drops the app chrome.
- Printing the treatment page prints only the patient handout (static/js/treatment/handout.js):
  today's points with names and locations, and the recommendations left switched on.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.integration import treatment_source as ts
from tests.integration.test_point_cards import JS, run_js

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
BASE = ts.WEB / "templates/base.html"
SHELL_CSS = ts.WEB / "static/css/shell.css"


# ── responsive shell ──
def test_the_menu_button_controls_the_sidebar() -> None:
    base = BASE.read_text(encoding="utf-8")
    button = re.search(r"<button[^>]*id=\"zf-menu-btn\"[^>]*>", base, re.DOTALL)
    assert button is not None
    for attr in ('aria-controls="zf-sidebar"', 'aria-expanded="false"', "aria-label="):
        assert attr in button.group(0)
    assert 'id="zf-sidebar"' in base and 'id="zf-scrim"' in base
    assert '<script src="/static/js/shell.js"></script>' in base
    assert '<link rel="stylesheet" href="/static/css/shell.css" />' in base


def test_the_drawer_only_exists_on_narrow_screens() -> None:
    css = SHELL_CSS.read_text(encoding="utf-8")
    narrow = css[css.index("@media (max-width: 900px)") :]
    narrow = narrow[: narrow.index("\n}\n")]
    assert ".zf-sidebar {" in narrow and "position: fixed" in narrow
    assert ".zf-app.nav-open .zf-sidebar" in narrow
    assert '[dir="rtl"] .zf-sidebar' in narrow, "the drawer slides in from the reading side"
    before = css[: css.index("@media (max-width: 900px)")]
    assert ".zf-menu-btn," in before and "display: none" in before


def test_the_drawer_script_is_keyboard_friendly() -> None:
    js = (ts.WEB / "static/js/shell.js").read_text(encoding="utf-8")
    assert "aria-expanded" in js
    assert "'Escape'" in js and "button.focus()" in js
    assert "main.inert = open" in js, "the page behind the open drawer takes no focus"


def test_printing_drops_the_app_chrome() -> None:
    css = SHELL_CSS.read_text(encoding="utf-8")
    printed = css[css.index("@media print") :]
    assert "overflow: visible" in printed and "height: auto" in printed
    assert ".zf-sidebar," in printed and ".zf-topbar," in printed


async def test_every_page_serves_the_shell(make_therapist, login_as, fake_redis) -> None:
    client = await login_as(make_therapist(email="shell@example.com", password=PW))
    page = await client.get("/patients")
    assert page.status_code == 200
    assert 'id="zf-menu-btn"' in page.text
    for asset in ("/static/js/shell.js", "/static/css/shell.css", "/static/css/tokens.css"):
        assert (await client.get(asset)).status_code == 200, asset


def test_the_menu_label_is_translated() -> None:
    for lang in ("en", "he"):
        locale = json.loads((ts.WEB.parent / f"locales/{lang}.json").read_text(encoding="utf-8"))
        assert locale.get("nav_menu") and locale.get("treatment_print_handout"), lang


# ── patient handout ──
def handout(data: dict[str, object], lang: str = "en") -> str:
    html = run_js(f"handoutHtml({json.dumps(data)})", lang)
    assert isinstance(html, str)
    return html


def test_the_handout_lists_points_and_advice() -> None:
    html = handout(
        {
            "patient": "Dana Levi",
            "when": "Sunday, 20 September 2026 at 09:00",
            "therapist": "Dr Preview",
            "points": [{"code": "LR3", "name": "Taichong", "location": "Dorsum of foot"}],
            "advice": [{"category": "Sleep", "text": "Wind down"}],
        }
    )
    assert "Your treatment today" in html
    assert "Dana Levi · Sunday, 20 September 2026 at 09:00" in html
    assert '<td class="tp-handout-code">LR3</td><td>Taichong</td><td>Dorsum of foot</td>' in html
    assert "<strong>Sleep</strong> — Wind down" in html
    assert "Your therapist: Dr Preview" in html


def test_an_empty_session_says_so() -> None:
    html = handout({})
    assert "No points were recorded" in html and "No recommendations" in html
    assert "Your therapist" not in html


def test_the_handout_escapes_everything() -> None:
    probe = "<img src=x onerror=alert(1)>"
    html = handout(
        {
            "patient": probe,
            "therapist": probe,
            "points": [{"code": probe, "name": probe, "location": probe}],
            "advice": [{"category": probe, "text": probe}],
        }
    )
    assert "<img" not in html


def test_handout_points_come_from_the_reference_data_only() -> None:
    """Codes in, names and locations out: no AI rationale can reach the patient's copy."""
    points = run_js("handoutPoints(['LR3', 'KI3', 'XX9'])")
    assert points == [
        {
            "code": "LR3",
            "name": "Taichong",
            "location": "Dorsum of foot, depression between 1st and 2nd metatarsal bones",
        },
        {"code": "KI3", "name": "Taixi", "location": points[1]["location"]},
        {"code": "XX9", "name": "", "location": ""},
    ]
    assert points[1]["location"]


def test_the_handout_is_hebrew_on_hebrew_pages() -> None:
    html = handout({"therapist": "ד״ר", "points": [], "advice": []}, lang="he")
    assert "הטיפול שלך היום" in html and "המטפל/ת שלך: ד״ר" in html
    keys = run_js("[Object.keys(HANDOUT_TEXT.en).sort(), Object.keys(HANDOUT_TEXT.he).sort()]")
    assert keys[0] == keys[1]


def test_only_switched_on_advice_is_printed() -> None:
    source = (JS / "handout.js").read_text(encoding="utf-8")
    assert "advice: advice.filter((a) => a.enabled)" in source
    assert "points: handoutPoints(usedPoints)" in source


def test_printing_the_page_prints_only_the_handout() -> None:
    css = (ts.WEB / "static/css/treatment.css").read_text(encoding="utf-8")
    assert ".tp .tp-handout { display: none; }" in css
    printed = css[css.index("@media print") :]
    assert ".tp.tp-page > :not(.tp-handout)" in printed
    assert ".tp.zf-point-panel" in printed and ".tp .pc-undo" in printed
    assert 'id="print-handout"' in ts.markup()
    assert "window.addEventListener('beforeprint', renderHandout)" in (JS / "handout.js").read_text(
        encoding="utf-8"
    )
