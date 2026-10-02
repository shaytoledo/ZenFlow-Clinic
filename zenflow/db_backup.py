"""zenflow.db_backup — consistent SQLite backup via the online backup API (WAL content included).

python -m zenflow.db_backup              # <db>.bak-<UTC stamp>, beside the database
python -m zenflow.db_backup --encrypt    # <db>.bak-<UTC stamp>.enc — the copy that may leave
                                         # the host (9.9; BACKUP_ENCRYPTION_KEY, zenflow.file_crypto)
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import bot.db as dbmod
from zenflow.clock import now_utc


class NotSqlite(RuntimeError):
    """The database is Postgres (ZF_DB_URL): it is backed up by its own tools, not by this file copy."""


def backup_database(suffix: str = "bak", *, encrypt: bool = False) -> str:
    """Copy the live database to ``<db>.<suffix>-<UTC timestamp>`` and return the path.

    With `encrypt`, the copy is taken in memory, Fernet-encrypted and written as ``….enc`` — the
    plaintext never touches disk. It refuses up front when `BACKUP_ENCRYPTION_KEY` is not set.
    """
    if dbmod.is_postgres():
        raise NotSqlite(
            "ZF_DB_URL is Postgres: back it up with pg_dump or the server's snapshots / "
            "point-in-time recovery (Phase 12.2.7), not with this SQLite file copy"
        )
    src = dbmod.get_db()
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    dest_path = f"{dbmod.db_path()}.{suffix}-{stamp}"
    if encrypt:
        from zenflow.file_crypto import SUFFIX, require_fernet

        fernet = require_fernet()  # fail before copying anything if there is no key
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
            token = fernet.encrypt(mem.serialize())
        finally:
            mem.close()
        dest_path += SUFFIX
        dbmod.write_owner_only(dest_path, token)
        return dest_path
    dbmod.create_owner_only(dest_path)  # a full copy of the database: owner-only from the start
    dest = sqlite3.connect(dest_path)
    try:
        src.backup(dest)
    finally:
        dest.close()
    return dest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the SQLite database.")
    parser.add_argument("--encrypt", action="store_true", help="encrypt the copy (9.9)")
    args = parser.parse_args(argv)
    from zenflow.file_crypto import EncryptionUnavailable

    try:
        print(backup_database(encrypt=args.encrypt))
    except (EncryptionUnavailable, NotSqlite) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
