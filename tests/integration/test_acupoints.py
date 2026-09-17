"""Phase 4.3a — acupoint reference data lives in the database, not in the page's JavaScript.

`acupoints` is created and seeded by init_db() from zenflow/seed_data/acupoints.json;
`python -m zenflow.seed acupoints` applies later changes idempotently; the treatment page reads
it from GET /api/acupoints in the therapist's language.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zenflow import seed

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
WHO_CODE = r"^(LU|LI|ST|SP|HT|SI|BL|KI|PC|TE|GB|LR|GV|CV)\d{1,2}$"


def _count() -> int:
    from bot.db import get_db

    return int(get_db().execute("SELECT COUNT(*) FROM acupoints").fetchone()[0])


# ── the seed ──
def test_the_seed_is_valid_and_uses_who_codes() -> None:
    import re

    points = seed.load_acupoints_seed()
    assert len(points) >= 26
    for point in points:
        assert re.match(WHO_CODE, point["code"]) or point["code"] == "YINTANG", point["code"]
        assert point["name_pinyin"] and point["name_cn"] and point["channel"], point["code"]
        assert point["location"] and point["actions"], point["code"]
        he = point["translations"]["he"]
        assert he["name"] and he["channel"] and he["location"] and he["actions"], point["code"]
        assert point["source"] and point["licence"], point["code"]
    codes = {p["code"] for p in points}
    assert "KD3" not in codes and "KI3" in codes, "Kidney is KI in the WHO standard"
    assert {"LI4", "SP6", "GB21", "BL60", "BL67", "CV3", "CV4"} <= {
        p["code"] for p in points if "pregnancy" in p["contraindications"]
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda pts: pts.append(dict(pts[0])), "used twice"),
        (lambda pts: pts[1]["aliases"].append(pts[0]["code"]), "used twice"),
        (lambda pts: pts[0].update(contraindications=["gout"]), "unknown contraindications"),
        (lambda pts: pts[0].pop("licence"), "lacks"),
        (lambda pts: pts[0].update(code=""), "needs a code"),
    ],
)
def test_a_broken_seed_is_refused(tmp_path: Path, change, message) -> None:
    points = json.loads(seed.ACUPOINTS_SEED.read_text(encoding="utf-8"))["points"]
    change(points)
    broken = tmp_path / "acupoints.json"
    broken.write_text(json.dumps({"version": 1, "points": points}), encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        seed.load_acupoints_seed(broken)


# ── loading ──
def test_a_new_database_is_seeded() -> None:
    assert _count() == len(seed.load_acupoints_seed())


def test_seeding_is_idempotent_and_keeps_other_rows(capsys) -> None:
    from bot.db import get_db

    conn = get_db()
    conn.execute(
        "INSERT INTO acupoints (code, name_pinyin, updated_at) VALUES ('EX1', 'Local', '2026-01-01T00:00:00Z')"
    )
    conn.execute("UPDATE acupoints SET location='edited' WHERE code='LR3'")
    total = _count()

    dry = seed.seed_acupoints(conn, dry_run=True)
    assert dry.updated == ["LR3"] and dry.inserted == []
    assert conn.execute("SELECT location FROM acupoints WHERE code='LR3'").fetchone()[0] == "edited"

    assert seed.main(["acupoints"]) == 0
    assert "1 changed" in capsys.readouterr().out
    assert conn.execute("SELECT location FROM acupoints WHERE code='LR3'").fetchone()[0] != "edited"
    assert _count() == total, "rows outside the seed are kept"

    again = seed.seed_acupoints(conn)
    assert again.inserted == [] and again.updated == []
    assert again.unchanged == len(seed.load_acupoints_seed())


# ── the API ──
async def test_the_page_gets_the_reference_data(make_therapist, login_as, fake_redis) -> None:
    client = await login_as(make_therapist(email="acu-en@example.com", password=PW))
    resp = await client.get("/api/acupoints")
    assert resp.status_code == 200
    data = resp.json()
    assert data["lang"] == "en"
    li4 = data["points"]["LI4"]
    assert (
        li4["name"] == "Hegu"
        and li4["name_cn"] == "合谷"
        and li4["channel_en"] == "Large Intestine"
    )
    assert li4["contraindications"] == ["pregnancy"]
    assert data["aliases"]["KD3"] == "KI3" and data["aliases"]["YIN"] == "YINTANG"
    assert "max-age" in resp.headers["cache-control"]


async def test_hebrew_text_keeps_the_english_channel(make_therapist, login_as, fake_redis) -> None:
    client = await login_as(make_therapist(email="acu-he@example.com", password=PW, language="he"))
    point = (await client.get("/api/acupoints")).json()["points"]["LR3"]
    assert point["channel"] == "כבד" and point["channel_en"] == "Liver"
    assert point["name"] != "Taichong" and point["name_pinyin"] == "Taichong"
    english = (await client.get("/api/acupoints?lang=en")).json()["points"]["LR3"]
    assert english["name"] == "Taichong"
    assert (await client.get("/api/acupoints?lang=fr")).json()["lang"] == "en"


async def test_an_unchanged_answer_is_not_resent(make_therapist, login_as, fake_redis) -> None:
    client = await login_as(make_therapist(email="acu-etag@example.com", password=PW))
    first = await client.get("/api/acupoints")
    again = await client.get("/api/acupoints", headers={"If-None-Match": first.headers["etag"]})
    assert again.status_code == 304 and again.content == b""


async def test_reference_data_needs_a_session(client, fake_redis) -> None:
    assert (await client.get("/api/acupoints")).status_code == 401


def test_the_page_no_longer_ships_the_reference_tables() -> None:
    from tests.integration import treatment_source as ts

    js = ts.javascript()
    assert "Zusanli" not in js and "זוסנלי" not in js, "reference text belongs in the database"
    assert "PREGNANCY_CAUTION" not in js
    assert "/api/acupoints" in (ts.WEB / "static/js/treatment/point-info.js").read_text(
        encoding="utf-8"
    )
