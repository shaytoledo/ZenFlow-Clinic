"""
zenflow.pg
───────────
Postgres behind the sqlite3-shaped connection the repositories already use (Phase 12.2.2, ADR-46).

`bot.db.get_db()` returns this when `ZF_DB_URL` is a Postgres URL; otherwise the plain sqlite3
connection, exactly as before. The repositories keep their SQL. The SQL in this codebase is written
to be portable (ON CONFLICT, CASE instead of the two-argument MAX, IN-lists instead of
json_each), and this adapter covers the remaining *mechanical* differences in one place:

| sqlite3 behaviour the code relies on              | here                                                |
|---------------------------------------------------|-----------------------------------------------------|
| `?` placeholders                                  | rewritten to `%s` (outside string literals); `%` → `%%` |
| `cursor.lastrowid` after an INSERT                | `RETURNING id` added for tables with an integer `id` |
| rows read as `row["col"]`, `row[0]`, `dict(row)`  | `Row`                                               |
| `strftime('%Y-%m-%dT%H:%M:%SZ','now')` (SQL_NOW)  | the same instant as `to_char(…)` in UTC             |
| `BEGIN IMMEDIATE`                                 | `BEGIN` — the unique constraints guard the races SQLite's write lock used to |
| `conn.commit()` with autocommit                   | a no-op, as on sqlite3 in autocommit mode           |

Anything else that differs is a bug to fix in the SQL itself: the whole test suite runs against
Postgres in CI (`postgres` job) to find it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from functools import lru_cache
from typing import Any

SQLITE_NOW = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
PG_NOW = "to_char(timezone('UTC', now()), 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
_INSERT = re.compile(r"^\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
_SAVEPOINT = re.compile(r"^\s*SAVEPOINT\s+(\w+)\s*;?\s*$", re.IGNORECASE)
_RELEASE = re.compile(r"^\s*RELEASE\s+(?:SAVEPOINT\s+)?(\w+)\s*;?\s*$", re.IGNORECASE)
_ROLLBACK = re.compile(r"^\s*ROLLBACK\s*;?\s*$", re.IGNORECASE)  # the whole transaction, not TO


def is_postgres_url(url: str) -> bool:
    return url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://"))


def libpq_url(url: str) -> str:
    """SQLAlchemy's `postgresql+psycopg://` → the plain URL psycopg understands."""
    return re.sub(r"^postgresql\+psycopg://", "postgresql://", url)


@lru_cache(maxsize=1)
def integer_id_tables() -> frozenset[str]:
    """Tables whose primary key is an integer `id` — the ones sqlite3 gives a `lastrowid`."""
    import sqlalchemy as sa

    from zenflow.schema import metadata

    return frozenset(
        name
        for name, table in metadata.tables.items()
        if "id" in table.c and table.c.id.primary_key and isinstance(table.c.id.type, sa.Integer)
    )


def translate(sql: str, has_params: bool) -> str:
    """sqlite3-flavoured SQL → psycopg SQL (placeholders, the canonical now, BEGIN IMMEDIATE)."""
    sql = sql.replace(SQLITE_NOW, PG_NOW)
    if re.match(r"^\s*BEGIN\s+IMMEDIATE\s*$", sql, re.IGNORECASE):
        return "BEGIN"
    if not has_params:
        return sql
    out, quote = [], ""
    for ch in sql:
        if quote:
            out.append("%%" if ch == "%" else ch)
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "?":
            out.append("%s")
        elif ch == "%":
            out.append("%%")
        else:
            out.append(ch)
    return "".join(out)


class Row(Sequence[Any]):
    """A result row readable like sqlite3.Row: by column name, by position, and as a mapping."""

    __slots__ = ("_values", "_index")

    def __init__(self, values: Sequence[Any], index: dict[str, int]) -> None:
        self._values = tuple(values)
        self._index = index

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._values[self._index[key]]
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values)

    def keys(self) -> list[str]:
        return list(self._index)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Row):
            return self._values == other._values
        return self._values == other

    def __hash__(self) -> int:
        return hash(self._values)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._index, self._values, strict=False))!r})"


def _row_factory(cursor: Any) -> Any:
    description = cursor.description
    if description is None:
        return tuple
    index = {column.name: i for i, column in enumerate(description)}
    return lambda values: Row(values, index)


class Cursor:
    """The few cursor attributes the code uses: fetches, iteration, rowcount, lastrowid."""

    def __init__(self, cursor: Any, lastrowid: int | None, first: Any = None) -> None:
        self._cursor = cursor
        self.lastrowid = lastrowid
        self._first = first  # a row already read to learn the RETURNING id

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    @property
    def description(self) -> Any:
        return self._cursor.description

    def fetchone(self) -> Any:
        if self._first is not None:
            row, self._first = self._first, None
            return row
        return self._cursor.fetchone() if self._cursor.description else None

    def fetchall(self) -> list[Any]:
        rows = [] if self._first is None else [self._first]
        self._first = None
        if self._cursor.description:
            rows.extend(self._cursor.fetchall())
        return rows

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())


class Connection:
    """A psycopg connection that behaves like the sqlite3 one the repositories were written for."""

    row_factory: Any = None  # assigned by callers used to sqlite3; rows are always `Row` here

    def __init__(self, url: str) -> None:
        import psycopg
        from psycopg.types.numeric import FloatLoader

        self._conn = psycopg.connect(libpq_url(url), autocommit=True, row_factory=_row_factory)
        # AVG()/ROUND() come back as Decimal on Postgres and as float on SQLite; JSON needs float.
        self._conn.adapters.register_loader("numeric", FloatLoader)
        self._outer_savepoint: str | None = None  # a SAVEPOINT that opened the transaction

    @property
    def in_transaction(self) -> bool:
        from psycopg.pq import TransactionStatus

        return bool(self._conn.info.transaction_status != TransactionStatus.IDLE)

    def execute(self, sql: str, params: Sequence[Any] | None = ()) -> Cursor:
        savepoint = _SAVEPOINT.match(sql)
        if savepoint and not self.in_transaction:
            # SQLite: a SAVEPOINT outside a transaction opens one, and RELEASE of it commits.
            self._conn.execute("BEGIN")
            self._outer_savepoint = savepoint.group(1).lower()
        release = _RELEASE.match(sql)
        if release:
            cur = self._conn.execute(sql)
            if release.group(1).lower() == self._outer_savepoint:
                self._conn.execute("COMMIT")
                self._outer_savepoint = None
            return Cursor(cur, None)
        if _ROLLBACK.match(sql):
            self._outer_savepoint = None
        has_params = bool(params)
        statement = translate(sql, has_params)
        match = _INSERT.match(statement)
        wants_id = bool(
            match
            and match.group(1).lower() in integer_id_tables()
            and "RETURNING" not in statement.upper()
        )
        if wants_id:
            statement = statement.rstrip().rstrip(";") + " RETURNING id"
        cur = self._conn.execute(statement, tuple(params or ()) if has_params else None)
        if wants_id:
            first = cur.fetchone()
            return Cursor(cur, int(first[0]) if first else None, None)
        return Cursor(cur, None)

    def commit(self) -> None:
        """Autocommit, like the sqlite3 connection: an explicit BEGIN … COMMIT is sent as SQL."""

    def rollback(self) -> None:
        self._conn.execute("ROLLBACK")
        self._outer_savepoint = None

    def close(self) -> None:
        self._conn.close()
