"""Phase 4.2e — visual snapshots of the point cards and the app shell.

The treatment page's AI points section is captured at 3 widths × LTR/RTL × light/dark, and the
whole viewport once per width. Each image is compared with a committed baseline,
tests/e2e/snapshots/<name>-<platform>.png; fonts differ between systems, so baselines are per
platform.

    ZF_UPDATE_SNAPSHOTS=1 python -m pytest tests/e2e     # write or refresh the baselines

A failing comparison writes what it saw to tests/e2e/snapshots/_actual/ (git-ignored).
Rendering is made repeatable: external requests (web fonts) are blocked, transitions and
animations are off, the topbar clock is hidden, and the data is fixed.
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

SNAPSHOTS = Path(__file__).parent / "snapshots"
PW = "pw-Test-123"
#: share of pixels that may differ (anti-aliasing) before a snapshot counts as changed
TOLERANCE = 0.002
WIDTHS = {"phone": (375, 812), "tablet": (768, 1024), "desktop": (1280, 800)}
STEADY_CSS = """
*, *::before, *::after { transition: none !important; animation: none !important; caret-color: transparent !important; }
#topbar-datetime { visibility: hidden !important; }
"""
POINTS = [
    {
        "code": "LR3",
        "rationale": "Moves Liver Qi for the stress headache",
        "needle_technique": "Perpendicular 0.5 cun",
    },
    {"code": "GB20", "rationale": "Clears the head, eases the temporal pain"},
    {"code": "LI4", "rationale": "Command point for the face and head"},
]


def assert_matches_baseline(name: str, png: bytes) -> None:
    from PIL import Image, ImageChops

    path = SNAPSHOTS / f"{name}-{sys.platform}.png"
    if os.environ.get("ZF_UPDATE_SNAPSHOTS") == "1":
        path.write_bytes(png)
        return
    if not path.exists():
        pytest.fail(
            f"no baseline {path.name}: run with ZF_UPDATE_SNAPSHOTS=1 and review the images"
        )
    expected = Image.open(path).convert("RGB")
    actual = Image.open(io.BytesIO(png)).convert("RGB")
    changed = 1.0
    if expected.size == actual.size:
        diff = (
            ImageChops.difference(expected, actual)
            .convert("L")
            .point(lambda v: 255 if v > 16 else 0)
        )
        changed = diff.histogram()[255] / (actual.width * actual.height)
    if changed > TOLERANCE:
        out = SNAPSHOTS / "_actual"
        out.mkdir(exist_ok=True)
        (out / path.name).write_bytes(png)
        pytest.fail(
            f"{name} changed: {changed:.2%} of pixels (sizes {expected.size} → {actual.size}); "
            f"see {out / path.name}"
        )


@pytest.fixture
def session_page(make_therapist, make_appointment, make_treatment_notes):
    """A signed-in treatment page for a finished run, in the requested language."""
    from web.repositories.treatment_repo import set_points_status

    def _open(browser: Any, base: str, lang: str, width: str) -> Any:
        therapist = make_therapist(
            name="Dr Preview", email=f"e2e-{lang}@example.com", password=PW, language=lang
        )
        apt = make_appointment(
            therapist=therapist, apt_date="2026-09-20", apt_time="09:00", summary="Headache"
        )
        make_treatment_notes(apt, ai_suggested_points=POINTS, used_points=["LR3"])
        set_points_status(apt["id"], "COMPLETED")

        w, h = WIDTHS[width]
        context = browser.new_context(viewport={"width": w, "height": h}, reduced_motion="reduce")
        context.route(
            "**/*",
            lambda route: (
                route.continue_() if route.request.url.startswith(base) else route.abort()
            ),
        )
        signin = context.request.post(
            f"{base}/register/signin",
            form={"email": therapist["email"], "password": PW},
            max_redirects=0,
        )
        assert signin.status in (302, 303, 307), signin.status
        page = context.new_page()
        page.goto(f"{base}/treatment/{apt['patient_id']}/2026-09-20/09-00")
        page.wait_for_selector(".pc >> nth=2")
        page.add_style_tag(content=STEADY_CSS)
        return page

    return _open


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("lang", ["en", "he"])
@pytest.mark.parametrize("width", list(WIDTHS))
def test_point_cards(browser, live_server, session_page, width: str, lang: str, theme: str) -> None:
    page = session_page(browser, live_server, lang, width)
    try:
        if theme == "dark":
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
        assert page.evaluate("document.documentElement.dir") == ("rtl" if lang == "he" else "ltr")
        section = page.locator("#ai-points-section")
        section.scroll_into_view_if_needed()
        assert_matches_baseline(f"points-{width}-{lang}-{theme}", section.screenshot())
    finally:
        page.context.close()


@pytest.mark.parametrize("width", list(WIDTHS))
def test_app_shell(browser, live_server, session_page, width: str) -> None:
    page = session_page(browser, live_server, "en", width)
    try:
        assert_matches_baseline(f"shell-{width}", page.screenshot())
        if width == "phone":
            page.click("#zf-menu-btn")
            assert page.get_attribute("#zf-menu-btn", "aria-expanded") == "true"
            assert_matches_baseline("shell-phone-drawer", page.screenshot())
    finally:
        page.context.close()
