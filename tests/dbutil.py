"""Helpers for tests that must run on SQLite and on Postgres alike (Phase 12.2.2)."""

from __future__ import annotations

import bot.db as dbmod


def columns(table: str) -> set[str]:
    """A table's column names — `PRAGMA table_info` exists only on SQLite."""
    cur = dbmod.get_db().execute(f"SELECT * FROM {table} WHERE 1 = 0")  # noqa: S608 - test-only
    return {column[0] for column in cur.description}
