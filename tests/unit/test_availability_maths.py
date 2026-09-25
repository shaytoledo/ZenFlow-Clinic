"""Phase 11.1 (UNIT) — the pure booking-window maths in availability.py.

The Google/SQLite reads are integration territory; the arithmetic underneath is pure and
correctness-critical, so it is pinned here in isolation. The load-bearing rule is that a patient can
never book **today or a day in the past** — `_week_range` always starts at tomorrow — and that a week
is the Monday→Sunday block for the requested offset. `_slot_dt` / `_hhmm_min` convert an "HH:MM" slot
to a wall-clock datetime and to minutes-since-midnight for slot comparisons.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

import bot.patient_bot.services.availability as avail

# Anchor dates (2026-03-02 is a Monday, ..-04 Wednesday, ..-08 Sunday).
_MON = date(2026, 3, 2)
_WED = date(2026, 3, 4)
_SUN = date(2026, 3, 8)


def _range(monkeypatch: pytest.MonkeyPatch, today: date, offset: int) -> tuple[date, date]:
    monkeypatch.setattr(avail, "clinic_today", lambda: today)
    return avail._week_range(offset)


def test_current_week_starts_tomorrow_not_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # Wednesday: this week's bookable window is Thu..Sun, never Wed (today) or earlier.
    start, end = _range(monkeypatch, _WED, 0)
    assert start == date(2026, 3, 5)  # Thursday (today + 1)
    assert end == date(2026, 3, 8)  # Sunday


def test_on_a_monday_today_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    start, end = _range(monkeypatch, _MON, 0)
    assert start == date(2026, 3, 3)  # Tuesday, not the Monday we're on
    assert end == date(2026, 3, 8)


def test_a_future_week_is_the_full_monday_to_sunday_block(monkeypatch: pytest.MonkeyPatch) -> None:
    start, end = _range(monkeypatch, _WED, 1)
    assert start == date(2026, 3, 9)  # Monday of next week
    assert end == date(2026, 3, 15)  # Sunday of next week
    assert start.weekday() == 0 and end.weekday() == 6
    assert (end - start).days == 6


def test_last_day_of_the_week_leaves_no_bookable_days_this_week(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On Sunday, tomorrow is already next week's Monday, so offset 0 yields an empty (start>end) range.
    start, end = _range(monkeypatch, _SUN, 0)
    assert start > end, "no future days remain in the current week on its last day"


def test_a_past_week_yields_an_empty_range(monkeypatch: pytest.MonkeyPatch) -> None:
    start, end = _range(monkeypatch, _WED, -1)
    assert start > end, "a week already in the past has no bookable days"


@pytest.mark.parametrize("weekday_today", [_MON, date(2026, 3, 3), _WED, date(2026, 3, 6), _SUN])
@pytest.mark.parametrize("offset", [0, 1, 2, 4])
def test_the_window_never_includes_today_or_the_past(
    monkeypatch: pytest.MonkeyPatch, weekday_today: date, offset: int
) -> None:
    start, end = _range(monkeypatch, weekday_today, offset)
    if start <= end:  # a non-empty window
        assert start > weekday_today, "the earliest bookable day is always after today"
        assert end.weekday() == 6, "a week window always ends on Sunday"


def test_slot_dt_is_the_wall_clock_datetime() -> None:
    assert avail._slot_dt(date(2026, 3, 4), "09:30") == datetime(2026, 3, 4, 9, 30)
    assert avail._slot_dt(date(2026, 3, 4), "00:00") == datetime(2026, 3, 4, 0, 0)
    assert avail._slot_dt(date(2026, 3, 4), "23:00") == datetime(2026, 3, 4, 23, 0)


@pytest.mark.parametrize(
    "hhmm,minutes",
    [("00:00", 0), ("01:05", 65), ("09:30", 570), ("12:00", 720), ("23:59", 1439)],
)
def test_hhmm_min_counts_minutes_since_midnight(hhmm: str, minutes: int) -> None:
    assert avail._hhmm_min(hhmm) == minutes


def test_slot_dt_and_hhmm_min_agree() -> None:
    day = date(2026, 3, 4)
    for hhmm in ("00:00", "08:15", "13:45", "23:00"):
        dt = avail._slot_dt(day, hhmm)
        assert avail._hhmm_min(hhmm) == dt.hour * 60 + dt.minute
