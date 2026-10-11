"""Reference data shipped with the repo, loaded into the database (Phase 4.3a).

    python -m zenflow.seed acupoints --dry-run   # report what would change
    python -m zenflow.seed acupoints             # insert new points, update changed ones

The seed lives in ``zenflow/seed_data/acupoints.json``: the 361 points of the WHO standard and
the common extra points. Loading is idempotent: a point is keyed by its code, unchanged rows are
left alone, and rows that are not in the file are kept. ``bot.db.init_db()`` applies it at every
start-up, so a release that ships new or corrected points reaches an existing database without a
manual step. The file is the source of truth: to change a point, edit the file.
"""

from __future__ import annotations

import argparse
import functools
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
    return [dict(point) for point in _load(str(path), path.stat().st_mtime_ns)]


@functools.lru_cache(maxsize=4)
def _load(path: str, mtime_ns: int) -> tuple[dict[str, Any], ...]:
    """Parse and validate once per file version: every start-up and every test reads the seed."""
    del mtime_ns  # part of the cache key only
    data = json.loads(Path(path).read_text(encoding="utf-8"))
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
    return tuple(points)


@functools.lru_cache(maxsize=4)
def _rows(path: str, mtime_ns: int) -> tuple[tuple[str, ...], ...]:
    """The shipped seed as table rows, built once per file version."""
    return tuple(_row(point) for point in _load(path, mtime_ns))


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


#: rows per INSERT: one statement per batch keeps a 400-point seed to a handful of round trips
_BATCH = 50


def seed_acupoints(
    conn: sqlite3.Connection, points: list[dict[str, Any]] | None = None, dry_run: bool = False
) -> SeedReport:
    """Insert or update every seed point in `acupoints` (created if missing)."""
    conn.execute(CREATE_ACUPOINTS)
    report = SeedReport(dry_run=dry_run)
    columns = ", ".join(ACUPOINT_COLUMNS)
    current = {
        str(row[0]): tuple(row)
        for row in conn.execute(f"SELECT {columns} FROM acupoints").fetchall()  # noqa: S608
    }
    rows = (
        tuple(_row(point) for point in points)
        if points is not None
        else _rows(str(ACUPOINTS_SEED), ACUPOINTS_SEED.stat().st_mtime_ns)
    )
    changed: list[tuple[str, ...]] = []
    for row in rows:
        if current.get(row[0]) == row:
            report.unchanged += 1
            continue
        (report.updated if row[0] in current else report.inserted).append(row[0])
        changed.append(row)
    if dry_run or not changed:
        return report
    one = "(" + ", ".join("?" for _ in ACUPOINT_COLUMNS) + ", ?)"
    updates = ", ".join(f"{c}=excluded.{c}" for c in ACUPOINT_COLUMNS[1:])
    now = iso_now()
    # one transaction: one commit instead of one per batch (unless the caller already opened one)
    own_transaction = not getattr(conn, "in_transaction", False)
    if own_transaction:
        conn.execute("BEGIN")
    try:
        for start in range(0, len(changed), _BATCH):
            batch = changed[start : start + _BATCH]
            conn.execute(
                f"""INSERT INTO acupoints ({columns}, updated_at) VALUES {", ".join(one for _ in batch)}
                    ON CONFLICT(code) DO UPDATE SET {updates}, updated_at=excluded.updated_at""",  # noqa: S608
                [value for row in batch for value in (*row, now)],
            )
        if own_transaction:
            conn.execute("COMMIT")
    except BaseException:
        if own_transaction:
            conn.execute("ROLLBACK")
        raise
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
