"""Load acupoint images from a folder into the media store and the database (Phase 4.3b).

    python -m zenflow.ingest_images <folder> --dry-run   # report only
    python -m zenflow.ingest_images <folder>

File names give the point: ``LI4.png``, ``ST-36.jpg``, ``SP6_diagram.webp`` (the first part is
the code, normalised and resolved through the aliases — ``KD3`` → ``KI3``; a later part may name
the kind: diagram, photo or 3d). Every image must carry its credit and licence, from
``credits.json`` in the same folder::

    {"default": {"credit": "…", "licence": "CC BY-SA 4.0", "licence_url": "https://…",
                 "source_url": "https://…"},
     "files": {"LI4.png": {"credit": "…"}}}          # per-file values override the default

Each image is decoded (size- and decompression-bomb-limited), turned upright, stripped of EXIF
and other metadata, and stored as a web copy (≤1600 px) and a thumbnail (≤320 px) in WebP. Keys
come from the content hash, so re-running updates rows instead of duplicating them. Nothing is
downloaded here: sourcing is a separate, licence-first step (docs/POINT_CARD_DESIGN.md, Q4).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sqlite3
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zenflow.clock import iso_now
from zenflow.storage import Storage, get_storage

KINDS = ("diagram", "photo", "3d")
WEB_SIZE = 1600
THUMB_SIZE = 320
MAX_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 40_000_000
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
CREDITS_FILE = "credits.json"
REQUIRED_META = ("credit", "licence")

CREATE_ACUPOINT_IMAGES = """CREATE TABLE IF NOT EXISTS acupoint_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    point_code TEXT NOT NULL REFERENCES acupoints(code) ON DELETE CASCADE,
    storage_key TEXT NOT NULL UNIQUE,
    thumb_key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'diagram' CHECK (kind IN ('diagram', 'photo', '3d')),
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    credit TEXT NOT NULL,
    licence TEXT NOT NULL,
    licence_url TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL DEFAULT '',
    is_primary INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)"""
CREATE_ACUPOINT_IMAGES_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_acupoint_images_code ON acupoint_images(point_code)"
)


def parse_name(filename: str) -> tuple[str, str]:
    """(code, kind) from a file name: 'ST-36.jpg' → ('ST36', 'diagram'), 'SP6_photo.png' → …"""
    parts = [p for p in re.split(r"[_\s.]+", Path(filename).stem) if p]
    code = re.sub(r"[\s-]", "", parts[0]).upper() if parts else ""
    kind = next((p.lower() for p in parts[1:] if p.lower() in KINDS), "diagram")
    return code, kind


def resolve_code(conn: sqlite3.Connection, code: str) -> str | None:
    """The table code for `code` or one of its aliases; None if no point matches."""
    if conn.execute("SELECT 1 FROM acupoints WHERE code=?", (code,)).fetchone():
        return code
    row = conn.execute(
        "SELECT code FROM acupoints WHERE EXISTS "
        "(SELECT 1 FROM json_each(acupoints.aliases) WHERE value = ?)",
        (code,),
    ).fetchone()
    return str(row[0]) if row else None


def load_credits(folder: Path) -> dict[str, Any]:
    path = folder / CREDITS_FILE
    if not path.is_file():
        return {"default": {}, "files": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return {"default": data.get("default") or {}, "files": data.get("files") or {}}


def metadata_for(credits: dict[str, Any], filename: str) -> dict[str, str]:
    meta = {**credits["default"], **credits["files"].get(filename, {})}
    return {
        key: str(meta.get(key) or "").strip()
        for key in ("credit", "licence", "licence_url", "source_url")
    }


@dataclass
class Rendition:
    web: bytes
    thumb: bytes
    width: int
    height: int


def render(data: bytes) -> Rendition:
    """Decode, turn upright, drop all metadata, and encode the web copy and the thumbnail."""
    from PIL import Image, ImageOps

    if len(data) > MAX_BYTES:
        raise ValueError(f"larger than {MAX_BYTES // (1024 * 1024)} MB")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        previous = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        try:
            with Image.open(io.BytesIO(data)) as probe:
                probe.verify()
            image = Image.open(io.BytesIO(data))
            image.load()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError(f"too many pixels: {exc}") from exc
        except Exception as exc:
            raise ValueError(f"not a readable image: {exc}") from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous

    try:
        upright = ImageOps.exif_transpose(image) or image
        mode = "RGBA" if "A" in upright.getbands() or "transparency" in upright.info else "RGB"
        # A fresh image from the pixels alone: no EXIF, XMP, ICC or comments survive.
        clean = Image.new(mode, upright.size)
        clean.paste(upright.convert(mode))
    except Exception as exc:  # e.g. corrupt EXIF: skip the file, never crash the run
        raise ValueError(f"could not process the image: {exc}") from exc

    def encode(size: int) -> bytes:
        copy = clean.copy()
        copy.thumbnail((size, size))
        out = io.BytesIO()
        copy.save(out, "WEBP", quality=85, method=4)
        return out.getvalue()

    web = encode(WEB_SIZE)
    with Image.open(io.BytesIO(web)) as saved:
        width, height = saved.size
    return Rendition(web=web, thumb=encode(THUMB_SIZE), width=width, height=height)


@dataclass
class IngestReport:
    dry_run: bool
    lines: list[str] = field(default_factory=list)
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0

    def note(self, name: str, outcome: str) -> None:
        self.lines.append(f"  {name}: {outcome}")

    def summary(self) -> str:
        mode = "DRY RUN — nothing written" if self.dry_run else "applied"
        head = (
            f"image ingest ({mode}): {self.added} new, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.skipped} skipped"
        )
        return "\n".join([head, *self.lines])


def ingest_folder(
    folder: Path, conn: sqlite3.Connection, storage: Storage, dry_run: bool = False
) -> IngestReport:
    conn.execute(CREATE_ACUPOINT_IMAGES)
    conn.execute(CREATE_ACUPOINT_IMAGES_INDEX)
    report = IngestReport(dry_run=dry_run)
    credits = load_credits(folder)

    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.name != CREDITS_FILE):
        name = path.name
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            report.skipped += 1
            report.note(name, "skipped — not a .png/.jpg/.webp file")
            continue
        raw_code, kind = parse_name(name)
        code = resolve_code(conn, raw_code) if raw_code else None
        if code is None:
            report.skipped += 1
            report.note(name, f"skipped — no acupoint {raw_code or '(empty name)'}")
            continue
        meta = metadata_for(credits, name)
        missing = [key for key in REQUIRED_META if not meta[key]]
        if missing:
            report.skipped += 1
            report.note(name, f"skipped — no {' or '.join(missing)} in {CREDITS_FILE}")
            continue
        data = path.read_bytes()
        try:
            image = render(data)
        except ValueError as exc:
            report.skipped += 1
            report.note(name, f"skipped — {exc}")
            continue

        digest = hashlib.sha256(data).hexdigest()
        base = f"acupoints/{code.lower()}/{digest[:16]}"
        key, thumb_key = f"{base}.webp", f"{base}-thumb.webp"
        values = {
            "point_code": code,
            "thumb_key": thumb_key,
            "kind": kind,
            "width": image.width,
            "height": image.height,
            "sha256": digest,
            "original_name": name,
            **meta,
        }
        current = conn.execute(
            f"SELECT {', '.join(values)} FROM acupoint_images WHERE storage_key=?",  # noqa: S608
            (key,),
        ).fetchone()
        if current is not None and tuple(current) == tuple(values.values()):
            report.unchanged += 1
            report.note(name, f"{code} ({kind}) unchanged")
            continue
        label = "updated" if current is not None else "new"
        report.note(name, f"{code} ({kind}) {label}, {image.width}×{image.height}")
        if current is not None:
            report.updated += 1
        else:
            report.added += 1
        if dry_run:
            continue

        storage.put(key, image.web, "image/webp")
        storage.put(thumb_key, image.thumb, "image/webp")
        now = iso_now()
        has_primary = conn.execute(
            "SELECT 1 FROM acupoint_images WHERE point_code=? AND is_primary=1 AND storage_key<>?",
            (code, key),
        ).fetchone()
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(f"{c}=excluded.{c}" for c in values)
        conn.execute(
            f"""INSERT INTO acupoint_images
                    (storage_key, {columns}, is_primary, created_at, updated_at)
                VALUES (?, {placeholders}, ?, ?, ?)
                ON CONFLICT(storage_key) DO UPDATE SET {updates},
                    updated_at=excluded.updated_at""",  # noqa: S608
            (key, *values.values(), 0 if has_primary else 1, now, now),
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zenflow.ingest_images", description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = parser.parse_args(argv)
    if not args.folder.is_dir():
        parser.error(f"not a folder: {args.folder}")

    import bot.db as dbmod

    report = ingest_folder(args.folder, dbmod.get_db(), get_storage(), dry_run=args.dry_run)
    print(report.summary())  # noqa: T201 — CLI output
    return 1 if report.skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
