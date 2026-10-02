"""Phase 12.2.2 — the sqlite3-shaped Postgres adapter (`zenflow/pg.py`), the parts that need no server.

The adapter against a real Postgres is proven by the whole suite (`ZF_TEST_DB_URL=… pytest`, CI's
`postgres-suite` job); `tests/integration/test_pg_connection.py` covers its transaction rules.
"""

from __future__ import annotations

import pytest

from zenflow import pg
from zenflow.clock import SQL_NOW


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("SELECT * FROM t WHERE a=? AND b=?", "SELECT * FROM t WHERE a=%s AND b=%s"),
        # a ? inside a string literal is data, not a placeholder
        ("SELECT '?' FROM t WHERE a=?", "SELECT '?' FROM t WHERE a=%s"),
        # % is psycopg's placeholder character: escaped in the SQL and inside literals
        (
            "SELECT * FROM t WHERE a LIKE ? AND b LIKE '%x%'",
            "SELECT * FROM t WHERE a LIKE %s AND b LIKE '%%x%%'",
        ),
        ('SELECT "?" FROM t WHERE a=?', 'SELECT "?" FROM t WHERE a=%s'),
    ],
)
def test_placeholders_are_rewritten_outside_string_literals(sql: str, expected: str) -> None:
    assert pg.translate(sql, has_params=True) == expected


def test_sql_without_parameters_is_left_alone() -> None:
    """psycopg only interprets % when parameters are passed — so nothing is escaped then."""
    assert (
        pg.translate("SELECT '%' || name FROM t", has_params=False) == "SELECT '%' || name FROM t"
    )


def test_the_canonical_now_becomes_the_same_instant_in_postgres() -> None:
    translated = pg.translate(f"UPDATE t SET updated_at={SQL_NOW} WHERE id=?", has_params=True)
    assert SQL_NOW not in translated
    # the PG expression's own literal % (none) and quotes survive; the placeholder is rewritten
    assert translated.endswith("WHERE id=%s")
    assert "to_char(timezone('UTC', now())" in translated


@pytest.mark.parametrize("sql", ["BEGIN IMMEDIATE", "  begin immediate  "])
def test_begin_immediate_is_a_plain_begin(sql: str) -> None:
    assert pg.translate(sql, has_params=False) == "BEGIN"


def test_a_row_reads_like_sqlite3_row() -> None:
    row = pg.Row(("a", 2), {"name": 0, "n": 1})
    assert row["name"] == "a" and row[1] == 2
    assert dict(zip(row.keys(), row, strict=True)) == {"name": "a", "n": 2}
    assert tuple(row) == ("a", 2) and len(row) == 2
    assert row == ("a", 2)
    name, n = row
    assert (name, n) == ("a", 2)


def test_dict_of_a_row_is_its_columns() -> None:
    """`dict(row)` is how the repositories turn rows into JSON — it must give columns, not pairs."""
    row = pg.Row(("a", 2), {"name": 0, "n": 1})
    assert dict(row) == {"name": "a", "n": 2}


def test_integer_id_tables_are_the_ones_sqlite_gives_a_lastrowid() -> None:
    tables = pg.integer_id_tables()
    assert {"appointments", "patients", "jobs", "audit_log", "notifications"} <= tables
    # text / composite keys: an INSERT there must not grow a RETURNING id
    assert not {"therapists", "acupoints", "bot_persistence", "leases"} & tables


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h/db", True),
        ("postgresql+psycopg://u:p@h/db", True),
        ("postgres://u:p@h/db", True),
        ("sqlite:///x.db", False),
        ("", False),
    ],
)
def test_postgres_urls_are_recognised(url: str, expected: bool) -> None:
    assert pg.is_postgres_url(url) is expected


def test_the_driver_suffix_is_stripped_for_psycopg_and_added_for_sqlalchemy() -> None:
    from zenflow.migrate import sqlalchemy_url

    assert pg.libpq_url("postgresql+psycopg://u@h/db") == "postgresql://u@h/db"
    assert sqlalchemy_url("postgresql://u@h/db") == "postgresql+psycopg://u@h/db"
    assert sqlalchemy_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert sqlalchemy_url("sqlite:///x.db") == "sqlite:///x.db"


@pytest.mark.parametrize(
    ("url", "password", "expected"),
    [
        (  # RDS (12.2.6): the URL names the user, the managed password is injected apart
            "postgresql+psycopg://zenflow@db.internal:5432/zenflow?sslmode=require",
            "p@ss/w:rd",
            "postgresql+psycopg://zenflow:p%40ss%2Fw%3Ard@db.internal:5432/zenflow?sslmode=require",
        ),
        ("postgresql://u:given@h/db", "other", "postgresql://u:given@h/db"),  # the URL wins
        ("postgresql://h/db", "pw", "postgresql://h/db"),  # no user: nothing to fill in
    ],
)
def test_the_password_is_filled_into_a_url_that_names_a_user(
    url: str, password: str, expected: str
) -> None:
    from bot.db import _with_password

    assert _with_password(url, password) == expected


def test_db_url_combines_the_url_and_the_injected_password(monkeypatch) -> None:
    import bot.db as dbmod

    monkeypatch.setenv("ZF_DB_URL", "postgresql+psycopg://zenflow@db.internal/zenflow")
    monkeypatch.setenv("ZF_DB_PASSWORD", "s3cret")
    assert dbmod.db_url() == "postgresql+psycopg://zenflow:s3cret@db.internal/zenflow"
