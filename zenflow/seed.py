"""Reference data shipped with the repo, loaded into the database (Phase 4.3a).

    python -m zenflow.seed acupoints --dry-run   # report what would change
    python -m zenflow.seed acupoints             # insert new points, update changed ones

The seed lives in ``zenflow/seed_data/acupoints.json``. Loading is idempotent: a point is keyed
by its WHO code, unchanged rows are left alone, and rows that are not in the file are kept.
``bot.db.init_db()`` loads it automatically into an empty table.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zenflow.clock import iso_now

SEED_DIR = Path(__file__).parent / "seed_data"
ACUPOINTS_SEED = SEED_DIR / "acupoints.json"

#: columns of `acupoints`, in insert order; JSON-typed ones are stored as text
ACUPOINT_COLUMNS: tuple[str, ...] = (
    "code",
    "aliases",
    "name_pinyin",
    "name_cn",
    "name_en",
    "channel",
    "location",
    "actions",
    "needle_depth",
    "needle_angle",
    "contraindications",
    "translations",
    "source",
    "licence",
)
JSON_COLUMNS = frozenset({"aliases", "contraindications", "translations"})
#: the only contraindication tags the page knows how to show
CONTRAINDICATIONS = frozenset({"pregnancy"})

CREATE_ACUPOINTS = """CREATE TABLE IF NOT EXISTS acupoints (
    code TEXT PRIMARY KEY,
    aliases TEXT NOT NULL DEFAULT '[]',
    name_pinyin TEXT NOT NULL DEFAULT '',
    name_cn TEXT NOT NULL DEFAULT '',
    name_en TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    actions TEXT NOT NULL DEFAULT '',
    needle_depth TEXT NOT NULL DEFAULT '',
    needle_angle TEXT NOT NULL DEFAULT '',
    contraindications TEXT NOT NULL DEFAULT '[]',
    translations TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT '',
    licence TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
)"""


def load_acupoints_seed(path: Path = ACUPOINTS_SEED) -> list[dict[str, Any]]:
    """The seed's points, validated. Raises ValueError on a malformed file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    points = data.get("points") if isinstance(data, dict) else None
    if not isinstance(points, list) or not points:
        raise ValueError(f"{path}: expected an object with a non-empty 'points' list")
    seen: set[str] = set()
    for point in points:
        if not isinstance(point, dict) or not point.get("code") or not point.get("name_pinyin"):
            raise ValueError(f"{path}: every point needs a code and a name_pinyin: {point!r}")
        missing = set(ACUPOINT_COLUMNS) - set(point)
        if missing:
            raise ValueError(f"{path}: {point['code']} lacks {sorted(missing)}")
        names = [point["code"], *point["aliases"]]
        clash = seen.intersection(names)
        if clash:
            raise ValueError(f"{path}: code or alias used twice: {sorted(clash)}")
        seen.update(names)
        unknown = set(point["contraindications"]) - CONTRAINDICATIONS
        if unknown:
            raise ValueError(f"{path}: {point['code']} has unknown contraindications {unknown}")
    return points


def _row(point: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        (
            json.dumps(point[c], ensure_ascii=False, sort_keys=True)
            if c in JSON_COLUMNS
            else str(point[c])
        )
        for c in ACUPOINT_COLUMNS
    )


@dataclass
class SeedReport:
    dry_run: bool
    inserted: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: int = 0

    def summary(self) -> str:
        mode = "DRY RUN — nothing written" if self.dry_run else "applied"
        return (
            f"acupoints seed ({mode}): {len(self.inserted)} new, {len(self.updated)} changed, "
            f"{self.unchanged} unchanged"
            + (f"\n  new: {', '.join(self.inserted)}" if self.inserted else "")
            + (f"\n  changed: {', '.join(self.updated)}" if self.updated else "")
        )


def seed_acupoints(
    conn: sqlite3.Connection, points: list[dict[str, Any]] | None = None, dry_run: bool = False
) -> SeedReport:
    """Insert or update every seed point in `acupoints` (created if missing)."""
    conn.execute(CREATE_ACUPOINTS)
    report = SeedReport(dry_run=dry_run)
    columns = ", ".join(ACUPOINT_COLUMNS)
    placeholders = ", ".join("?" for _ in ACUPOINT_COLUMNS)
    updates = ", ".join(f"{c}=excluded.{c}" for c in ACUPOINT_COLUMNS[1:])
    for point in points if points is not None else load_acupoints_seed():
        row = _row(point)
        current = conn.execute(
            f"SELECT {columns} FROM acupoints WHERE code=?", (point["code"],)  # noqa: S608
        ).fetchone()
        if current is not None and tuple(current) == row:
            report.unchanged += 1
            continue
        (report.updated if current is not None else report.inserted).append(point["code"])
        if dry_run:
            continue
        conn.execute(
            f"""INSERT INTO acupoints ({columns}, updated_at) VALUES ({placeholders}, ?)
                ON CONFLICT(code) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",  # noqa: S608
            (*row, iso_now()),
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zenflow.seed", description=__doc__)
    parser.add_argument("dataset", choices=["acupoints"])
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = parser.parse_args(argv)

    import bot.db as dbmod

    report = seed_acupoints(dbmod.get_db(), dry_run=args.dry_run)
    print(report.summary())  # noqa: T201 — CLI output
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
