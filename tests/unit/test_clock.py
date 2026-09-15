"""Phase 1.1 — zenflow.clock: one clock, one canonical string."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from freezegun import freeze_time

from zenflow import clock


def test_now_utc_is_aware_utc(frozen_clock) -> None:
    now = clock.now_utc()
    assert now.tzinfo is not None and now.utcoffset() == timedelta(0)
    assert clock.iso_now() == "2026-03-01T12:00:00Z"


def test_to_iso_is_canonical_and_drops_microseconds() -> None:
    dt = datetime(2026, 3, 1, 14, 5, 9, 123456, tzinfo=ZoneInfo("Asia/Jerusalem"))
    assert clock.to_iso(dt) == "2026-03-01T12:05:09Z"
    assert clock.is_canonical(clock.to_iso(dt))


def test_to_iso_refuses_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="aware"):
        clock.to_iso(datetime(2026, 3, 1, 12, 0, 0))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-03-01T12:00:00Z", "2026-03-01T12:00:00Z"),
        ("2026-03-01T12:00:00+00:00", "2026-03-01T12:00:00Z"),
        ("2026-03-01T15:00:00+03:00", "2026-03-01T12:00:00Z"),
        ("2026-03-01 12:00:00", "2026-03-01T12:00:00Z"),  # SQLite datetime('now')
        ("2026-03-01T12:00:00.654321", "2026-03-01T12:00:00Z"),  # naive → UTC by default
        ("2026-03-01T12:00:00.654321+00:00", "2026-03-01T12:00:00Z"),
    ],
)
def test_parse_and_normalize_every_legacy_shape(raw: str, expected: str) -> None:
    assert clock.normalize(raw) == expected
    parsed = clock.parse_iso(raw).replace(microsecond=0)  # parse keeps µs; normalize drops them
    assert parsed == datetime.fromisoformat(expected.replace("Z", "+00:00"))


def test_naive_values_can_be_read_in_a_given_zone() -> None:
    il = ZoneInfo("Asia/Jerusalem")  # UTC+2 in March
    assert clock.normalize("2026-03-01T14:00:00", naive_tz=il) == "2026-03-01T12:00:00Z"


@pytest.mark.parametrize(
    ("raw", "shape"),
    [
        ("2026-03-01T12:00:00Z", "canonical"),
        ("2026-03-01T12:00:00+02:00", "aware"),
        ("2026-03-01 12:00:00", "sqlite-utc"),
        ("2026-03-01T12:00:00.123", "naive-local"),
        ("", "empty"),
        (None, "empty"),
        ("yesterday", "unparseable"),
    ],
)
def test_classify(raw, shape: str) -> None:
    assert clock.classify(raw) == shape


def test_canonical_strings_sort_as_instants() -> None:
    a = clock.to_iso(datetime(2026, 3, 1, 23, 0, tzinfo=UTC))
    b = clock.to_iso(
        datetime(2026, 3, 2, 1, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))
    )  # 23:00Z same day
    c = clock.to_iso(datetime(2026, 3, 2, 0, 30, tzinfo=UTC))
    assert a == b
    assert a < c  # plain string comparison is a correct instant comparison


def test_sql_now_matches_the_canonical_shape(db) -> None:
    import bot.db as dbmod

    value = dbmod.get_db().execute(f"SELECT {clock.SQL_NOW}").fetchone()[0]
    assert clock.is_canonical(value), value


@pytest.mark.parametrize(
    ("clinic_tz", "expected_today"),
    [
        ("UTC", "2026-03-01"),
        ("Asia/Jerusalem", "2026-03-02"),
        ("America/Los_Angeles", "2026-03-01"),
    ],
)
def test_today_is_the_clinic_date_not_the_utc_or_host_date(
    clinic_tz: str, expected_today: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """23:30 UTC on 1 March: Jerusalem (UTC+2) is already 2 March, Los Angeles is still 1 March.

    Host-local time is irrelevant by construction: nothing calls datetime.now() (ruff DTZ), and
    freezegun's tz_offset also shifts datetime.now(UTC), so the clinic zone is what we vary.
    """
    from zenflow import settings as S

    monkeypatch.setenv("CLINIC_TZ", clinic_tz)
    S.reset_settings()
    try:
        with freeze_time("2026-03-01T23:30:00Z"):
            assert clock.today().isoformat() == expected_today
            assert clock.iso_now() == "2026-03-01T23:30:00Z"  # the instant never shifts
    finally:
        S.reset_settings()


def test_hours_ago_and_ahead(frozen_clock) -> None:
    assert clock.hours_ago(23) == "2026-02-28T13:00:00Z"
    assert clock.hours_ahead(24) == "2026-03-02T12:00:00Z"


def test_clinic_tz_setting_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("CLINIC_TZ", "Mars/Olympus_Mons")
    S.reset_settings()
    try:
        with pytest.raises(S.SettingsError, match="CLINIC_TZ"):
            S.get_settings()
    finally:
        S.reset_settings()


def test_fixed_offset_is_accepted_by_parse() -> None:
    dt = clock.parse_iso("2026-03-01T12:00:00-0700")
    assert dt == datetime(2026, 3, 1, 19, 0, tzinfo=UTC)
