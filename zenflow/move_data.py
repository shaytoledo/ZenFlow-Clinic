"""
zenflow.move_data — move the clinic's data between databases, and verify it (Phase 12.2.9).

    python -m zenflow.move_data --from data/zenflow.db --to postgresql+psycopg://…   # cut-over
    python -m zenflow.move_data --from s3://bucket/migration/zenflow.db.enc --to app  # on AWS
    python -m zenflow.move_data --from app --to s3://bucket/migration/rollback.db.enc   # rollback

`--from` / `--to` take a SQLite path, a Postgres URL, `app` (the app's own database: ZF_DB_URL +
ZF_DB_PASSWORD, or the SQLite file) or `s3://bucket/key` (a SQLite file in S3, Fernet-encrypted
when it ends in `.enc` — BACKUP_ENCRYPTION_KEY). The runbook is docs/MIGRATION_RUNBOOK.md.

What it guarantees:
- **The source is never modified.** SQLite is opened read-only. Rolling back means pointing the app
  at the old database again.
- **The target must be empty.** It is built at the source's Alembic revision (`zenflow.migrate`),
  and only the acupoint reference data may already be there (it is replaced from the source).
- **All or nothing.** One transaction on the target, tables in foreign-key order, ids kept as they
  are. On Postgres every id sequence then continues after the largest id.
- **Verified.** Every table's row count and SHA-256 over its rows (`zenflow.restore_drill`) must be
  identical in source and target, or the move reports the differences and exits 1.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import bot.db as dbmod

BATCH = 200
REFERENCE_TABLES = ("acupoint_images", "acupoints")  # seeded on an empty database; replaced


class MoveRefused(RuntimeError):
    """The move would not be safe (non-empty target, revision mismatch, unreadable source)."""


# ── endpoints ──
def _s3(uri: str) -> tuple[Any, str, str]:
    from urllib.parse import urlsplit

    from zenflow.settings import get_settings
    from zenflow.storage import s3_client

    parts = urlsplit(uri)
    settings = get_settings()
    return (
        s3_client(settings.s3_region, settings.s3_endpoint_url),
        parts.netloc,
        parts.path.lstrip("/"),
    )


def _download(uri: str, workdir: Path) -> Path:
    client, bucket, key = _s3(uri)
    data = client.get_object(Bucket=bucket, Key=key)["Body"].read()
    if key.endswith(".enc"):
        from zenflow.file_crypto import require_fernet

        data = require_fernet().decrypt(data)
    path = workdir / "source.db"
    dbmod.write_owner_only(path, data)
    return path


def _upload(path: Path, uri: str) -> None:
    from zenflow.settings import get_settings

    client, bucket, key = _s3(uri)
    data = path.read_bytes()
    if key.endswith(".enc"):
        from zenflow.file_crypto import require_fernet

        data = require_fernet().encrypt(data)
    extra: dict[str, str] = {}
    kms = get_settings().s3_kms_key_id
    if kms:
        extra = {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": kms}
    client.put_object(Bucket=bucket, Key=key, Body=data, **extra)


def _spec(value: str) -> str:
    """`app` → the app's own database; anything else as given."""
    if value != "app":
        return value
    return dbmod.db_url() or str(dbmod.db_path())


def _is_postgres(spec: str) -> bool:
    from zenflow.pg import is_postgres_url

    return is_postgres_url(spec)


def _open_sqlite(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _prepare_target(spec: str) -> None:
    """Build the schema on the target (Alembic), exactly as a fresh deployment would."""
    from zenflow.migrate import sqlalchemy_url, upgrade

    upgrade(sqlalchemy_url(spec) if _is_postgres(spec) else f"sqlite:///{spec}")


# ── the copy ──
def _revision(conn: Any) -> str | None:
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except Exception:  # noqa: BLE001 - no alembic_version table: never migrated
        return None
    return str(row[0]) if row else None


def _check_target_is_empty(conn: Any) -> None:
    from zenflow.schema import metadata

    busy = {}
    for name in metadata.tables:
        if name in REFERENCE_TABLES:
            continue
        rows = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]  # noqa: S608
        if rows:
            busy[name] = rows
    if busy:
        raise MoveRefused(f"the target already holds data: {busy} — move into an empty database")


