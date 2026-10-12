"""Fetch the licensed point diagrams named in the manifest (Phase 14.3, owner decision Q4).

    python -m zenflow.fetch_point_images --list                  # what the manifest offers
    python -m zenflow.fetch_point_images <folder> LI4 ST36 …     # fetch these points
    python -m zenflow.fetch_point_images <folder> --all          # every point in the manifest

The manifest (``zenflow/seed_data/point_images.json``) lists one diagram per WHO point from one
source, with its credit and licence. This command only downloads: it writes ``<CODE>.webp`` and a
``credits.json`` into the folder, which ``python -m zenflow.ingest_images <folder>`` then checks,
re-encodes and stores. A download is something the owner approves first (docs/POINT_IMAGE_SOURCING.md).

What it refuses: any host other than the manifest's, anything but HTTPS, a redirect, a file over
the size limit, bytes that are not a WebP image, and a file whose SHA-256 differs from a pinned one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

MANIFEST = Path(__file__).parent / "seed_data" / "point_images.json"
#: the diagrams are 20–120 KB; anything far larger is not what the manifest described
MAX_BYTES = 2 * 1024 * 1024
TIMEOUT_SECONDS = 30.0
USER_AGENT = "ZenFlowClinic-point-images/1.0 (licensed diagrams, with attribution)"


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    """The manifest, validated. Raises ValueError on a malformed file."""
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    source = data.get("source") or {}
    for key in ("host", "credit", "licence", "licence_url", "source_url"):
        if not source.get(key):
            raise ValueError(f"{path}: source.{key} is missing")
    images = data.get("images")
    if not isinstance(images, dict) or not images:
        raise ValueError(f"{path}: expected a non-empty 'images' object")
    for code, image in images.items():
        if not re.fullmatch(r"[A-Z]{2}[0-9]{1,2}", code):  # it becomes a file name
            raise ValueError(f"{path}: {code!r} is not a point code")
        location = str(image.get("path", ""))
        if not location.startswith("/") or ".." in location or not location.endswith(".webp"):
            raise ValueError(f"{path}: {code} has an unusable path {location!r}")
    return data


def url_for(manifest: dict[str, Any], code: str) -> str:
    return f"https://{manifest['source']['host']}{manifest['images'][code]['path']}"


@dataclass
class FetchReport:
    fetched: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    refused: dict[str, str] = field(default_factory=dict)
    bytes: int = 0

    def summary(self) -> str:
        lines = [
            f"point images: {len(self.fetched)} fetched ({self.bytes / 1024:.0f} KB), "
            f"{len(self.present)} already there, {len(self.refused)} refused"
        ]
        lines += [f"  refused {code}: {why}" for code, why in sorted(self.refused.items())]
        return "\n".join(lines)


def _is_webp(data: bytes) -> bool:
    return len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _download(client: httpx.Client, url: str) -> bytes:
    """The body of `url`, or ValueError: no redirects, a size limit, WebP only."""
    with client.stream("GET", url) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError(f"larger than {MAX_BYTES // 1024} KB")
            chunks.append(chunk)
    data = b"".join(chunks)
    if not _is_webp(data):
        raise ValueError("not a WebP image")
    return data


def fetch(
    folder: Path,
    codes: list[str],
    *,
    manifest: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
    delay_seconds: float = 0.2,
) -> FetchReport:
    """Download the diagrams of `codes` into `folder` and (re)write its credits.json."""
    manifest = manifest or load_manifest()
    unknown = [code for code in codes if code not in manifest["images"]]
    if unknown:
        raise ValueError(f"not in the manifest: {unknown}")
    folder.mkdir(parents=True, exist_ok=True)
    report = FetchReport()
    own_client = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )
    try:
        for code in codes:
            target = folder / f"{code}.webp"
            pinned = str(manifest["images"][code].get("sha256", ""))
            if target.is_file() and (
                not pinned or hashlib.sha256(target.read_bytes()).hexdigest() == pinned
            ):
                report.present.append(code)
                continue
            try:
                data = _download(client, url_for(manifest, code))
                if pinned and hashlib.sha256(data).hexdigest() != pinned:
                    raise ValueError("the file changed at the source (SHA-256 differs)")
            except (ValueError, httpx.HTTPError) as exc:
                report.refused[code] = str(exc) or type(exc).__name__
                continue
            target.write_bytes(data)
            report.fetched.append(code)
            report.bytes += len(data)
            if delay_seconds:
                time.sleep(delay_seconds)
    finally:
        if own_client:
            client.close()
    _write_credits(folder, manifest)
    return report


def _write_credits(folder: Path, manifest: dict[str, Any]) -> None:
    """credits.json in the form ingest_images reads: the source's credit and licence for every
    file here, and each file's own address as its source."""
    source = manifest["source"]
    default = {key: source[key] for key in ("credit", "licence", "licence_url", "source_url")}
    files = {
        path.name: {"source_url": url_for(manifest, path.stem)}
        for path in sorted(folder.glob("*.webp"))
        if path.stem in manifest["images"]
    }
    (folder / "credits.json").write_text(
        json.dumps({"default": default, "files": files}, indent=2), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zenflow.fetch_point_images",
        description=(__doc__ or "").split("\n\n")[0],
    )
    parser.add_argument("folder", nargs="?", help="where the files go (e.g. data/point_images_src)")
    parser.add_argument("codes", nargs="*", help="point codes, e.g. LI4 ST36")
    parser.add_argument("--all", action="store_true", help="every point in the manifest")
    parser.add_argument("--list", action="store_true", help="show the manifest and exit")
    args = parser.parse_args(argv)

    manifest = load_manifest()
    if args.list:
        source = manifest["source"]
        print(  # noqa: T201 - CLI output
            f"{len(manifest['images'])} diagrams from {source['host']}, {source['licence']}, "
            f"credit: {source['credit']}"
        )
        return 0
    if not args.folder or args.all == bool(args.codes):
        parser.error("give a folder, then point codes or --all")
    codes = sorted(manifest["images"]) if args.all else [c.upper() for c in args.codes]
    try:
        report = fetch(Path(args.folder), codes, manifest=manifest)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    print(report.summary())  # noqa: T201
    return 1 if report.refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
