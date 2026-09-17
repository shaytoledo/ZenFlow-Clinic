"""Phase 4.3b — acupoint images: the Storage ABC, the ingester, the media route and the API.

No image is downloaded: every picture here is drawn by the test. Sourcing real images waits for
the licence decision (Q4).
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from zenflow import ingest_images as ingest
from zenflow.storage import LocalStorage, StorageError, check_key

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
CREDITS = {
    "default": {
        "credit": "Drawn for ZenFlow tests",
        "licence": "CC0 1.0",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
    },
    "files": {"GB20.png": {"licence": ""}},
}


def _png(size: tuple[int, int], mode: str = "RGBA") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, (13, 148, 136, 255) if mode == "RGBA" else (13, 148, 136)).save(
        out, "PNG"
    )
    return out.getvalue()


def _jpeg_with_exif(size: tuple[int, int]) -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"  # Make
    exif[0x0112] = 6  # Orientation: rotate 90° CW to view
    out = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


@pytest.fixture
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from zenflow.settings import reset_settings

    root = tmp_path / "media"
    monkeypatch.setenv("MEDIA_ROOT", str(root))
    reset_settings()
    return root


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    src = tmp_path / "incoming"
    src.mkdir()
    (src / "credits.json").write_text(json.dumps(CREDITS), encoding="utf-8")
    (src / "LI4.png").write_bytes(_png((2000, 1000)))
    (src / "ST-36.jpg").write_bytes(_jpeg_with_exif((600, 300)))
    (src / "SP6_diagram.webp").write_bytes(_png((400, 400)))  # PNG bytes, .webp name: still decoded
    (src / "KD3_photo.png").write_bytes(_png((500, 500), "RGB"))
    (src / "XX9.png").write_bytes(_png((10, 10)))
    (src / "LR3.png").write_bytes(b"this is not an image")
    (src / "GB20.png").write_bytes(_png((50, 50)))
    (src / "notes.txt").write_text("hello", encoding="utf-8")
    return src


def _rows() -> list[dict[str, Any]]:
    from bot.db import get_db

    return [
        dict(r) for r in get_db().execute("SELECT * FROM acupoint_images ORDER BY point_code, id")
    ]


def _run(folder: Path, root: Path, dry_run: bool = False) -> ingest.IngestReport:
    from bot.db import get_db

    return ingest.ingest_folder(folder, get_db(), LocalStorage(root), dry_run=dry_run)


# ── storage ──
@pytest.mark.parametrize(
    "key",
    [
        "../etc/passwd.png",
        "acupoints/../../x.png",
        "/abs/x.png",
        "acupoints\\li4\\a.png",
        "Acupoints/LI4/a.png",
        "a.png",
        "acupoints/li4/a.exe",
        "acupoints//a.png",
        "",
    ],
)
def test_unsafe_keys_are_refused(key: str, tmp_path: Path) -> None:
    with pytest.raises(StorageError):
        check_key(key)
    with pytest.raises(StorageError):
        LocalStorage(tmp_path).put(key, b"x", "image/png")


def test_local_storage_round_trip(tmp_path: Path) -> None:
    store = LocalStorage(tmp_path / "m")
    key = "acupoints/li4/abc.webp"
    assert not store.exists(key)
    store.put(key, b"data", "image/webp")
    assert store.exists(key) and store.get(key) == b"data"
    assert store.url(key) == "/media/acupoints/li4/abc.webp"
    assert not list((tmp_path / "m" / "acupoints" / "li4").glob(".upload-*")), "no temp file left"
    with pytest.raises(StorageError, match="image/webp"):
        store.put(key, b"data", "image/png")
    store.delete(key)
    store.delete(key)  # deleting twice is fine
    assert not store.exists(key)


# ── the ingester ──
def test_names_become_codes_and_kinds() -> None:
    assert ingest.parse_name("ST-36.jpg") == ("ST36", "diagram")
    assert ingest.parse_name("sp6_photo.png") == ("SP6", "photo")
    assert ingest.parse_name("LI4 3d view.webp") == ("LI4", "3d")
    assert ingest.parse_name(".png") == ("PNG", "diagram")


def test_a_folder_is_ingested_with_licences_and_clean_files(folder: Path, media_root: Path) -> None:
    report = _run(folder, media_root)
    assert (report.added, report.updated, report.skipped) == (4, 0, 4), report.summary()
    summary = report.summary()
    for reason in (
        "XX9.png: skipped — no acupoint XX9",
        "LR3.png: skipped — not a readable image",
        "GB20.png: skipped — no licence",
        "notes.txt: skipped — not a .png/.jpg/.webp",
    ):
        assert reason in summary, summary

    rows = {r["original_name"]: r for r in _rows()}
    assert set(rows) == {"LI4.png", "ST-36.jpg", "SP6_diagram.webp", "KD3_photo.png"}
    assert rows["KD3_photo.png"]["point_code"] == "KI3" and rows["KD3_photo.png"]["kind"] == "photo"
    assert (rows["LI4.png"]["width"], rows["LI4.png"]["height"]) == (1600, 800)
    assert all(r["licence"] == "CC0 1.0" and r["credit"] for r in rows.values())
    assert all(r["is_primary"] == 1 for r in rows.values()), "the first image of a point is primary"

    store = LocalStorage(media_root)
    for row in rows.values():
        with Image.open(io.BytesIO(store.get(row["storage_key"]))) as web:
            assert web.format == "WEBP" and max(web.size) <= 1600
            assert not web.getexif() and "exif" not in web.info and "icc_profile" not in web.info
        with Image.open(io.BytesIO(store.get(row["thumb_key"]))) as thumb:
            assert max(thumb.size) <= 320
    st36 = rows["ST-36.jpg"]
    assert (st36["width"], st36["height"]) == (300, 600), "EXIF orientation applied, then dropped"


def test_ingesting_again_changes_nothing_until_the_credits_do(
    folder: Path, media_root: Path
) -> None:
    _run(folder, media_root)
    before = _rows()
    again = _run(folder, media_root)
    assert (again.added, again.updated, again.unchanged) == (0, 0, 4)
    assert _rows() == before

    credits = {
        "default": CREDITS["default"],
        "files": {"LI4.png": {"credit": "Redrawn"}, "GB20.png": {"licence": ""}},
    }
    (folder / "credits.json").write_text(json.dumps(credits), encoding="utf-8")
    changed = _run(folder, media_root)
    assert (changed.added, changed.updated) == (0, 1)
    assert {r["original_name"]: r["credit"] for r in _rows()}["LI4.png"] == "Redrawn"
    assert len(_rows()) == 4


def test_a_second_image_of_a_point_is_not_primary(folder: Path, media_root: Path) -> None:
    _run(folder, media_root)
    (folder / "LI4_photo.png").write_bytes(_png((300, 200), "RGB"))
    _run(folder, media_root)
    li4 = [r for r in _rows() if r["point_code"] == "LI4"]
    assert [(r["kind"], r["is_primary"]) for r in li4] == [("diagram", 1), ("photo", 0)]


def test_a_dry_run_writes_nothing(folder: Path, media_root: Path) -> None:
    report = _run(folder, media_root, dry_run=True)
    assert report.added == 4 and "DRY RUN" in report.summary()
    assert _rows() == []
    assert not media_root.exists()


def test_oversized_images_are_refused(folder: Path, media_root: Path, monkeypatch) -> None:
    monkeypatch.setattr(ingest, "MAX_PIXELS", 1000)
    report = _run(folder, media_root)
    assert "LI4.png: skipped — too many pixels" in report.summary()
    monkeypatch.setattr(ingest, "MAX_BYTES", 10)
    assert "larger than" in _run(folder, media_root).summary()


def test_the_cli_reports_and_fails_on_skipped_files(folder: Path, media_root: Path, capsys) -> None:
    assert ingest.main([str(folder)]) == 1
    out = capsys.readouterr().out
    assert "4 new" in out and "4 skipped" in out
    for name in ("XX9.png", "LR3.png", "GB20.png", "notes.txt"):
        (folder / name).unlink()
    assert ingest.main([str(folder)]) == 0


# ── serving ──
async def test_media_is_served_to_signed_in_therapists_only(
    folder: Path, media_root: Path, make_therapist, login_as, client, fake_redis
) -> None:
    _run(folder, media_root)
    key = next(r["storage_key"] for r in _rows() if r["point_code"] == "LI4")
    signed_in = await login_as(make_therapist(email="media@example.com", password=PW))

    resp = await signed_in.get(f"/media/{key}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.content == LocalStorage(media_root).get(key)

    assert (await client.get(f"/media/{key}")).status_code == 401
    for bad in ("acupoints/li4/missing.webp", "acupoints/../../bot/db.py", "..%2f..%2fbot%2fdb.py"):
        assert (await signed_in.get(f"/media/{bad}")).status_code == 404, bad


async def test_the_api_links_images_only_when_enabled(
    folder: Path, media_root: Path, make_therapist, login_as, fake_redis, monkeypatch
) -> None:
    from zenflow.settings import reset_settings

    _run(folder, media_root)
    client = await login_as(make_therapist(email="img-api@example.com", password=PW))
    off = (await client.get("/api/acupoints")).json()
    assert off["points"]["LI4"]["images"] == []

    monkeypatch.setenv("ZF_POINT_IMAGES", "1")
    reset_settings()
    on = (await client.get("/api/acupoints")).json()
    [image] = on["points"]["LI4"]["images"]
    assert image["url"].startswith("/media/acupoints/li4/") and image["url"].endswith(".webp")
    assert image["thumb_url"].endswith("-thumb.webp")
    assert image["primary"] is True and image["kind"] == "diagram"
    assert image["credit"] == "Drawn for ZenFlow tests" and image["licence"] == "CC0 1.0"
    assert "storage_key" not in image and "sha256" not in image
    assert on["points"]["KI3"]["images"][0]["kind"] == "photo"
    assert (await client.get(image["url"])).status_code == 200
