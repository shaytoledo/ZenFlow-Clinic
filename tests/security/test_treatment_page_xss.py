"""Phase 4.1b / F10 — the treatment page cannot be scripted through the data it shows.

The page renders AI output (diagnosis, advice, point codes and rationales — all steerable by
whatever a patient types into the intake), patient input and database rows with `innerHTML`.
Before this phase the AI advice text was interpolated raw, and point codes were spliced into
`onclick="quickAddPoint('…')"`; a planted `<img onerror>` in the advice executed in the browser.

The rules, enforced here:
- no inline `on*=` handlers anywhere on the page (Phase 9's CSP forbids them); actions are
  `data-action` attributes dispatched by static/js/treatment/events.js;
- every `${…}` interpolation in the page's scripts is `escHtml(…)` or on the reviewed list
  below, which only holds constants, numbers, URL parts and strings built in the same file;
- `escHtml` escapes the characters that break out of text *and* quoted attributes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from tests.integration import treatment_source as ts

pytestmark = pytest.mark.security

#: Reviewed un-escaped interpolations. Add to this only with a reason next to it.
REVIEWED = {
    # URL path parts of the page itself (percent-encoded by the browser)
    "patientId",
    "aptDate",
    "aptTimeSlug",
    "q",  # '' or '?force=true'
    # numbers
    "certainty",  # coerced with Number() in render-diagnosis.js
    "count",
    "newCount",
    "r.status",
    "history.length",
    "pointObjects.length",
    "usedPoints.length",
    # built from constants in the same function
    "lbl",
    "icon",
    "dateStr",  # Date.toLocaleDateString()
    "channel",  # 'email' | 'Telegram'
    "msg",  # one of three literal messages
    "msgs",  # joined from escHtml()-ed parts
    "needleSVG",
    "actionsLabel",
    "locationLabel",
    # CSS class names from literal maps (Phase 4.1c; checked against treatment.css below)
    "tone",  # _certaintyTone()
    "painTone",
    "improvementTone",
    "themeClass",  # channelThemeClass()
    # point cards (Phase 4.2): SVG constants, and markup assembled from escHtml()-ed parts
    "ICON_ADD",
    "ICON_DONE",
    "ICON_PIN",
    "ICON_CAUTION",
    "body",
    "rows",
    "pointToggleHtml(code, selected)",
    # inside an escHtml(`…`) template — escaped as a whole
    "phone",
    # a CSS selector, not HTML
    "CSS.escape(c)",
}
#: `cond ? 'literal' : 'literal'` needs no escaping
LITERAL_TERNARY = re.compile(
    r"""^[\w.!=' ]+\?\s*('[^'`$]*'|"[^"`$]*")\s*:\s*('[^'`$]*'|"[^"`$]*"|' ' \+ channelLabel)$"""
)


def _interpolations(src: str) -> list[str]:
    found, i = [], 0
    while (i := src.find("${", i)) >= 0:
        depth, j = 1, i + 2
        while j < len(src) and depth:
            depth += {"{": 1, "}": -1}.get(src[j], 0)
            j += 1
        found.append(src[i + 2 : j - 1].strip())
        i += 2
    return found


def test_no_inline_event_handlers_anywhere_on_the_page() -> None:
    offenders = re.findall(r"""\son[a-z]+\s*=\s*["']""", ts.source())
    assert offenders == []


def test_every_interpolation_is_escaped_or_reviewed() -> None:
    unreviewed = set()
    for expr in _interpolations(ts.javascript()):
        if expr.startswith(("escHtml(", "escHtml (")) or "`" in expr:
            continue  # nested templates are checked through their own ${…}
        if expr in REVIEWED or LITERAL_TERNARY.match(expr):
            continue
        unreviewed.add(expr)
    assert unreviewed == set(), (
        "wrap untrusted values in escHtml(); if a value is provably safe, add it to REVIEWED "
        f"with the reason: {sorted(unreviewed)}"
    )


def test_every_data_action_has_a_handler() -> None:
    events = (ts.WEB / "static/js/treatment/events.js").read_text(encoding="utf-8")
    declared = set(re.findall(r"""data-action=\\?["']([\w-]+)""", ts.source()))
    declared.discard("name")  # the placeholder in events.js's own header comment
    handled = set(re.findall(r"^\s*'([\w-]+)':\s*\(", events, re.MULTILINE))
    assert declared, "no data-action found — did the page change shape?"
    assert declared <= handled, f"no handler for {sorted(declared - handled)}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_esc_html_neutralises_markup_and_attribute_breakouts() -> None:
    main = (ts.WEB / "static/js/treatment/main.js").read_text(encoding="utf-8")
    fn = re.search(r"function escHtml\(value\) \{.*?\n\}", main, re.DOTALL)
    assert fn is not None
    probes = [
        "<img src=x onerror=alert(1)>",
        "x') , alert(1), ('",
        '" onmouseover="x',
        "a & b",
        0,
        None,
    ]
    script = fn.group(0) + "\nconsole.log(JSON.stringify(" + json.dumps(probes) + ".map(escHtml)));"
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    escaped = json.loads(out)
    assert escaped == [
        "&lt;img src=x onerror=alert(1)&gt;",
        "x&#39;) , alert(1), (&#39;",
        "&quot; onmouseover=&quot;x",
        "a &amp; b",
        "0",
        "",
    ]
