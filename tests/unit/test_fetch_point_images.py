"""Phase 14.3 — the licensed point diagrams are fetched from one pinned source, and nothing else.

The manifest names one diagram per WHO point (owner decision Q4: HOPE Neuro-Acupuncture Rehab,
CC BY-SA 4.0). These tests never touch the network: the HTTP client is a stub.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import httpx
import pytest

from zenflow import fetch_point_images as fpi

WEBP = b"RIFF" + (26).to_bytes(4, "little") + b"WEBP" + b"VP8 " + b"\x00" * 18
HOST = "images.example"
MANIFEST = {
    "source": {
        "host": HOST,
        "credit": "© 2024 Example Clinic",
        "licence": "CC BY-SA 4.0",
        "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://images.example/chart",
    },
    "images": {"LI4": {"path": "/img/li4.webp"}, "ST36": {"path": "/img/st36.webp"}},
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def _serving(body: bytes = WEBP, status: int = 200, seen: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx.Response(status, content=body)

    return handler


# ── the shipped manifest ──
def test_the_manifest_offers_one_diagram_for_every_who_point() -> None:
    from zenflow.seed import load_acupoints_seed

    manifest = fpi.load_manifest()
    who = {p["code"] for p in load_acupoints_seed() if p["channel"] != "Extra Point"}
    assert set(manifest["images"]) == who and len(who) == 361
    source = manifest["source"]
    assert source["licence"] == "CC BY-SA 4.0" and "creativecommons.org" in source["licence_url"]
    assert source["credit"], "CC BY needs the credit line shown with the image"
    paths = [image["path"] for image in manifest["images"].values()]
    assert len(set(paths)) == len(paths), "two points share one file"
    rejected = {code for code, image in manifest["images"].items() if image.get("reject")}
    channel_of = {p["code"]: p["channel"] for p in load_acupoints_seed()}
    assert {channel_of[code] for code in rejected} == {"Bladder", "Gallbladder"}
    assert len(rejected) == 67 + 44, "the two sets whose drawings name the wrong point"
    assert set(fpi.accepted(manifest)) == who - rejected
    for code, image in manifest["images"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", image.get("sha256", "")), f"{code} is not pinned"
    for code in manifest["images"]:
        assert re.fullmatch(
            rf"https://{re.escape(source['host'])}/[\w./-]+\.webp", fpi.url_for(manifest, code)
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda m: m["source"].pop("licence"), "source.licence"),
        (lambda m: m["images"]["LI4"].update(path="https://evil.example/x.webp"), "unusable path"),
        (lambda m: m["images"]["LI4"].update(path="/img/../../etc/passwd.webp"), "unusable path"),
        (lambda m: m["images"]["LI4"].update(path="/img/li4.svg"), "unusable path"),
        (lambda m: m.update(images={}), "non-empty"),
        (lambda m: m["images"].update({"../x": {"path": "/img/x.webp"}}), "not a point code"),
    ],
)
def test_a_manifest_that_could_fetch_something_else_is_refused(tmp_path, change, message) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    change(manifest)
    path = tmp_path / "point_images.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        fpi.load_manifest(path)


# ── fetching ──
def test_a_fetch_writes_the_files_and_their_credits(tmp_path: Path) -> None:
    seen: list[str] = []
    report = fpi.fetch(
        tmp_path,
        ["LI4", "ST36"],
        manifest=MANIFEST,
        client=_client(_serving(seen=seen)),
        delay_seconds=0,
    )
    assert report.fetched == ["LI4", "ST36"] and not report.refused
    assert seen == [f"https://{HOST}/img/li4.webp", f"https://{HOST}/img/st36.webp"]
    assert (tmp_path / "LI4.webp").read_bytes() == WEBP
    credits = json.loads((tmp_path / "credits.json").read_text(encoding="utf-8"))
    assert credits["default"]["licence"] == "CC BY-SA 4.0"
    assert credits["default"]["credit"] == "© 2024 Example Clinic"
    assert credits["files"]["LI4.webp"]["source_url"] == f"https://{HOST}/img/li4.webp"

    from zenflow.ingest_images import load_credits, metadata_for

    meta = metadata_for(load_credits(tmp_path), "LI4.webp")
    assert meta["credit"] and meta["licence"], "the ingester refuses a file without both"


def test_a_file_already_there_is_not_fetched_again(tmp_path: Path) -> None:
    seen: list[str] = []
    client = _client(_serving(seen=seen))
    fpi.fetch(tmp_path, ["LI4"], manifest=MANIFEST, client=client, delay_seconds=0)
    again = fpi.fetch(tmp_path, ["LI4"], manifest=MANIFEST, client=client, delay_seconds=0)
    assert again.present == ["LI4"] and again.fetched == [] and len(seen) == 1


@pytest.mark.parametrize(
    ("handler", "why"),
    [
        (_serving(b"<html>blocked</html>"), "not a WebP"),
        (_serving(WEBP + b"\x00" * (fpi.MAX_BYTES + 1)), "larger than"),
        (_serving(status=404), "HTTP 404"),
        (
            lambda request: httpx.Response(
                302, headers={"location": "https://evil.example/x.webp"}
            ),
            "HTTP 302",
        ),
    ],
)
def test_what_is_not_the_expected_image_is_refused(tmp_path: Path, handler, why: str) -> None:
    report = fpi.fetch(
        tmp_path, ["LI4"], manifest=MANIFEST, client=_client(handler), delay_seconds=0
    )
    assert report.fetched == [] and why in report.refused["LI4"]
    assert not (tmp_path / "LI4.webp").exists(), "nothing refused is left on disk"


def test_a_redirect_is_never_followed(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/x.webp"})

    fpi.fetch(tmp_path, ["LI4"], manifest=MANIFEST, client=_client(handler), delay_seconds=0)
    assert seen == [HOST]


def test_a_pinned_file_that_changed_at_the_source_is_refused(tmp_path: Path) -> None:
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["images"]["LI4"]["sha256"] = hashlib.sha256(WEBP).hexdigest()
    ok = fpi.fetch(
        tmp_path / "a", ["LI4"], manifest=manifest, client=_client(_serving()), delay_seconds=0
    )
    assert ok.fetched == ["LI4"]
    other = WEBP + b"\x01"
    changed = fpi.fetch(
        tmp_path / "b", ["LI4"], manifest=manifest, client=_client(_serving(other)), delay_seconds=0
    )
    assert "SHA-256" in changed.refused["LI4"] and not (tmp_path / "b" / "LI4.webp").exists()


def test_a_rejected_diagram_is_never_requested(tmp_path: Path) -> None:
    """A drawing found to be wrong stays in the manifest with its reason, and is refused."""
    manifest = json.loads(json.dumps(MANIFEST))
    manifest["images"]["ST36"]["reject"] = "the drawing names another point"
    seen: list[str] = []
    report = fpi.fetch(
        tmp_path,
        ["LI4", "ST36"],
        manifest=manifest,
        client=_client(_serving(seen=seen)),
        delay_seconds=0,
    )
    assert report.fetched == ["LI4"] and seen == [f"https://{HOST}/img/li4.webp"]
    assert report.refused["ST36"] == "rejected: the drawing names another point"
    assert fpi.accepted(manifest) == ["LI4"], "--all fetches the accepted ones only"
    credits = json.loads((tmp_path / "credits.json").read_text(encoding="utf-8"))
    assert set(credits["files"]) == {"LI4.webp"}

    manifest["images"]["ST36"]["reject"] = " "
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="without a reason"):
        fpi.load_manifest(path)


def test_a_code_outside_the_manifest_stops_before_any_request(tmp_path: Path) -> None:
    seen: list[str] = []
    with pytest.raises(ValueError, match="not in the manifest"):
        fpi.fetch(tmp_path, ["LI4", "XX9"], manifest=MANIFEST, client=_client(_serving(seen=seen)))
    assert seen == []


def test_the_command_lists_and_reports(tmp_path: Path, capsys, monkeypatch) -> None:
    assert fpi.main(["--list"]) == 0
    listed = capsys.readouterr().out
    assert "361 diagrams" in listed and "111 rejected" in listed
    assert fpi.main([str(tmp_path), "XX9"]) == 2
    with pytest.raises(SystemExit):
        fpi.main([str(tmp_path)])  # neither codes nor --all
