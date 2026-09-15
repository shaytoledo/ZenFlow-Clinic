"""One-time data migration: rewrite every stored timestamp to the canonical UTC form (Phase 1.1).

    python -m zenflow.migrate_timestamps --dry-run                  # report only
    python -m zenflow.migrate_timestamps                            # backup, then rewrite
    python -m zenflow.migrate_timestamps --local-tz Asia/Jerusalem  # tz the old process ran in

Legacy shapes found in the database and how they are interpreted:
  * ``YYYY-MM-DD HH:MM:SS``            SQLite ``datetime('now')`` — UTC
  * ``YYYY-MM-DDTHH:MM:SS[.ffffff]``   Python ``datetime.now().isoformat()`` — host-LOCAL time,
                                       converted using ``--local-tz`` (default: CLINIC_TZ)
  * ``…+03:00`` / ``…+00:00``          aware — converted to UTC
Unparseable values are reported and left untouched. Exit code 1 if any were found.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import bot.db as dbmod
from zenflow.clock import classify, normalize

#: table → timestamp columns (only those present in the live schema are touched)
TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "treatment_notes": (
        "created_at",
        "updated_at",
        "completed_at",
        "recommendations_sent_at",
        "followup_sent_at",
        "pending_rec_send_at",
    ),
    "appointments": ("created_at",),
    "intake_sessions": ("created_at",),
    "therapists": ("created_at",),
    "notifications": ("created_at", "read_at", "resolved_at"),
    "google_tokens": ("updated_at",),
}


@dataclass
class MigrationReport:
    dry_run: bool
    local_tz: str
    scanned: int = 0
    converted: int = 0
    already_canonical: int = 0
    empty: int = 0
    unparseable: list[tuple[str, str, int, str]] = field(default_factory=list)
    by_shape: dict[str, int] = field(default_factory=dict)
    backup_path: str | None = None

    def summary(self) -> str:
        mode = "DRY RUN — nothing written" if self.dry_run else "applied"
        lines = [
            f"timestamp migration ({mode}; naive-local values read as {self.local_tz})",
            f"  scanned:            {self.scanned}",
            f"  already canonical:  {self.already_canonical}",
            f"  converted:          {self.converted}  {dict(sorted(self.by_shape.items()))}",
            f"  empty:              {self.empty}",
            f"  unparseable:        {len(self.unparseable)}",
        ]
        for table, col, rowid, value in self.unparseable[:20]:
            lines.append(f"    {table}.{col} rowid={rowid}: {value!r}")
        if self.backup_path:
            lines.append(f"  backup:             {self.backup_path}")
        return "\n".join(lines)


def _existing_columns(conn: object, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()  # type: ignore[attr-defined]
    return {r[1] for r in rows}


def migrate(*, local_tz: str, dry_run: bool, backup: bool = True) -> MigrationReport:
    tz = ZoneInfo(local_tz)
    report = MigrationReport(dry_run=dry_run, local_tz=local_tz)
    conn = dbmod.get_db()
    updates: list[tuple[str, str, int, str]] = []

    for table, columns in TIMESTAMP_COLUMNS.items():
        present = _existing_columns(conn, table)
        for col in columns:
            if col not in present:
                continue
            for rowid, value in conn.execute(f"SELECT rowid, {col} FROM {table}").fetchall():
                report.scanned += 1
                shape = classify(value)
                if shape == "empty":
                    report.empty += 1
                elif shape == "canonical":
                    report.already_canonical += 1
                elif shape == "unparseable":
                    report.unparseable.append((table, col, rowid, str(value)))
                else:
                    new = normalize(
                        value, naive_tz=tz if shape == "naive-local" else ZoneInfo("UTC")
                    )
                    report.by_shape[shape] = report.by_shape.get(shape, 0) + 1
                    updates.append((table, col, rowid, new))

    report.converted = len(updates)
    if dry_run or not updates:
        return report
    if backup:
        from zenflow.db_backup import backup_database

        report.backup_path = backup_database("bak-timestamps")
    for table, col, rowid, new in updates:
        conn.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?", (new, rowid))
    return report


def main(argv: list[str] | None = None) -> int:
    from zenflow.settings import get_settings

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--local-tz",
        default=get_settings().clinic_tz,
        help="IANA zone the OLD process ran in (for naive Python-written values)",
    )
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    report = migrate(local_tz=args.local_tz, dry_run=args.dry_run, backup=not args.no_backup)
    print(report.summary())
    return 1 if report.unparseable else 0


if __name__ == "__main__":
    raise SystemExit(main())
