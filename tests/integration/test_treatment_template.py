"""Phase 4.1 — the treatment page is split into partials, one stylesheet and ordered scripts.

Guards the structure the rest of Phase 4 builds on: the entry template stays small (plan gate:
under 400 lines), everything it includes or loads exists, no CSS or JavaScript creeps back into
the templates (Phase 9's strict CSP needs that), and the rendered page still has every section.
The pixel-level "renders identically" check was a before/after DOM comparison in the browser
(computed styles of every element on four representative sessions); see the PR.
"""

from __future__ import annotations

import re

import pytest

from tests.integration import treatment_source as ts

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
#: every element id the page's scripts look up; losing one breaks a feature silently
SECTION_IDS = (
    "treatment-root",
    "no-telegram-banner",
    "pt-name",
    "ai-points-section",
    "ai-points-grid",
    "ai-points-count",
    "points-undo",
    "points-undo-text",
    "points-undo-btn",
    "intake-card",
    "intake-body",
    "summary-body",
    "tongue-input",
    "pulse-input",
    "rediag-btn",
    "therapist-diagnosis",
    "therapist-notes",
    "regen-points-btn",
    "points-progress",
    "points-progress-bar",
    "cancel-generation-btn",
    "suggested-points",
    "points-tags",
    "point-input",
    "point-count",
    "session-notes",
    "advice-list",
    "send-advice-btn",
    "send-later-btn",
    "complete-btn",
    "followup-card",
    "followup-body",
    "manual-feedback-card",
    "mf-stars",
    "mf-save-btn",
    "point-panel",
    "panel-code",
    "panel-body",
    "treatment-config",
)


def test_the_entry_template_stays_small() -> None:
    assert len(ts.ENTRY.read_text(encoding="utf-8").splitlines()) < 400


def test_everything_the_page_includes_or_loads_exists() -> None:
    assert ts.includes() and ts.scripts() and ts.stylesheets()
    for path in [*ts.includes(), *ts.scripts(), *ts.stylesheets()]:
        assert path.is_file(), f"missing {path}"


def test_no_css_or_javascript_lives_in_the_templates() -> None:
    markup = ts.markup()
    assert "<style" not in markup, "styles belong in static/css/treatment.css"
    inline_scripts = re.findall(r"<script(?![^>]*\bsrc=)([^>]*)>", markup)
    assert inline_scripts == [
        ' type="application/json" id="treatment-config"'
    ], "only the JSON config island may be inline"


def test_no_inline_styles_anywhere_on_the_page() -> None:
    """Phase 4.1c: a strict CSP (Phase 9) ignores style attributes and injected <style> elements.

    Styles are classes in static/css/treatment.css; a value that comes from data (a width) is
    set through the CSSOM (`el.style.width = …`), which the CSP allows.
    """
    source = ts.source()
    assert re.findall(r"""\sstyle\s*=\s*["'`\\]""", source) == []
    assert "cssText" not in source
    assert not re.search(r"""createElement\(\s*["']style["']""", source)


def _page_css() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in ts.stylesheets())


#: page-scoped class names: tp-* (Phase 4.1c) and pc-* (point cards, Phase 4.2)
CLASS_NAME = r"(?:tp|pc)-[a-z0-9-]+"


def _defined_classes() -> set[str]:
    return set(re.findall(r"\.(" + CLASS_NAME + ")", _page_css()))


def _used_classes() -> set[str]:
    return set(re.findall(r"\b" + CLASS_NAME, ts.source()))


def test_the_stylesheet_is_balanced() -> None:
    """A stray brace makes the browser drop the rule after it, silently."""
    css = re.sub(r"/\*.*?\*/", "", _page_css(), flags=re.DOTALL)
    depth = 0
    for char in css:
        depth += {"{": 1, "}": -1}.get(char, 0)
        assert depth >= 0, "a '}' closes nothing"
    assert depth == 0


def test_every_page_class_is_defined_in_the_stylesheet() -> None:
    defined = _defined_classes()
    used = _used_classes()
    prefixes = {name for name in used if name.endswith("-")}
    assert prefixes == {"tp-tone-", "tp-ch-"}, "a new computed class name needs a check below"
    assert sorted(used - prefixes - defined) == []


def test_the_stylesheet_has_no_dead_classes() -> None:
    computed = {name for name in _defined_classes() if name.startswith(("tp-tone-", "tp-ch-"))}
    assert sorted(_defined_classes() - _used_classes() - computed) == []


def test_every_computed_class_is_defined_in_the_stylesheet() -> None:
    """Tone and channel class names are assembled in JS from these literal maps."""
    js = ts.javascript()

    def literals(pattern: str) -> list[str]:
        found = re.search(pattern, js, re.DOTALL)
        assert found is not None, pattern
        return re.findall(r"'([a-z-]+)'", found.group(1))

    tones = {
        *literals(r"function _certaintyTone\(pct\) \{(.*?)\n\}"),
        *literals(r"const improvementTones = \{(.*?)\};"),
        *literals(r"const improvementTone = (.*?);"),
        *literals(r"const painTone = (.*?);"),
    }
    # the map's keys are channel names ('Large Intestine'); only its lowercase values match
    channels = {*literals(r"const CHANNEL_THEMES = \{(.*?)\};"), "default"}

    assert len(tones) >= 5 and len(channels) == 15
    missing = {f"tp-tone-{t}" for t in tones} | {f"tp-ch-{c}" for c in channels}
    assert sorted(missing - _defined_classes()) == []


def test_the_scripts_start_the_page_last() -> None:
    """Classic scripts share globals and run in order: start-up must come after every definition."""
    starters = [
        path.name
        for path in ts.scripts()
        if "loadTreatment();" in path.read_text(encoding="utf-8").splitlines()
    ]
    assert starters == [ts.scripts()[-1].name]


async def test_the_rendered_page_keeps_every_section(
    make_therapist, make_appointment, login_as, fake_redis
) -> None:
    therapist = make_therapist(email="tpl@example.com", password=PW)
    apt = make_appointment(therapist=therapist)
    client = await login_as(therapist)
    slug = f"{apt['patient_id']}/{apt['date']}/{apt['time'].replace(':', '-')}"
    page = await client.get(f"/treatment/{slug}")
    assert page.status_code == 200
    missing = [i for i in SECTION_IDS if f'id="{i}"' not in page.text]
    assert missing == []
    for path in [*ts.scripts(), *ts.stylesheets()]:
        rel = "/static/" + path.relative_to(ts.WEB / "static").as_posix()
        assert rel in page.text
        assert (await client.get(rel)).status_code == 200, rel
