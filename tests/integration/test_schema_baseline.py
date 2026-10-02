"""Phase 12.2.3 — the portable schema (`zenflow/schema.py`) IS today's schema.

The application has always built its tables with raw `CREATE TABLE` / `ALTER TABLE` statements on
SQLite. Alembic's baseline migration is generated from `zenflow.schema.metadata`, so the metadata
has to describe exactly that schema: every table, column (name, type, order, nullability, default),
CHECK and UNIQUE constraint, foreign key, index (partial ones included), trigger and view.

This builds both on fresh SQLite files and compares their fingerprints. One documented normalisation:
SQLite lets a non-INTEGER PRIMARY KEY hold NULL unless NOT NULL is spelled out; the metadata says
NOT NULL (as Postgres requires), so `notnull` is not compared for primary-key columns.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

import bot.db as dbmod

pytestmark = pytest.mark.integration

INTERNAL = {"sqlite_sequence", "alembic_version"}


def _squash(sql: str) -> str:
    return re.sub(r"\s+", "", sql or "").lower()


def _checks(create_sql: str) -> set[str]:
    """Every CHECK (...) expression in a CREATE TABLE, balanced-paren aware."""
    out, i = set(), 0
    while (i := create_sql.upper().find("CHECK", i)) >= 0:
        start = create_sql.index("(", i)
        depth, j = 0, start
        while True:
            depth += {"(": 1, ")": -1}.get(create_sql[j], 0)
            if depth == 0:
                break
            j += 1
        out.add(_squash(create_sql[start + 1 : j]))
        i = j
    return out


def fingerprint(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    master = conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master").fetchall()
    tables = sorted(n for t, n, _tb, _s in master if t == "table" and n not in INTERNAL)
    fp: dict[str, Any] = {"tables": tables, "columns": {}, "checks": {}, "fks": {}, "indexes": {}}
    for table in tables:
        fp["columns"][table] = [
            (name, ctype.upper(), None if pk else bool(notnull), default, pk)
            for _cid, name, ctype, notnull, default, pk in conn.execute(
                f"PRAGMA table_info({table})"
            )
        ]
        create_sql = next(s for t, n, _tb, s in master if t == "table" and n == table)
        fp["checks"][table] = sorted(_checks(create_sql))
        fp["fks"][table] = sorted(
            (row[2], row[3], row[4], row[6])  # parent table, from, to, on_delete
            for row in conn.execute(f"PRAGMA foreign_key_list({table})")
        )
        idx = []
        for _seq, name, unique, origin, partial in conn.execute(f"PRAGMA index_list({table})"):
            if origin == "pk":
                continue  # covered by table_info's pk column
            cols = tuple(r[2] for r in conn.execute(f"PRAGMA index_info({name})"))
            label = name if origin == "c" else "<constraint>"
            where = ""
            if partial:
                sql = next(s for t, n, _tb, s in master if t == "index" and n == name)
                where = _squash(sql.split("WHERE", 1)[1])
            idx.append((label, bool(unique), cols, where))
        fp["indexes"][table] = sorted(idx)
    fp["triggers"] = sorted((n, _squash(s)) for t, n, _tb, s in master if t == "trigger")
    fp["views"] = sorted((n, _squash(s)) for t, n, _tb, s in master if t == "view")
    conn.close()
    return fp


@pytest.fixture
def legacy_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The schema exactly as the application has always built it (init_db on an empty file)."""
    path = tmp_path / "legacy.db"
    monkeypatch.setenv("ZENFLOW_DB_PATH", str(path))
    dbmod.close_db()
    dbmod.init_db()
    dbmod.close_db()
    return path


@pytest.fixture
def metadata_schema(tmp_path: Path) -> Path:
    from zenflow import schema

    path = tmp_path / "metadata.db"
    engine = sa.create_engine(f"sqlite:///{path}")
    schema.create_all(engine)
    engine.dispose()
    return path


def test_the_metadata_has_every_table_index_trigger_and_view(legacy_schema, metadata_schema):
    legacy, meta = fingerprint(legacy_schema), fingerprint(metadata_schema)
    assert meta["tables"] == legacy["tables"]
    assert meta["triggers"] == legacy["triggers"]
    assert meta["views"] == legacy["views"]


