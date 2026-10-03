"""
zenflow.restore_drill — prove that a backup restores, table by table (Phase 12.2.7).

    python -m zenflow.restore_drill                  # back up the live DB, restore into a scratch copy, compare
    python -m zenflow.restore_drill --keep           # … and leave the scratch copy for inspection
    python -m zenflow.restore_drill --compare-url postgresql://…   # AWS: compare a point-in-time
                                                     # restored instance with the live one

A backup nobody has restored is a hope, not a backup. The drill:
1. fingerprints the live database: every table's row count and a SHA-256 over its rows in
   primary-key order, plus the Alembic revision;
2. takes a backup with the production code path (`zenflow.db_backup`), encrypted when
   BACKUP_ENCRYPTION_KEY is set;
3. restores it into a scratch copy (SQLite: a temp file, after `PRAGMA integrity_check`;
   Postgres: a new database restored with `pg_restore`, dropped afterwards);
4. fingerprints the copy and compares.

It prints one JSON report: sizes, how long the backup and the restore took (the RTO's raw
material) and any table that differs. Exit 0 = identical, 1 = a difference, 2 = could not run.
Run it on a quiet database: a write between steps 1 and 2 shows up as a difference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess  # nosec B404 - createdb/pg_restore/dropdb with fixed argv, no shell
import tempfile
import time
from pathlib import Path
from typing import Any

import bot.db as dbmod
from zenflow.clock import now_utc


def fingerprint(conn: Any) -> dict[str, Any]:
    """{table: {"rows": n, "sha256": h}} for every table in the schema, and the revision."""
    from zenflow.schema import metadata

    out: dict[str, Any] = {}
    for name, table in sorted(metadata.tables.items()):
        order = ", ".join(c.name for c in table.primary_key.columns) or "1"
        digest = hashlib.sha256()
        rows = 0
        for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY {order}'):  # noqa: S608
            digest.update(repr(tuple(row)).encode())
            rows += 1
        out[name] = {"rows": rows, "sha256": digest.hexdigest()}
    version = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    out["_alembic"] = str(version[0]) if version else None
    return out


def differences(live: dict[str, Any], restored: dict[str, Any]) -> list[str]:
    diffs = []
    for key in sorted(set(live) | set(restored)):
        if live.get(key) != restored.get(key):
            diffs.append(f"{key}: live {live.get(key)} != restored {restored.get(key)}")
    return diffs


def _backup_bytes(path: str) -> bytes:
    """The backup's plaintext (decrypted in memory when it is an encrypted copy)."""
    data = Path(path).read_bytes()
    if path.endswith(".enc"):
        from zenflow.file_crypto import require_fernet

        data = require_fernet().decrypt(data)
    return data


def _restore_sqlite(data: bytes, workdir: Path) -> tuple[dict[str, Any], str]:
    target = workdir / "restored.db"
    dbmod.write_owner_only(target, data)
    conn = sqlite3.connect(target)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"integrity_check: {integrity}")
        return fingerprint(conn), str(target)
    finally:
        conn.close()


def _run(cmd: list[str], env: dict[str, str], data: bytes | None = None) -> None:
    done = subprocess.run(  # noqa: S603 # nosec B603 - fixed argv, no shell
        cmd, input=data, capture_output=True, env=env, timeout=3600, check=False
    )
    if done.returncode != 0:
        raise RuntimeError(
            f"{Path(cmd[0]).name} failed: {done.stderr.decode(errors='replace')[-400:]}"
        )


def _scratch_url(url: str, name: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    return urlunsplit(urlsplit(url)._replace(path=f"/{name}"))


def _restore_postgres(data: bytes, keep: bool) -> tuple[dict[str, Any], str]:
    from zenflow.db_backup import pg_command
    from zenflow.pg import Connection

    url = dbmod.db_url()
    scratch = f"zenflow_restore_drill_{now_utc().strftime('%Y%m%d%H%M%S')}"
    admin = Connection(url)
    try:
        admin.execute(f'CREATE DATABASE "{scratch}"')
        try:
            cmd, env = pg_command("pg_restore", _scratch_url(url, scratch))
            _run([*cmd, "--no-owner", "--no-privileges", "--exit-on-error"], env, data)
            restored = Connection(_scratch_url(url, scratch))
            try:
                return fingerprint(restored), scratch
            finally:
                restored.close()
        finally:
            if not keep:
                admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
    finally:
        admin.close()


def drill(*, keep: bool = False) -> dict[str, Any]:
    from zenflow.db_backup import backup_database
    from zenflow.settings import get_settings

    encrypt = bool(get_settings().backup_encryption_key)
    live = fingerprint(dbmod.get_db())
    started = time.monotonic()
    path = backup_database("drill", encrypt=encrypt)
    backup_seconds = time.monotonic() - started
    try:
        data = _backup_bytes(path)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="zenflow-drill-") as tmp:
            if dbmod.is_postgres():
                restored, where = _restore_postgres(data, keep)
            else:
                restored, where = _restore_sqlite(data, Path(tmp))
                if keep:
                    kept = Path(f"{dbmod.db_path()}.drill-restored.db")
                    dbmod.write_owner_only(kept, Path(where).read_bytes())
                    where = str(kept)
        restore_seconds = time.monotonic() - started
    finally:
        if not keep:
            Path(path).unlink(missing_ok=True)
    diffs = differences(live, restored)
    return {
        "ok": not diffs,
        "database": "postgres" if dbmod.is_postgres() else "sqlite",
        "backup": path if keep else "(deleted after the drill)",
        "backup_bytes": len(data),
        "encrypted": encrypt,
        "backup_seconds": round(backup_seconds, 2),
        "restore_seconds": round(restore_seconds, 2),
        "restored_into": where if keep else "(dropped after the drill)",
        "tables": sum(1 for key in live if not key.startswith("_")),
        "rows": sum(v["rows"] for k, v in live.items() if not k.startswith("_")),
        "alembic": live["_alembic"],
        "differences": diffs,
        "at": now_utc().isoformat(),
    }


def compare_with(url: str) -> dict[str, Any]:
    """AWS drill: fingerprint a restored instance (point-in-time recovery) against the live one."""
    from zenflow.pg import Connection

    other = Connection(url)
    try:
        diffs = differences(fingerprint(dbmod.get_db()), fingerprint(other))
    finally:
        other.close()
    return {"ok": not diffs, "compared_with": "the given database", "differences": diffs}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up, restore and compare (12.2.7).")
    parser.add_argument("--keep", action="store_true", help="keep the backup and scratch copy")
    parser.add_argument("--compare-url", help="compare the live DB with this restored database")
    args = parser.parse_args(argv)
    dbmod.init_db()
    try:
        report = compare_with(args.compare_url) if args.compare_url else drill(keep=args.keep)
    except Exception as exc:  # noqa: BLE001 - the drill reports, it does not crash
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))  # noqa: T201
        return 2
    print(json.dumps(report, indent=2))  # noqa: T201 - CLI output
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
