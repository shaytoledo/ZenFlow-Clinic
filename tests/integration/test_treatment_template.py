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
