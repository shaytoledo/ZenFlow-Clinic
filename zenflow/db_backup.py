"""zenflow.db_backup — a consistent copy of the database, on SQLite or on Postgres.

python -m zenflow.db_backup              # <db>.bak-<UTC stamp>, beside the database file
python -m zenflow.db_backup --encrypt    # … .enc — the copy that may leave the host (9.9;
                                         # BACKUP_ENCRYPTION_KEY, zenflow.file_crypto)

SQLite: the online backup API (WAL content included). Postgres (ZF_DB_URL, 12.2.7): `pg_dump
--format=custom` (it must be on PATH and at least the server's version). On AWS the primary
backups are RDS's own (daily snapshots + point-in-time recovery, docs/BACKUP_DR.md); this logical
dump is the portable extra copy, and what the restore drill (`zenflow.restore_drill`) restores.

Every backup file is owner-only from its first byte (A10). With `--encrypt` the plaintext never
touches disk. On a single host, `db.backup` takes one automatically every ZF_BACKUP_HOURS and
keeps the newest ZF_BACKUP_KEEP (a `zenflow.periodic` task, 12.2.7).
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import subprocess  # nosec B404 - pg_dump, with a fixed argument list and no shell
import sys
from datetime import UTC, datetime
from pathlib import Path

import bot.db as dbmod
from zenflow import periodic
from zenflow.clock import now_utc

logger = logging.getLogger(__name__)

AUTO_SUFFIX = "auto"


class BackupUnavailable(RuntimeError):
    """The backup cannot be taken here (no pg_dump, pg_dump failed)."""


def pg_command(tool: str, url: str) -> tuple[list[str], dict[str, str]]:
    """`tool` with a libpq URL that carries no password; the password goes in PGPASSWORD (a
    password in argv is visible to every local user in `ps`)."""
    from urllib.parse import urlsplit, urlunsplit

    from zenflow.pg import libpq_url

    exe = shutil.which(tool)
    if not exe:
        raise BackupUnavailable(f"{tool} is not on PATH (install the PostgreSQL client tools)")
    parts = urlsplit(libpq_url(url))
    env = dict(os.environ)
    if parts.password is not None:
        env["PGPASSWORD"] = parts.password
        host = f"[{parts.hostname}]" if parts.hostname and ":" in parts.hostname else parts.hostname
        netloc = f"{parts.username}@{host}" + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return [exe, "--dbname", urlunsplit(parts)], env


def _pg_dump() -> bytes:
    cmd, env = pg_command("pg_dump", dbmod.db_url())
    done = subprocess.run(  # noqa: S603 # nosec B603 - fixed argv, no shell, no user input
        [*cmd, "--format=custom", "--no-owner", "--no-privileges"],
        capture_output=True,
        env=env,
        timeout=3600,
        check=False,
    )
    if done.returncode != 0:
        raise BackupUnavailable(f"pg_dump failed: {done.stderr.decode(errors='replace')[-400:]}")
    return done.stdout


def backup_database(suffix: str = "bak", *, encrypt: bool = False) -> str:
    """Copy the live database to ``<db>.<suffix>-<UTC timestamp>[.pgdump][.enc]``; return the path.

    With `encrypt`, the copy is taken in memory and Fernet-encrypted before it is written. It
    refuses up front when `BACKUP_ENCRYPTION_KEY` is not set.
    """
    stamp = now_utc().strftime("%Y%m%dT%H%M%SZ")
    dest_path = f"{dbmod.db_path()}.{suffix}-{stamp}"
    fernet = None
    if encrypt:
        from zenflow.file_crypto import require_fernet

        fernet = require_fernet()  # fail before copying anything if there is no key
    if dbmod.is_postgres():
        dest_path += ".pgdump"
        data = _pg_dump()
    else:
        src = dbmod.get_db()
        if fernet is None:
            dbmod.create_owner_only(dest_path)  # a full copy of the database: owner-only
            dest = sqlite3.connect(dest_path)
            try:
                src.backup(dest)
            finally:
                dest.close()
            return dest_path
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
            data = mem.serialize()
        finally:
            mem.close()
    if fernet is not None:
        from zenflow.file_crypto import SUFFIX

        dest_path += SUFFIX
        data = fernet.encrypt(data)
    dbmod.write_owner_only(dest_path, data)
    return dest_path


# ── the automatic backup on a single host (12.2.7) ──
def automatic_backups() -> list[Path]:
    """The automatic backups beside the database, oldest first (their names sort by time)."""
    db = dbmod.db_path()
    return sorted(db.parent.glob(f"{db.name}.{AUTO_SUFFIX}-*"))


def _taken_at(path: Path) -> datetime:
    """The UTC instant in the backup's own name (``….auto-20261003T010000Z…``)."""
    stamp = path.name.split(f".{AUTO_SUFFIX}-", 1)[1][:16]
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


@periodic.task("db.backup", every_seconds=3600)
def backup_if_due() -> str | None:
    """Every hour, check: on SQLite, take a backup when the newest is ZF_BACKUP_HOURS old, then
    keep the newest ZF_BACKUP_KEEP. Encrypted whenever BACKUP_ENCRYPTION_KEY is set. Postgres is
    skipped — RDS backs itself up (snapshots + point-in-time recovery)."""
    from zenflow.settings import get_settings

    settings = get_settings()
    hours, keep = settings.flags.backup_hours, settings.flags.backup_keep
    if hours <= 0 or dbmod.is_postgres():
        return None
    existing = automatic_backups()
    if existing and (now_utc() - _taken_at(existing[-1])).total_seconds() < hours * 3600:
        return None
    path = backup_database(AUTO_SUFFIX, encrypt=bool(settings.backup_encryption_key))
    for old in automatic_backups()[:-keep]:
        old.unlink(missing_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up the database (SQLite or Postgres).")
    parser.add_argument("--encrypt", action="store_true", help="encrypt the copy (9.9)")
    args = parser.parse_args(argv)
    from zenflow.file_crypto import EncryptionUnavailable

    try:
        print(backup_database(encrypt=args.encrypt))  # noqa: T201 - CLI output
    except (EncryptionUnavailable, BackupUnavailable) as exc:
        print(f"refused: {exc}", file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
