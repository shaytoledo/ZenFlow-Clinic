"""Phase 4.2 — the point cards: anatomy, accessibility, selection model, RTL-safe styles.

The card markup is built by plain functions in static/js/treatment/render-points.js; these tests
run them in node (the page's classic scripts, loaded in the page's order, with the language
global stubbed) and inspect the HTML they return. Design: docs/POINT_CARD_DESIGN.md.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from tests.integration import treatment_source as ts

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed"),
]

JS = ts.WEB / "static/js/treatment"


def run_js(expression: str, lang: str = "en") -> Any:
    """Evaluate `expression` after the page's point scripts; return its JSON value."""
    main = (JS / "main.js").read_text(encoding="utf-8")
    esc = re.search(r"function escHtml\(value\) \{.*?\n\}", main, re.DOTALL)
    assert esc is not None
    script = "\n".join(
        [
            f"const _ZF_LANG = {json.dumps(lang)};",
            "const document = { addEventListener() {} };",
            "let usedPoints = []; let aiPointRationale = {};",
            esc.group(0),
            (JS / "point-info.js").read_text(encoding="utf-8"),
            (JS / "render-points.js").read_text(encoding="utf-8"),
            (JS / "point-states.js").read_text(encoding="utf-8"),
            (JS / "points-input.js").read_text(encoding="utf-8"),
            f"process.stdout.write(JSON.stringify({expression}));",
        ]
    )
    # stdin, not `node -e`: the scripts together exceed Windows' command-line limit
    out = subprocess.run(
        ["node", "-"], input=script, capture_output=True, text=True, timeout=30, encoding="utf-8"
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def card(point: dict[str, Any], selected: bool = False, lang: str = "en") -> str:
    html = run_js(f"pointCardHtml({json.dumps(point)}, {json.dumps(selected)})", lang)
    assert isinstance(html, str)
    return html


# ── anatomy ──
def test_a_card_has_the_designed_anatomy() -> None:
    html = card(
        {
            "code": "LR3",
            "rationale": "Moves Liver Qi for the stress headache",
            "needle_technique": "Perpendicular 0.5 cun",
        }
    )
    assert html.startswith("<article")
    assert 'aria-label="LR3 Taichong"' in html
    order = [
        'class="pc-code">LR3<',
        '<h3 class="pc-name">Taichong</h3>',
        'class="pc-toggle"',
        'class="pc-channel"',
        'class="pc-location"',
        'class="pc-why"',
        '<details class="pc-more">',
    ]
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(
        positions
    ), "code → name → toggle → channel → location → why → details"
    assert "Perpendicular 0.5 cun" in html.split('<details class="pc-more">')[1]


def test_the_channel_colour_always_comes_with_the_channel_name() -> None:
    """No colour-only meaning: every themed card names its channel."""
    codes = run_js("Object.keys(POINT_INFO)")
    cards = run_js(f"{json.dumps(codes)}.map((code) => pointCardHtml({{ code }}, false))")
    for code, html in zip(codes, cards, strict=True):
        channel = run_js(f"POINT_INFO[{json.dumps(code)}].channel")
        assert "tp-ch-default" not in html, f"{code}: {channel} has no colour theme"
        assert f"</span>{channel}</p>" in html, code


def test_hebrew_cards_keep_their_channel_colours() -> None:
    """The theme is looked up by the English channel; Hebrew pages used to be all grey."""
    html = card({"code": "LR3"}, lang="he")
    assert "tp-ch-liver" in html
    assert "</span>כבד</p>" in html
    assert "הוסף" in html


def test_pregnancy_cautions_are_on_the_card_face() -> None:
    li4 = card({"code": "LI4"})
    caution = li4.index('class="pc-caution"')
    assert "Traditionally avoided in pregnancy" in li4
    assert caution < li4.index("<details"), "never behind the disclosure"
    assert "pc-caution" not in card({"code": "LR3"})
    assert "pc-caution" in card({"code": "sp-6"}), "codes are normalised first"


def test_the_who_kidney_code_finds_the_reference_data() -> None:
    html = card({"code": "KI3"})
    assert "tp-ch-kidney" in html and "Taixi" in html and 'class="pc-code">KI3<' in html


def test_an_unknown_point_still_renders_plainly() -> None:
    html = card({"code": "XX99", "rationale": "custom"})
    assert "tp-ch-default" in html
    assert "<h3" not in html and "pc-channel" not in html
    assert 'aria-label="XX99"' in html


def test_everything_the_ai_wrote_is_escaped() -> None:
    probe = "<img src=x onerror=alert(1)>"
    html = card(
        {
            "code": "LR3",
            "rationale": probe,
            "location": probe,
            "needle_technique": '" onmouseover="x',
        }
    )
    assert "<img" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert 'onmouseover="x' not in html


# ── selection ──
def test_selected_state_is_not_colour_only() -> None:
    off, on = card({"code": "LR3"}), card({"code": "LR3"}, selected=True)
    assert 'aria-pressed="false"' in off and ">Add<" in off and "is-selected" not in off
    assert 'aria-pressed="true"' in on and ">Added<" in on and "is-selected" in on
    for html in (off, on):
        assert 'class="tp-sr-only"> LR3<' in html, "the toggle says which point it adds"


def test_chips_are_toggles_too() -> None:
    chip = run_js('pointChipHtml({ code: "gb20" }, true)')
    assert 'data-action="toggle-point"' in chip and 'data-code="GB20"' in chip
    assert 'aria-pressed="true"' in chip and "is-selected" in chip
    assert "tp-ch-gallbladder" in chip


def test_toggling_adds_at_the_end_and_removes_in_place() -> None:
    result = run_js(
        "[withPointToggled(['LR3', 'GB20'], 'st36'), withPointToggled(['LR3', 'GB20', 'ST36'], 'GB20')]"
    )
    assert result == [
        {"list": ["LR3", "GB20", "ST36"], "removedAt": -1},
        {"list": ["LR3", "ST36"], "removedAt": 1},
    ]


def test_undo_puts_a_point_back_where_it_was() -> None:
    result = run_js(
        "[withPointRestored(['LR3', 'ST36'], 'GB20', 1),"
        " withPointRestored(['LR3'], 'GB20', 5),"
        " withPointRestored(['LR3', 'GB20'], 'GB20', 0)]"
    )
    assert result == [["LR3", "GB20", "ST36"], ["LR3", "GB20"], ["LR3", "GB20"]]


def test_old_notes_and_odd_entries_are_normalised() -> None:
    result = run_js(
        "[normalizeSuggestedPoints({ ai_suggested_points: ['LR3', null, { code: '' }, { code: 'GB20', rationale: 'r' }] }),"
        " normalizeSuggestedPoints(null, 'Consider LI4 and ST36'),"
        " normalizeSuggestedPoints(null, null)]"
    )
    assert result[0] == [{"code": "LR3", "rationale": ""}, {"code": "GB20", "rationale": "r"}]
    assert {p["code"] for p in result[1]} == {"LI4", "ST36"}
    assert result[2] == []


# ── markup & styles ──
def test_the_cards_use_logical_properties_only() -> None:
    """RTL comes from dir=rtl alone (no row-reverse double flips, commit 2c00db6)."""
    css = (ts.WEB / "static/css/treatment.css").read_text(encoding="utf-8")
    start = css.index("/* ══ Point cards (Phase 4.2")
    block = css[start : css.index("/* ══ end point cards ══ */")]
    block = re.sub(r"/\*.*?\*/", "", block, flags=re.DOTALL)
    assert re.findall(r"\b(left|right|row-reverse)\b", block) == []


def test_the_toggles_and_removals_are_real_buttons_with_names() -> None:
    js = (JS / "points-input.js").read_text(encoding="utf-8")
    tag = re.search(r"<span class=\"zf-point-tag\">.*?</span>`", js, re.DOTALL)
    assert tag is not None
    assert tag.group(0).count('<button type="button"') == 2
    assert "aria-label=" in tag.group(0), "the × button needs a name"
    panel = (ts.WEB / "templates/treatment/point_panel.html").read_text(encoding="utf-8")
    assert 'role="dialog"' in panel and 'aria-labelledby="panel-code"' in panel
    assert 'aria-label="Close"' in panel


def test_the_page_offers_undo_in_a_live_region() -> None:
    markup = ts.markup()
    region = re.search(r'<div class="tp" role="status">(.*?)</div>\s*</div>', markup, re.DOTALL)
    assert region is not None
    assert 'id="points-undo"' in region.group(
        1
    ) and 'data-action="undo-point-removal"' in region.group(1)
