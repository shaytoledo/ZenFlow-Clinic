"""Phase 11.1 (UNIT) — appointment aggregation and the "today" filter.

`appointment_service.aggregate_patients` turns a flat list of appointment rows into the per-patient
summary the /patients dashboard renders: session counts, active/intake tallies, the most-recent
appointment, and the last five visits per patient. It is pure list-in/list-out logic and was barely
unit-covered (the service sat at ~18%), so its counting, recency and truncation rules are pinned here.
`list_today` is the small pure filter on top of the (stubbed) repo read.
"""

from __future__ import annotations

from datetime import date

import pytest

from web.services import appointment_service as svc
from zenflow import clock


def _apt(pid, name="", d="2026-03-01", t="09:00", status="active", intake=None, summary=""):
    return {
        "patient_id": pid,
        "patient_name": name,
        "date": d,
        "time": t,
        "status": status,
        "intake_history": intake,
        "summary": summary,
    }


def test_empty_input_gives_empty_output() -> None:
    assert svc.aggregate_patients([]) == []


def test_rows_without_a_patient_id_are_skipped() -> None:
    out = svc.aggregate_patients([_apt(None), _apt(0), {"date": "2026-03-01"}])
    assert out == []


def test_counts_sessions_active_and_intake() -> None:
    rows = [
        _apt(1, "Maya", d="2026-03-01", status="active", intake=[{"q": "a"}]),
        _apt(1, "Maya", d="2026-03-02", status="cancelled"),
        _apt(1, "Maya", d="2026-03-03", status="active"),
    ]
    (p,) = svc.aggregate_patients(rows)
    assert p["id"] == 1 and p["name"] == "Maya"
    assert p["sessions"] == 3
    assert p["active_count"] == 2  # the cancelled one does not count
    assert p["intake_count"] == 1  # only the row that carried intake_history


@pytest.mark.parametrize(
    "row",
    [
        {"patient_id": 7, "patient_name": ""},
        {"patient_id": 7, "patient_name": None},
        {"patient_id": 7},
    ],
)
def test_name_falls_back_to_patient_id_label(row) -> None:
    # The repo always selects patient_name (NOT NULL, but can be ""), so the key is present — the
    # label must fire for an empty/None/absent name, not only a missing key.
    (p,) = svc.aggregate_patients([row])
    assert p["name"] == "Patient 7"


def test_last_appointment_is_the_latest_date_with_its_time() -> None:
    rows = [
        _apt(1, "Maya", d="2026-03-01", t="09:00"),
        _apt(1, "Maya", d="2026-03-05", t="14:30"),
        _apt(1, "Maya", d="2026-03-03", t="11:00"),
    ]
    (p,) = svc.aggregate_patients(rows)
    assert p["last_appointment"] == "2026-03-05"
    assert p["last_time"] == "14:30"


def test_recent_is_date_sorted_and_capped_to_five() -> None:
    rows = [_apt(1, "Maya", d=f"2026-03-{day:02d}") for day in range(1, 9)]  # 8 visits
    (p,) = svc.aggregate_patients(rows)
    dates = [r["date"] for r in p["recent"]]
    assert dates == sorted(dates), "recent visits are chronologically ordered"
    assert dates == ["2026-03-04", "2026-03-05", "2026-03-06", "2026-03-07", "2026-03-08"]
    assert len(p["recent"]) == 5, "only the five most recent are kept"


def test_recent_summary_is_truncated() -> None:
    (p,) = svc.aggregate_patients([_apt(1, "Maya", summary="x" * 500)])
    assert len(p["recent"][0]["summary"]) == 120


def test_a_none_summary_becomes_empty_string() -> None:
    (p,) = svc.aggregate_patients([_apt(1, "Maya", summary=None)])
    assert p["recent"][0]["summary"] == ""


def test_patients_are_sorted_by_most_recent_appointment_desc() -> None:
    rows = [
        _apt(1, "Early", d="2026-01-10"),
        _apt(2, "Late", d="2026-06-20"),
        _apt(3, "Middle", d="2026-03-15"),
    ]
    out = svc.aggregate_patients(rows)
    assert [p["name"] for p in out] == ["Late", "Middle", "Early"]


def test_list_today_keeps_only_active_rows_dated_today(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _apt(1, d="2026-03-01", status="active"),
        _apt(2, d="2026-03-01", status="cancelled"),
        _apt(3, d="2026-02-28", status="active"),  # yesterday
    ]
    monkeypatch.setattr(svc, "list_all", lambda: rows)
    monkeypatch.setattr(clock, "today", lambda: date(2026, 3, 1))
    today = svc.list_today()
    assert [a["patient_id"] for a in today] == [1], "only the active, today-dated row survives"
