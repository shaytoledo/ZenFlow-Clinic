"""Phase 12.2.3 — the Alembic baseline builds the whole schema on Postgres.

CI's `postgres` job starts a Postgres service, runs `python -m zenflow.migrate upgrade` against it
and sets ZF_TEST_POSTGRES_URL; locally these skip unless you point that variable at a database.
They check what SQLite-only tests cannot: the tables, the view, the plpgsql audit guard, the
partial unique index and the legacy `datetime('now')` default all work on Postgres.
"""

from __future__ import annotations

import os
import re

import pytest
import sqlalchemy as sa

URL = os.environ.get("ZF_TEST_POSTGRES_URL", "")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not URL, reason="set ZF_TEST_POSTGRES_URL (CI's postgres job does)"),
]


@pytest.fixture(scope="module")
def engine():
    eng = sa.create_engine(URL)
    yield eng
    eng.dispose()


def test_every_table_and_the_view_exist(engine) -> None:
    from zenflow import schema

    inspector = sa.inspect(engine)
    assert set(schema.metadata.tables) <= set(inspector.get_table_names())
    assert "patient_contacts" in inspector.get_view_names()


def test_the_database_is_at_the_head_revision(engine) -> None:
    from zenflow.migrate import head

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == head()


def test_the_audit_log_is_append_only_on_postgres(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO audit_log (ts, actor_type, action, entity_type, entity_id) "
                "VALUES ('2026-10-02T00:00:00Z', 'system', 'pg.check', 'x', '1')"
            )
        )
    for statement in ("UPDATE audit_log SET action='tampered'", "DELETE FROM audit_log"):
        with pytest.raises(sa.exc.DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(sa.text(statement))


def test_one_active_appointment_per_slot_on_postgres(engine) -> None:
    insert = sa.text(
        "INSERT INTO appointments (patient_id, patient_name, therapist_id, date, time, status) "
        "VALUES (:p, 'x', 'pg-t1', '2026-10-05', '10:00', :s)"
    )
    with engine.begin() as conn:
        conn.execute(insert, {"p": 1, "s": "active"})
        conn.execute(insert, {"p": 2, "s": "cancelled"})  # a cancelled one may share the slot
    with pytest.raises(sa.exc.IntegrityError), engine.begin() as conn:
        conn.execute(insert, {"p": 3, "s": "active"})


def test_the_legacy_timestamp_default_works_on_postgres(engine) -> None:
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO therapists (id, name) VALUES ('pg-t9', 'Dr PG')"))
        created = conn.execute(
            sa.text("SELECT created_at FROM therapists WHERE id='pg-t9'")
        ).scalar()
    assert re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d", created), created


def test_schema_and_migrations_agree_on_postgres() -> None:
    from alembic import command

    from zenflow.migrate import _config

    command.check(_config(URL))
