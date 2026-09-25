"""Phase 11.1/11.2 — data-layer coverage for the two thinnest repos.

`availability_repo` (local-mode slots when Google Calendar is not connected) and `intake_repo`
(the AI intake transcript per appointment) were the least-covered repositories (~57% / ~58%). These
DB-backed tests exercise their real SQL: insert/list ordering, the tenant-scoped delete that must not
let one therapist remove another's slot, and the intake history JSON round-trip including the
corrupted-JSON fallback.
"""

from __future__ import annotations

import pytest

from web.repositories import availability_repo, intake_repo

pytestmark = pytest.mark.integration


# ── availability_repo ────────────────────────────────────────────────────────────


def test_insert_returns_a_prefixed_id_and_the_row_is_listed(make_therapist) -> None:
    t = make_therapist()
    slot_id = availability_repo.insert(t["id"], "2026-03-02T09:00:00Z", "2026-03-02T10:00:00Z")
    assert slot_id.startswith("loc_")
    rows = availability_repo.list_for_therapist(t["id"])
    assert len(rows) == 1
    assert rows[0]["id"] == slot_id
    assert rows[0]["start_dt"] == "2026-03-02T09:00:00Z"


def test_list_is_ordered_by_start_and_scoped_to_the_therapist(make_therapist) -> None:
    a, b = make_therapist(therapist_id="t_a"), make_therapist(therapist_id="t_b")
    availability_repo.insert(a["id"], "2026-03-02T15:00:00Z", "2026-03-02T16:00:00Z")
    availability_repo.insert(a["id"], "2026-03-02T09:00:00Z", "2026-03-02T10:00:00Z")
    availability_repo.insert(b["id"], "2026-03-02T08:00:00Z", "2026-03-02T09:00:00Z")

    rows = availability_repo.list_for_therapist(a["id"])
    assert [r["start_dt"] for r in rows] == ["2026-03-02T09:00:00Z", "2026-03-02T15:00:00Z"]
    assert all(r["therapist_id"] == "t_a" for r in rows), "b's slot must not appear in a's list"


def test_delete_scoped_to_owner_refuses_another_therapists_slot(make_therapist) -> None:
    a, b = make_therapist(therapist_id="t_a"), make_therapist(therapist_id="t_b")
    slot_id = availability_repo.insert(a["id"], "2026-03-02T09:00:00Z", "2026-03-02T10:00:00Z")

    # b tries to delete a's slot — the therapist_id scope must refuse it.
    assert availability_repo.delete(slot_id, therapist_id=b["id"]) == 0
    assert len(availability_repo.list_for_therapist(a["id"])) == 1, "the slot is untouched"

    # the owner can delete it.
    assert availability_repo.delete(slot_id, therapist_id=a["id"]) == 1
    assert availability_repo.list_for_therapist(a["id"]) == []


def test_unscoped_delete_removes_any_slot(make_therapist) -> None:
    t = make_therapist()
    slot_id = availability_repo.insert(t["id"], "2026-03-02T09:00:00Z", "2026-03-02T10:00:00Z")
    assert availability_repo.delete(slot_id) == 1
    assert availability_repo.delete(slot_id) == 0, "deleting a gone slot reports zero rows"


def test_delete_of_an_unknown_slot_reports_zero() -> None:
    assert availability_repo.delete("loc_doesnotexist") == 0


# ── intake_repo ──────────────────────────────────────────────────────────────────


def test_get_for_a_missing_appointment_is_none() -> None:
    assert intake_repo.get_for_appointment(999_999) is None


def test_insert_then_get_round_trips_the_history(make_appointment) -> None:
    apt = make_appointment()
    history = [
        {"role": "assistant", "text": "Where does it hurt?"},
        {"role": "user", "text": "לחץ בגב התחתון"},  # Hebrew, ensure_ascii=False round-trip
    ]
    intake_repo.insert(apt["id"], apt["patient_id"], apt["therapist_id"], history)

    got = intake_repo.get_for_appointment(apt["id"])
    assert got is not None
    assert got["appointment_id"] == apt["id"]
    assert got["history"] == history
    assert "לחץ בגב התחתון" in got["history_json"], "non-ASCII stored as UTF-8, not escaped"


def test_empty_history_round_trips_as_empty_list(make_appointment) -> None:
    apt = make_appointment()
    intake_repo.insert(apt["id"], apt["patient_id"], apt["therapist_id"], [])
    got = intake_repo.get_for_appointment(apt["id"])
    assert got is not None and got["history"] == []


def test_null_history_json_reads_as_empty_list(make_appointment) -> None:
    # A row with no history_json at all (NULL) reads back as an empty history, not None/KeyError.
    from bot.db import get_db

    apt = make_appointment()
    get_db().execute(
        "INSERT INTO intake_sessions (appointment_id, patient_id, therapist_id, history_json, "
        "created_at) VALUES (?, ?, ?, NULL, datetime('now'))",
        (apt["id"], apt["patient_id"], apt["therapist_id"]),
    )
    got = intake_repo.get_for_appointment(apt["id"])
    assert got is not None and got["history"] == []


def test_corrupted_history_json_falls_back_to_empty_list(make_appointment) -> None:
    # A malformed history_json in the row (e.g. a truncated write) must not raise on read.
    from bot.db import get_db

    apt = make_appointment()
    get_db().execute(
        "INSERT INTO intake_sessions (appointment_id, patient_id, therapist_id, history_json, "
        "created_at) VALUES (?, ?, ?, ?, datetime('now'))",
        (apt["id"], apt["patient_id"], apt["therapist_id"], "{not valid json"),
    )
    got = intake_repo.get_for_appointment(apt["id"])
    assert got is not None and got["history"] == [], "unparseable history degrades to []"
