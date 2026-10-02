"""SF-016 / SF-021 — no dashboard page can be scripted through the data it shows, and none needs
inline script, so the Content-Security-Policy can enforce.

The treatment page has had these rules since Phase 4.1b (`test_treatment_page_xss.py`). Every other
page rendered API data — patient names from Telegram profiles, AI intake summaries a patient can
steer — into `innerHTML` raw, and wired its buttons with inline `on*=` handlers that an enforcing CSP
blocks. The rules, for every template:

- no inline `on*=` handler anywhere — elements declare `data-click` / `data-on-input` /
  `data-on-change` / `data-on-enter` / `data-on-blur`, dispatched by `static/js/actions.js`;
- every such name is registered by the page (or by `base.html`) with `ZF.actions.register`;
- every `${…}` interpolation in a page's script is `ZF.esc(…)`, `Number(…)`,
  `encodeURIComponent(…)`, a nested template (checked through its own `${…}`), a literal ternary,
  or listed in that template as `{# xss-reviewed: <expr> | <reason> #}`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

WEB = Path(__file__).resolve().parents[2] / "web"
TEMPLATES = WEB / "templates"
ACTION_ATTR = re.compile(r"""data-(?:click|on-input|on-change|on-enter|on-blur)=\\?["']([\w-]+)""")
REGISTERED = re.compile(r"""^\s*'([\w-]+)':\s*\(""", re.MULTILINE)
#: `{# xss-reviewed: <expr> | <reason> #}` — split at the last " | ", so `a || b` is one expression
REVIEW_LINE = re.compile(r"\{#\s*xss-reviewed:\s*(.+)\s\|\s([^|]+?)\s*#\}")
SAFE_CALLS = ("ZF.esc(", "Number(", "encodeURIComponent(")
#: `cond ? 'literal' : 'literal'` needs no escaping
LITERAL_TERNARY = re.compile(
    r"""^[\w.!=<>&|()' ]+\?\s*('[^'`$]*'|"[^"`$]*")\s*:\s*('[^'`$]*'|"[^"`$]*")$"""
)


def _pages() -> list[Path]:
    """Every template except the treatment page's own, which test_treatment_page_xss.py covers."""
    return sorted(
        p
        for p in TEMPLATES.rglob("*.html")
        if p.name != "treatment.html" and "treatment" not in p.relative_to(TEMPLATES).parts[:-1]
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


def _reviewed(src: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in REVIEW_LINE.finditer(src)}


def _is_safe(expr: str) -> bool:
    return expr.startswith(SAFE_CALLS) or "`" in expr or bool(LITERAL_TERNARY.match(expr))


def test_no_inline_event_handlers_on_any_page_or_script() -> None:
    sources = [*TEMPLATES.rglob("*.html"), *(WEB / "static").rglob("*.js")]
    offenders = {
        str(p.relative_to(WEB)): hits
        for p in sources
        if (hits := re.findall(r"""\son[a-z]+\s*=\s*\\?["']""", p.read_text(encoding="utf-8")))
    }
    assert offenders == {}, "use data-click / data-on-* and ZF.actions.register (SF-016)"


def test_every_declared_action_is_registered() -> None:
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    missing = {}
    for page in _pages():
        src = page.read_text(encoding="utf-8")
        declared = set(ACTION_ATTR.findall(src))
        handled = set(REGISTERED.findall(src)) | set(REGISTERED.findall(base))
        if declared - handled:
            missing[page.name] = sorted(declared - handled)
    assert missing == {}


def test_every_interpolation_is_escaped_or_reviewed() -> None:
    unreviewed = {}
    for page in _pages():
        src = page.read_text(encoding="utf-8")
        reviewed = _reviewed(src)
        bad = sorted({e for e in _interpolations(src) if not _is_safe(e) and e not in reviewed})
        if bad:
            unreviewed[page.name] = bad
    assert unreviewed == {}, (
        "wrap untrusted values in ZF.esc(); if a value is provably safe, add "
        "{# xss-reviewed: <expr> | <reason> #} to the template"
    )


def test_no_stale_reviews() -> None:
    """A review whose code is gone would silently approve a future value with the same name."""
    stale = {}
    for page in _pages():
        src = page.read_text(encoding="utf-8")
        used = set(_interpolations(src))
        if gone := sorted(set(_reviewed(src)) - used):
            stale[page.name] = gone
    assert stale == {}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_zf_esc_escapes_text_and_attribute_breakouts() -> None:
    script = (WEB / "static/js/actions.js").read_text(encoding="utf-8")
    probe = """<img src=x onerror="a('b')">&`"""
    program = (
        "const document = {addEventListener(){}}; const window = {}; class Element {};\n"
        + script
        + f"\nprocess.stdout.write(JSON.stringify(window.ZF.esc({json.dumps(probe)})));"
        + "\nprocess.stdout.write('|' + window.ZF.esc(null) + '|' + window.ZF.esc(7));"
    )
    out = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    escaped, rest = out.split("|", 1)
    escaped = json.loads(escaped)
    assert not any(ch in escaped for ch in "<>\"'`")
    assert escaped.count("&amp;") == 1
    assert rest == "|7"
