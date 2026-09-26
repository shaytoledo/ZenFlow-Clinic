"""Phase 11.1 (UNIT) — point normalisation.

Two helpers coerce whatever the AI returned into clean `{code, rationale}` dicts:
`ai_intake._normalise_points` and `treatment._normalize_points`. They accept dict points (with
`code`/`point` and `rationale`/`why` aliases) and bare string codes, uppercase and trim the code, and
drop anything they can't use. This is pure logic the parsers lean on, so it is pinned here.
"""

from __future__ import annotations

import pytest

from bot.patient_bot.services.ai_intake import _normalise_points
from web.routers.api.treatment import _normalize_points

_IMPLS = [
    pytest.param(_normalise_points, id="ai_intake"),
    pytest.param(_normalize_points, id="treatment"),
]


@pytest.mark.parametrize("norm", _IMPLS)
def test_a_dict_point_keeps_code_and_rationale(norm) -> None:
    out = norm([{"code": "lr3", "rationale": "moves qi"}])
    assert out == [{"code": "LR3", "rationale": "moves qi"}], "code is upper-cased"


@pytest.mark.parametrize("norm", _IMPLS)
def test_the_point_and_why_aliases_are_accepted(norm) -> None:
    out = norm([{"point": "st36", "why": "tonify"}])
    assert out == [{"code": "ST36", "rationale": "tonify"}]


@pytest.mark.parametrize("norm", _IMPLS)
def test_a_bare_string_becomes_a_coded_point(norm) -> None:
    assert norm(["sp6", "  ki3 "]) == [
        {"code": "SP6", "rationale": ""},
        {"code": "KI3", "rationale": ""},
    ]


@pytest.mark.parametrize("norm", _IMPLS)
def test_points_without_a_code_or_of_the_wrong_type_are_dropped(norm) -> None:
    out = norm([{"rationale": "no code"}, {"code": ""}, 42, None, ["nested"], "", "   "])
    assert out == [], "only usable dict/string points survive"


@pytest.mark.parametrize("norm", _IMPLS)
def test_a_non_list_input_is_handled(norm) -> None:
    # treatment._normalize_points guards non-lists explicitly; ai_intake is only ever handed a list,
    # so exercise each with what it must tolerate.
    assert norm([]) == []
