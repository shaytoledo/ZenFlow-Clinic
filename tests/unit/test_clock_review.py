"""Regressions for the PR #4 (Phase 1.1) review findings."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import httpx
import pytest
from freezegun import freeze_time

import bot.db as dbmod
from zenflow import clock
from zenflow import migrate_timestamps as mig
from zenflow import settings as S

REPO_ROOT = Path(__file__).resolve().parents[2]


def _use_tz(monkeypatch: pytest.MonkeyPatch, zone: str) -> None:
    monkeypatch.setenv("CLINIC_TZ", zone)
    S.reset_settings()


# ── finding 5: date-only strings must not be "normalised" into the previous day ──
def test_date_only_values_are_classified_as_date_and_left_alone(
    db, make_completed_session, monkeypatch
) -> None:
    assert clock.classify("2026-03-01") == "date"
    apt = make_completed_session()
    dbmod.get_db().execute(
        "UPDATE treatment_notes SET recommendations_sent_at='2026-03-01' WHERE appointment_id=?",
        (apt["id"],),
    )
    report = mig.migrate(local_tz="Asia/Jerusalem", dry_run=False)
    assert report.skipped_date_only == 1
    row = (
        dbmod.get_db()
        .execute(
            "SELECT recommendations_sent_at FROM treatment_notes WHERE appointment_id=?",
            (apt["id"],),
        )
        .fetchone()
    )
    assert row[0] == "2026-03-01"


# ── finding 2: templates render the clinic-local date of a UTC instant, not a UTC slice ──
def test_format_clinic_gives_the_clinic_local_date(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_tz(monkeypatch, "Asia/Jerusalem")  # UTC+2 on 1 March (DST starts late March)
    assert clock.format_clinic("2026-03-01T22:30:00Z") == "2026-03-02"  # 00:30 next day in IL
    assert clock.format_clinic("2026-03-01 22:30:00") == "2026-03-02"  # legacy sqlite shape
    assert clock.format_clinic("2026-03-01T22:30:00Z", "%Y-%m-%d %H:%M") == "2026-03-02 00:30"
    assert clock.format_clinic(None) == ""
    assert clock.format_clinic("not a time") == "not a time"  # never crash a template


def test_templates_register_the_clinic_filters() -> None:
    from web.deps import templates

    assert "clinic_date" in templates.env.filters
    assert "clinic_datetime" in templates.env.filters
    text = (REPO_ROOT / "web/templates/session_archive.html").read_text(encoding="utf-8")
    assert "[:10]" not in text, "date slices of UTC instants must go through clinic_date"


# ── finding 6: the rolling calendar window is the clinic day expressed in UTC ──
def test_day_bounds_utc_follow_the_clinic_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_tz(monkeypatch, "Asia/Jerusalem")  # UTC+2 in March
    assert clock.day_bounds_utc(date(2026, 3, 2)) == (
        "2026-03-01T22:00:00Z",
        "2026-03-02T21:59:59Z",
    )
    _use_tz(monkeypatch, "UTC")
    assert clock.day_bounds_utc(date(2026, 3, 2)) == (
        "2026-03-02T00:00:00Z",
        "2026-03-02T23:59:59Z",
    )


def test_rolling_window_starts_at_the_clinic_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    from web.services.cache_service import _rolling_window

    _use_tz(monkeypatch, "Asia/Jerusalem")
    with freeze_time("2026-03-01T22:10:00Z"):  # already 2 March 00:10 in the clinic
        start, end = _rolling_window()
    assert start == "2026-03-01T22:00:00Z"
    assert end.endswith("T21:59:59Z")


# ── findings 3 & 4: every INSERT into a table with created_at stamps it explicitly ──
TABLES_WITH_CREATED_AT = (
    "therapists",
    "appointments",
    "intake_sessions",
    "treatment_notes",
    "notifications",
)


def test_every_insert_sets_created_at_explicitly() -> None:
    pattern = re.compile(
        r"INSERT\s+INTO\s+(" + "|".join(TABLES_WITH_CREATED_AT) + r")\s*\(([^)]*)\)", re.I | re.S
    )
    offenders: list[str] = []
    for path in list((REPO_ROOT / "bot").rglob("*.py")) + list((REPO_ROOT / "web").rglob("*.py")):
        for m in pattern.finditer(path.read_text(encoding="utf-8")):
            if "created_at" not in m.group(2):
                offenders.append(f"{path.relative_to(REPO_ROOT)}: INSERT INTO {m.group(1)}")
    assert offenders == [], offenders


# ── finding 5 (server side): client-supplied recommendations_sent_at is validated + canonical ──
async def test_recommendations_sent_at_is_validated_and_normalised(
    authenticated_client: httpx.AsyncClient, make_appointment
) -> None:
    tid = authenticated_client.headers["X-Test-Therapist-Id"]
    apt = make_appointment(therapist={"id": tid}, apt_date="2026-03-05", apt_time="10:00")
    url = f"/api/treatment-notes/{apt['patient_id']}/2026-03-05/10-00"
    bad = await authenticated_client.post(url, json={"recommendations_sent_at": "next tuesday"})
    assert bad.status_code == 400
    ok = await authenticated_client.post(
        url, json={"recommendations_sent_at": "2026-03-05 12:00:00"}
    )
    assert ok.status_code == 200
    row = (
        dbmod.get_db()
        .execute(
            "SELECT recommendations_sent_at, created_at FROM treatment_notes WHERE appointment_id=?",
            (apt["id"],),
        )
        .fetchone()
    )
    assert row[0] == "2026-03-05T12:00:00Z"
    assert clock.is_canonical(row[1]), row[1]  # finding 4: created_at explicit on upsert