def _copy(source: Any, target: Any, *, target_is_postgres: bool) -> dict[str, int]:
    from zenflow.schema import metadata

    copied: dict[str, int] = {}
    target.execute("BEGIN")
    try:
        for name in reversed([t.name for t in metadata.sorted_tables]):
            if name in REFERENCE_TABLES:
                target.execute(f'DELETE FROM "{name}"')  # noqa: S608 - fixed table names
        for table in metadata.sorted_tables:
            columns = [c.name for c in table.columns]
            names = ", ".join(f'"{c}"' for c in columns)
            order = ", ".join(c.name for c in table.primary_key.columns) or "1"
            rows = source.execute(
                f'SELECT {names} FROM "{table.name}" ORDER BY {order}'
            ).fetchall()  # noqa: S608
            for start in range(0, len(rows), BATCH):
                chunk = rows[start : start + BATCH]
                marks = ", ".join("(" + ", ".join("?" * len(columns)) + ")" for _ in chunk)
                values = [value for row in chunk for value in tuple(row)]
                target.execute(
                    f'INSERT INTO "{table.name}" ({names}) VALUES {marks}', values
                )  # noqa: S608
            copied[table.name] = len(rows)
        if target_is_postgres:
            from zenflow.pg import integer_id_tables

            for name in sorted(integer_id_tables()):
                target.execute(
                    f"SELECT setval(pg_get_serial_sequence('\"{name}\"', 'id'), "  # noqa: S608
                    f'(SELECT COALESCE(MAX(id), 0) + 1 FROM "{name}"), false)'
                )
        target.execute("COMMIT")
    except Exception:
        target.execute("ROLLBACK")
        raise
    return copied


def move(source_spec: str, target_spec: str) -> dict[str, Any]:
    from zenflow.pg import Connection
    from zenflow.restore_drill import differences, fingerprint

    started = time.monotonic()
    with ExitStack() as stack:
        workdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="zenflow-move-")))
        source_spec, target_spec = _spec(source_spec), _spec(target_spec)
        if source_spec.startswith("s3://"):
            source_spec = str(_download(source_spec, workdir))
        upload_to = None
        if target_spec.startswith("s3://"):
            upload_to, target_spec = target_spec, str(workdir / "target.db")

        if _is_postgres(source_spec):
            source: Any = Connection(source_spec)
        else:
            if not Path(source_spec).is_file():
                raise MoveRefused(f"no SQLite database at {source_spec}")
            source = _open_sqlite(Path(source_spec), read_only=True)
        stack.callback(source.close)
        revision = _revision(source)
        if revision is None:
            raise MoveRefused("the source has no Alembic revision — start the app on it once first")

        _prepare_target(target_spec)
        target_is_postgres = _is_postgres(target_spec)
        target: Any = (
            Connection(target_spec)
            if target_is_postgres
            else _open_sqlite(Path(target_spec), read_only=False)
        )
        stack.callback(target.close)
        if _revision(target) != revision:
            raise MoveRefused(
                f"revision mismatch: source {revision}, target {_revision(target)} — "
                "upgrade the source first (python -m zenflow.migrate upgrade)"
            )
        _check_target_is_empty(target)
        copied = _copy(source, target, target_is_postgres=target_is_postgres)
        diffs = differences(fingerprint(source), fingerprint(target))
        if upload_to and not diffs:
            target.close()
            _upload(Path(target_spec), upload_to)
    return {
        "ok": not diffs,
        "from": "postgres" if _is_postgres(source_spec) else "sqlite",
        "to": upload_to or ("postgres" if target_is_postgres else "sqlite"),
        "alembic": revision,
        "rows": copied,
        "total_rows": sum(copied.values()),
        "seconds": round(time.monotonic() - started, 2),
        "differences": diffs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Move the clinic's data between databases (12.2.9)."
    )
    parser.add_argument(
        "--from", dest="source", required=True, help="SQLite path, Postgres URL, app or s3://"
    )
    parser.add_argument(
        "--to", dest="target", required=True, help="SQLite path, Postgres URL, app or s3://"
    )
    args = parser.parse_args(argv)
    try:
        report = move(args.source, args.target)
    except MoveRefused as exc:
        print(json.dumps({"ok": False, "refused": str(exc)}))  # noqa: T201 - CLI output
        return 2
    print(json.dumps(report, indent=2))  # noqa: T201 - CLI output
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
