"""Plan 7.2 — the one-time move from "a patient IS a Telegram id" to internal patient ids.

Before 7.2, `appointments.patient_id` held a Telegram user id, or a negative millisecond stamp
for a manual booking. The migration gives every such id a `patients` row (keeping the old value
in `legacy_id` for one release), links the Telegram ones in `patient_channels`, and rewrites
every table that stored the old id. It runs once, atomically, after a backup.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import bot.db as dbmod

pytestmark = pytest.mark.integration

TG = 555_000_111  # a Telegram user id
MANUAL = -1_700_000_000_001  # a manual booking's negative id
ORPHAN = 555_000_999  # treatment notes whose appointment row is gone
MIGRATION = "0001_patient_identity"


def _q(sql: str, *args: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in dbmod.get_db().execute(sql, args)]


def _legacy_database(therapist_id: str) -> dict[str, int]:
    """Rows shaped the way a pre-7.2 database holds them."""
    conn = dbmod.get_db()
    conn.execute("DELETE FROM schema_migrations WHERE name=?", (MIGRATION,))

    def apt(pid: int, name: str, day: str, source: str, **extra: str) -> int:
        cur = conn.execute(
            """INSERT INTO appointments (patient_id, patient_name, therapist_id, date, time,
                   status, source, patient_phone, patient_email, created_at)
               VALUES (?, ?, ?, ?, '10:00', 'active', ?, ?, ?, ?)""",
            (
                pid,
                name,
                therapist_id,
                day,
                source,
                extra.get("phone", ""),
                extra.get("email", ""),
                f"{day} 08:00:00",
            ),
        )
        return int(cur.lastrowid or 0)

    ids = {
        "tg_old": apt(TG, "Dana L", "2026-01-05", "telegram"),
        "tg_new": apt(TG, "Dana Levi", "2026-02-05", "telegram"),
        # a manual booking attached to the Telegram patient (existing_patient_id)
        "tg_manual": apt(TG, "Dana Levi", "2026-02-20", "manual", phone="050-1111111"),
        "manual": apt(
            MANUAL,
            "Noa Manual",
            "2026-02-10",
            "manual",
            phone="052-2222222",
            email="noa@example.com",
        ),
    }
    for key in ("tg_new", "manual"):
        pid = TG if key == "tg_new" else MANUAL
        conn.execute(
            "INSERT INTO treatment_notes (appointment_id, patient_id, tcm_pattern) VALUES (?, ?, 'x')",
            (ids[key], pid),
        )
        conn.execute(
            "INSERT INTO intake_sessions (appointment_id, patient_id, therapist_id, history_json)"
            " VALUES (?, ?, ?, '[]')",
            (ids[key], pid, therapist_id),
        )
        conn.execute(
            """INSERT INTO notifications (therapist_id, kind, title, appointment_id, patient_id)
               VALUES (?, 'send_failed', 't', ?, ?)""",
            (therapist_id, ids[key], pid),
        )
        conn.execute(
            """INSERT INTO message_log (ts, channel, patient_id, therapist_id, appointment_id,
                   kind, status) VALUES ('2026-02-06T09:00:00Z', 'telegram', ?, ?, ?, 'followup', 'sent')""",
            (pid, therapist_id, ids[key]),
        )
        conn.execute(
            """INSERT INTO followups (appointment_id, patient_id, therapist_id, channel, status,
                   created_at, updated_at)
               VALUES (?, ?, ?, 'telegram', 'sent', '2026-02-06T09:00:00Z', '2026-02-06T09:00:00Z')""",
            (ids[key], pid, therapist_id),
        )
    # notes whose appointment was hard-deleted long ago still name a patient
    conn.execute(
        "INSERT INTO treatment_notes (appointment_id, patient_id) VALUES (99999, ?)", (ORPHAN,)
    )
    return ids


@pytest.fixture
def legacy(make_therapist):
    t = make_therapist(therapist_id="t1")
    return t, _legacy_database(t["id"])


def _run() -> None:
    dbmod.init_db()  # the migration runs at start-up, like every other schema step


def _patient(legacy_id: int) -> dict[str, Any]:
    (row,) = _q("SELECT * FROM patients WHERE legacy_id=?", legacy_id)
    return row


def test_every_old_id_becomes_a_patient(legacy) -> None:
    _run()
    tg, manual, orphan = _patient(TG), _patient(MANUAL), _patient(ORPHAN)
    assert tg["full_name"] == "Dana Levi", "the newest booking's name"
    assert tg["phone"] == "050-1111111"
    assert (manual["full_name"], manual["phone"], manual["email"]) == (
        "Noa Manual",
        "052-2222222",
        "noa@example.com",
    )
    assert orphan["full_name"] == ""
    assert len({tg["id"], manual["id"], orphan["id"]}) == 3
    assert all(p["id"] > 0 for p in (tg, manual, orphan))


def test_only_telegram_ids_get_a_channel(legacy) -> None:
    _run()
    channels = _q("SELECT patient_id, channel, external_id, is_primary FROM patient_channels")
    assert {
        (c["patient_id"], c["channel"], c["external_id"], c["is_primary"]) for c in channels
    } == {
        (_patient(TG)["id"], "telegram", str(TG), 1),
        (_patient(ORPHAN)["id"], "telegram", str(ORPHAN), 1),
    }


def test_every_table_is_rewritten_to_internal_ids(legacy) -> None:
    _, ids = legacy
    _run()
    tg, manual = _patient(TG)["id"], _patient(MANUAL)["id"]
    by_apt = {r["id"]: r["patient_id"] for r in _q("SELECT id, patient_id FROM appointments")}
    assert by_apt == {
        ids["tg_old"]: tg,
        ids["tg_new"]: tg,
        ids["tg_manual"]: tg,
        ids["manual"]: manual,
    }
    for table in (
        "treatment_notes",
        "intake_sessions",
        "notifications",
        "message_log",
        "followups",
    ):
        rows = _q(
            f"SELECT appointment_id, patient_id FROM {table} WHERE appointment_id != 99999"
        )  # noqa: S608
        assert {(r["appointment_id"], r["patient_id"]) for r in rows} == {
            (ids["tg_new"], tg),
            (ids["manual"], manual),
        }, table
    legacy_values = {TG, MANUAL, ORPHAN}
    for table in (
        "appointments",
        "treatment_notes",
        "intake_sessions",
        "notifications",
        "message_log",
        "followups",
    ):
        left = {r["patient_id"] for r in _q(f"SELECT patient_id FROM {table}")}  # noqa: S608
        assert not (left & legacy_values), table
        assert all(p is None or p > 0 for p in left), f"no negative id left in {table}"


def test_every_appointment_points_at_a_patient(legacy) -> None:
    _run()
    assert _q("""SELECT a.id FROM appointments a LEFT JOIN patients p ON p.id = a.patient_id
           WHERE p.id IS NULL""") == []


def test_it_runs_once(legacy) -> None:
    _run()
    before = _q("SELECT id, patient_id FROM appointments ORDER BY id")
    patients = _q("SELECT * FROM patients ORDER BY id")
    _run()
    _run()
    assert _q("SELECT id, patient_id FROM appointments ORDER BY id") == before
    assert _q("SELECT * FROM patients ORDER BY id") == patients
    (marker,) = _q("SELECT * FROM schema_migrations WHERE name=?", MIGRATION)
    assert marker["applied_at"].endswith("Z")


def test_a_backup_is_taken_first(legacy, db: Path) -> None:
    _run()
    backups = list(db.parent.glob(f"{db.name}.pre-patient-identity-*"))
    assert len(backups) == 1
    import sqlite3

    with sqlite3.connect(backups[0]) as old:
        assert (
            old.execute("SELECT COUNT(*) FROM appointments WHERE patient_id=?", (TG,)).fetchone()[0]
            == 3
        ), "the backup holds the pre-migration ids"


def test_an_empty_database_needs_no_backup(db: Path) -> None:
    dbmod.get_db().execute("DELETE FROM schema_migrations")
    _run()
    assert list(db.parent.glob(f"{db.name}.pre-patient-identity-*")) == []
    assert {"name": MIGRATION} in _q("SELECT name FROM schema_migrations")


def test_a_failure_changes_nothing_and_stops_the_start(legacy) -> None:
    conn = dbmod.get_db()
    conn.execute("""CREATE TEMP TRIGGER boom BEFORE UPDATE ON notifications
           BEGIN SELECT RAISE(ABORT, 'disk on fire'); END""")
    # Refusing to start is safer than running with old and new ids mixed.
    with pytest.raises(RuntimeError, match="patient identity migration failed"):
        _run()
    assert _q("SELECT COUNT(*) AS n FROM patients") == [{"n": 0}]
    assert _q("SELECT COUNT(*) AS n FROM appointments WHERE patient_id IN (?, ?)", TG, MANUAL) == [
        {"n": 4}
    ]
    assert _q("SELECT * FROM schema_migrations WHERE name=?", MIGRATION) == []
    conn.execute("DROP TRIGGER boom")
    _run()
    assert len(_q("SELECT * FROM patients")) == 3


# ── one release of compatibility for the old ids ──
async def test_an_old_treatment_link_redirects(legacy, login_as) -> None:
    therapist, _ = legacy
    _run()
    client = await login_as_existing(login_as, therapist)
    new_id = _patient(TG)["id"]

    resp = await client.get(f"/treatment/{TG}/2026-02-05/10-00")

    assert resp.status_code == 308
    assert resp.headers["location"] == f"/treatment/{new_id}/2026-02-05/10-00"
    page = await client.get(resp.headers["location"])
    assert page.status_code == 200


async def test_an_old_profile_link_redirects(legacy, login_as) -> None:
    therapist, _ = legacy
    _run()
    client = await login_as_existing(login_as, therapist)
    resp = await client.get(f"/patients/{MANUAL}")
    assert resp.status_code == 308
    assert resp.headers["location"] == f"/patients/{_patient(MANUAL)['id']}"


async def test_the_api_still_answers_for_an_old_id(legacy, login_as) -> None:
    therapist, ids = legacy
    _run()
    client = await login_as_existing(login_as, therapist)
    old = await client.get(f"/api/treatment-notes/{TG}/2026-02-05/10-00")
    new = await client.get(f"/api/treatment-notes/{_patient(TG)['id']}/2026-02-05/10-00")
    assert old.status_code == new.status_code == 200
    assert old.json()["appointment_id"] == new.json()["appointment_id"] == ids["tg_new"]


async def test_old_ids_stay_tenant_scoped(legacy, login_as, make_therapist) -> None:
    _run()
    other = make_therapist(therapist_id="t2", email="other@example.com", password="pw-Test-123")
    client = await login_as(other)
    page = await client.get(f"/treatment/{TG}/2026-02-05/10-00")
    assert page.status_code == 307 and page.headers["location"] == "/patients"
    profile = await client.get(f"/patients/{TG}")
    assert profile.status_code == 307 and profile.headers["location"] == "/patients"
    assert (await client.get(f"/api/treatment-notes/{TG}/2026-02-05/10-00")).status_code == 404


async def login_as_existing(login_as: Any, therapist: dict[str, Any]) -> Any:
    """The legacy fixture's therapist has no password — give it one to sign in."""
    from web.deps import _hash_password

    dbmod.get_db().execute(
        "UPDATE therapists SET email=?, password_hash=?, active=1 WHERE id=?",
        ("legacy@example.com", _hash_password("pw-Test-123"), therapist["id"]),
    )
    return await login_as({"email": "legacy@example.com", "password": "pw-Test-123"})
