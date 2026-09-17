"""Phase 4.2b — the AI points area shows one designed state for whatever the run is doing.

The mapping (`stateOfNotes`) and the panels (`pointMessageHtml`, `skeletonCardHtml`) are plain
functions in static/js/treatment/point-states.js; they run in node here. The browser check of
the rendered states is in the PR. Design: docs/POINT_CARD_DESIGN.md §4.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.integration import treatment_source as ts
from tests.integration.test_point_cards import JS, run_js

pytestmark = pytest.mark.integration

A = {"code": "LR3", "rationale": "a"}
B = {"code": "GB20", "rationale": "b"}


def state_of(notes: dict[str, Any] | None) -> dict[str, Any]:
    result = run_js(f"stateOfNotes({json.dumps(notes)})")
    assert isinstance(result, dict)
    return {
        "state": result["state"],
        "stage": result["stage"],
        "generating": result["generating"],
        "codes": [p["code"] for p in result["points"]],
    }


@pytest.mark.parametrize(
    ("notes", "expected"),
    [
        ({"points_status": "GENERATING_STAGE_0"}, ("loading", "summary", True, [])),
        (
            {"points_status": "GENERATING_STAGE_1", "tcm_pattern": "x"},
            ("loading", "diagnosis", True, []),
        ),
        ({"points_status": "GENERATING_STAGE_2A"}, ("loading", "batch-a", True, [])),
        (
            {"points_status": "GENERATING_STAGE_2B", "ai_suggested_points": [A]},
            ("partial", "batch-b", True, ["LR3"]),
        ),
        ({"points_status": "GENERATING"}, ("loading", "diagnosis", True, [])),
        (
            {"points_status": "COMPLETED", "ai_suggested_points": [A, B]},
            ("ready", None, False, ["LR3", "GB20"]),
        ),
        ({"points_status": "COMPLETED", "ai_suggested_points": []}, ("failed", None, False, [])),
        (
            {"points_status": "FAILED", "ai_suggested_points": [A]},
            ("failed", None, False, ["LR3"]),
        ),
        ({"points_status": "CANCELLED"}, ("cancelled", None, False, [])),
        ({"points_status": "", "ai_suggested_points": [A]}, ("ready", None, False, ["LR3"])),
        ({"points_status": ""}, ("idle", None, False, [])),
        (None, ("idle", None, False, [])),
    ],
)
def test_every_status_maps_to_one_designed_state(
    notes: dict[str, Any] | None, expected: tuple[Any, ...]
) -> None:
    state, stage, generating, codes = expected
    assert state_of(notes) == {
        "state": state,
        "stage": stage,
        "generating": generating,
        "codes": codes,
    }


def message(state: str, has_points: bool = False, has_input: bool = True, lang: str = "en") -> str:
    html = run_js(
        f"pointMessageHtml({json.dumps(state)}, {json.dumps(has_points)}, {json.dumps(has_input)})",
        lang,
    )
    assert isinstance(html, str)
    return html


def test_busy_and_ready_states_have_no_message() -> None:
    for state in ("loading", "partial", "ready"):
        assert message(state) == ""


def test_the_empty_state_explains_and_offers_generate() -> None:
    with_input, without = message("idle"), message("idle", has_input=False)
    assert "No AI formula yet" in with_input and 'data-action="generate"' in with_input
    assert "No intake on file" in without and 'data-action="generate"' in without
    assert 'role="alert"' not in with_input


def test_a_failure_is_announced_and_offers_retry() -> None:
    failed = message("failed")
    assert 'role="alert"' in failed and "ps-panel-alert" in failed
    assert 'data-action="rediagnose" data-force="false"' in failed
    assert "Your notes are safe" in failed
    assert "Only the first batch arrived" in message("failed", has_points=True)


def test_a_stalled_run_offers_the_forcing_retry() -> None:
    stalled = message("stalled")
    assert 'data-force="true"' in stalled and 'role="alert"' in stalled


def test_cancelled_says_what_was_kept() -> None:
    assert "Nothing from the cancelled run was saved" in message("cancelled")
    assert "arrived before you cancelled" in message("cancelled", has_points=True)
    assert 'data-action="generate"' in message("cancelled")


def test_hebrew_panels() -> None:
    assert "אין עדיין פורמולת AI" in message("idle", lang="he")
    assert "נסה שוב" in message("failed", lang="he")


def test_skeletons_are_hidden_from_assistive_tech_and_escape_their_label() -> None:
    html = run_js("skeletonCardHtml('<b>x</b>')")
    assert html.startswith('<div class="ps-skeleton" aria-hidden="true">')
    assert "&lt;b&gt;x&lt;/b&gt;" in html


def test_every_label_exists_in_both_languages() -> None:
    for table in ("STATE_TEXT", "POINT_TEXT"):
        keys = run_js(f"[Object.keys({table}.en).sort(), Object.keys({table}.he).sort()]")
        assert keys[0] == keys[1], table
    steps = run_js("POINT_STEPS.filter((s) => !(s in STATE_TEXT.en))")
    assert steps == []


def test_progress_is_the_real_stage_not_a_timer() -> None:
    """Plan 4.2: skeletons, not a fake progress bar."""
    pipeline = (JS / "pipeline.js").read_text(encoding="utf-8")
    assert "style.width" not in pipeline
    assert not re.search(r"\bpct\b", pipeline)
    assert "points-progress" not in ts.source()
    assert "showPointState(" in pipeline and "stateOfNotes(" in pipeline


def test_only_the_state_renderer_writes_the_points_area() -> None:
    """One owner for #ai-points-grid, #ai-points-message and #suggested-points."""
    for path in ts.scripts():
        text = path.read_text(encoding="utf-8")
        for element in ("ai-points-grid", "ai-points-message", "suggested-points"):
            if f"'{element}'" in text:
                assert path.name == "point-states.js", f"{path.name} touches #{element}"