@pytest.mark.parametrize("part", ["columns", "checks", "fks", "indexes"])
def test_every_table_matches_column_for_column(part, legacy_schema, metadata_schema):
    legacy, meta = fingerprint(legacy_schema)[part], fingerprint(metadata_schema)[part]
    diffs = {t: {"legacy": legacy[t], "metadata": meta[t]} for t in legacy if legacy[t] != meta[t]}
    assert diffs == {}


def test_the_fingerprint_notices_a_difference(legacy_schema, tmp_path):
    """The comparison is not vacuous: one changed default is caught."""
    changed = tmp_path / "changed.db"
    changed.write_bytes(legacy_schema.read_bytes())
    conn = sqlite3.connect(changed)
    conn.execute("ALTER TABLE patients ADD COLUMN surprise TEXT DEFAULT 'x'")
    conn.commit()
    conn.close()
    assert fingerprint(changed)["columns"] != fingerprint(legacy_schema)["columns"]


# ── Alembic ──
def test_alembic_builds_exactly_the_legacy_schema(legacy_schema, tmp_path):
    from zenflow.migrate import upgrade

    built = tmp_path / "alembic.db"
    upgrade(f"sqlite:///{built}")
    assert fingerprint(built) == fingerprint(legacy_schema)


def test_an_existing_database_is_stamped_not_rebuilt(legacy_schema, monkeypatch):
    """init_db() adopts a database Alembic has never seen: stamped 0001, data untouched."""
    from zenflow import migrate

    conn = sqlite3.connect(legacy_schema)
    assert migrate.current(conn) == migrate.head()
    conn.execute("DELETE FROM alembic_version")  # as if built before Alembic existed
    conn.execute(
        "INSERT INTO patients (full_name, created_at, updated_at) VALUES ('Kept', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("ZENFLOW_DB_PATH", str(legacy_schema))
    dbmod.close_db()
    dbmod.init_db()
    dbmod.close_db()
    conn = sqlite3.connect(legacy_schema)
    assert migrate.current(conn) == migrate.head()
    assert conn.execute("SELECT full_name FROM patients").fetchall() == [("Kept",)]


def test_the_schema_module_and_the_migrations_agree(tmp_path):
    """`zenflow.migrate check`: a change to zenflow/schema.py without a revision fails here."""
    from alembic import command

    from zenflow.migrate import _config, upgrade

    url = f"sqlite:///{tmp_path / 'check.db'}"
    upgrade(url)
    command.check(_config(url))  # raises when autogenerate would produce operations


def test_the_legacy_alter_list_is_frozen():
    """Schema changes are Alembic revisions now; the old ALTER list never grows (ADR-45)."""
    source = (Path(dbmod.__file__)).read_text(encoding="utf-8")
    assert source.count('"ALTER TABLE ') == 17


def _diffs(url: str, metadata: sa.MetaData) -> list:
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from zenflow.migrate import compare_default

    engine = sa.create_engine(url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_server_default": compare_default})
            return compare_metadata(ctx, metadata)
    finally:
        engine.dispose()


def test_the_check_notices_a_real_change(tmp_path):
    """The default comparison ignores quoting noise, not changes: a changed default and a new
    column are both caught (on a copy of the metadata — the real one is never touched)."""
    from zenflow import schema
    from zenflow.migrate import upgrade

    url = f"sqlite:///{tmp_path / 'drift.db'}"
    upgrade(url)
    assert _diffs(url, schema.metadata) == []

    changed = sa.MetaData()
    for table in schema.metadata.sorted_tables:
        table.to_metadata(changed)
    changed.tables["therapists"].c.language.server_default = sa.DefaultClause("he")
    assert any("modify_default" in str(d) for d in _diffs(url, changed))

    extra = sa.MetaData()
    for table in schema.metadata.sorted_tables:
        table.to_metadata(extra)
    extra.tables["patients"].append_column(sa.Column("surprise", sa.Text))
    assert any("add_column" in str(d) for d in _diffs(url, extra))
