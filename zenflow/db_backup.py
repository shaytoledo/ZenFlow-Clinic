"""zenflow.db_backup — consistent SQLite backup via the online backup API (WAL content included)."""

from __future__ import annotations

import sqlite3

import bot.db as dbmod
from zenflow.clock import now_utc


def backup_database(suffix: str = "bak") -> str:
    """Copy the live database to ``<db>.<suffix>-<UTC timestamp>`` and return the path."""
    src = dbmod.get_db()
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    dest_path = f"{dbmod.db_path()}.{suffix}-{stamp}"
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
    return dest_path
