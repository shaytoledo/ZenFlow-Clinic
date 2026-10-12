"""Phase 4.3a — acupoint reference data lives in the database, not in the page's JavaScript.

`acupoints` is created by init_db() and brought up to zenflow/seed_data/acupoints.json at every
start-up (Phase 14.1: the 361 WHO points and the common extra points); the treatment page reads it
from GET /api/acupoints in the therapist's language.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zenflow import seed

pytestmark = pytest.mark.integration

PW = "pw-Test-123"
WHO_CODE = r"^(LU|LI|ST|SP|HT|SI|BL|KI|PC|TE|GB|LR|GV|CV)\d{1,2}$"
#: points per channel in the WHO Standard Acupuncture Point Locations (2008): 361 in all
WHO_POINTS = {
    "LU": 11, "LI": 20, "ST": 45, "SP": 21, "HT": 9, "SI": 19, "BL": 67,
    "KI": 27, "PC": 9, "TE": 23, "GB": 44, "LR": 14, "CV": 24, "GV": 28,
}  # fmt: skip
#: the classic points forbidden in pregnancy, and a sample of the lower-abdominal and sacral ones
AVOIDED_IN_PREGNANCY = {"LI4", "SP6", "GB21", "BL60", "BL67", "ST12", "CV3", "CV4", "ST29", "BL32"}


def _count() -> int:
    from bot.db import get_db

    return int(get_db().execute("SELECT COUNT(*) FROM acupoints").fetchone()[0])


# ── the seed ──
def test_the_seed_is_valid_and_uses_who_codes() -> None:
    import re

    points = seed.load_acupoints_seed()
    for point in points:
        if point["channel"] == "Extra Point":
            assert re.fullmatch(r"[A-Z]{4,}", point["code"]), point["code"]
        else:
            assert re.match(WHO_CODE, point["code"]), point["code"]
        assert point["name_pinyin"] and point["name_cn"] and point["channel"], point["code"]
        assert point["name_en"], point["code"]
        assert point["location"] and point["actions"], point["code"]
        he = point["translations"]["he"]
        assert he["name"] and he["channel"] and he["location"] and he["actions"], point["code"]
        assert point["source"] and point["licence"], point["code"]
    codes = {p["code"] for p in points}
    assert "KD3" not in codes and "KI3" in codes, "Kidney is KI in the WHO standard"
    flagged = {p["code"] for p in points if "pregnancy" in p["contraindications"]}
    assert flagged >= AVOIDED_IN_PREGNANCY


def test_the_seed_holds_every_point_of_the_who_standard() -> None:
    """Phase 14.1: the table had 26 points, so most of what a therapist types had no card."""
    import re

    points = seed.load_acupoints_seed()
    codes = {p["code"] for p in points}
    assert sum(WHO_POINTS.values()) == 361
    for prefix, count in WHO_POINTS.items():
        missing = [f"{prefix}{n}" for n in range(1, count + 1) if f"{prefix}{n}" not in codes]
        assert not missing, missing
        assert f"{prefix}{count + 1}" not in codes, f"{prefix} has only {count} points"
        channels = {p["channel"] for p in points if re.match(rf"^{prefix}\d+$", p["code"])}
        assert len(channels) == 1, f"{prefix}: one channel, got {channels}"
    extras = {p["code"] for p in points if p["channel"] == "Extra Point"}
    assert len(codes) == 361 + len(extras)
    assert {
        "YINTANG",
        "TAIYANG",
        "SISHENCONG",
        "ANMIAN",
        "HUATUOJIAJI",
        "BAXIE",
        "BAFENG",
    } <= extras


def test_codes_and_aliases_are_spelled_the_way_the_page_looks_them_up() -> None:
    """normPointCode() upper-cases and drops spaces and dashes before the lookup."""
    import re

    points = seed.load_acupoints_seed()
    alias_of = {alias: p["code"] for p in points for alias in p["aliases"]}
    for name in [*alias_of, *(p["code"] for p in points)]:
        assert re.fullmatch(r"[A-Z0-9]+", name), name
    spellings = {
        "REN12": "CV12", "RN4": "CV4", "DU20": "GV20", "SJ5": "TE5", "TB5": "TE5", "TH5": "TE5",
        "UB23": "BL23", "LV3": "LR3", "LIV3": "LR3", "P6": "PC6", "KD3": "KI3", "K3": "KI3",
        "H7": "HT7", "EXHN3": "YINTANG", "GV29": "YINTANG", "EXHN5": "TAIYANG", "JIAJI": "HUATUOJIAJI",
    }  # fmt: skip
    assert {alias: alias_of.get(alias) for alias in spellings} == spellings


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


def test_an_existing_database_receives_the_points_a_release_adds() -> None:
    """A database made when the seed was small used to stay small: only an empty table was seeded."""
    import bot.db as dbmod

    conn = dbmod.get_db()
    conn.execute("DELETE FROM acupoints WHERE code NOT IN ('LI4', 'ST36')")
    conn.execute("UPDATE acupoints SET location='stale' WHERE code='LI4'")
    conn.execute(
        "INSERT INTO acupoints (code, name_pinyin, updated_at) VALUES ('LOCAL1', 'Local', '2026-01-01T00:00:00Z')"
    )
    dbmod.init_db()  # the next start-up
    assert _count() == len(seed.load_acupoints_seed()) + 1, "every seed point, and the local row"
    assert conn.execute("SELECT location FROM acupoints WHERE code='LI4'").fetchone()[0] != "stale"
    assert (
        conn.execute("SELECT name_pinyin FROM acupoints WHERE code='SI3'").fetchone()[0] == "Houxi"
    )


def test_a_start_up_with_nothing_to_change_only_reads() -> None:
    """Every process applies the seed when it starts: an unchanged table costs one SELECT, and a
    whole seed a handful of statements — never one per point."""
    from bot.db import get_db

    class Counting:
        def __init__(self, conn) -> None:
            self.conn = conn
            self.verbs: list[str] = []

        def execute(self, sql, params=()):
            self.verbs.append(sql.split()[0].upper())
            return self.conn.execute(sql, params)

        @property
        def in_transaction(self) -> bool:
            return bool(self.conn.in_transaction)

    unchanged = Counting(get_db())
    report = seed.seed_acupoints(unchanged)  # type: ignore[arg-type]
    assert report.inserted == [] and report.updated == []
    assert unchanged.verbs == ["CREATE", "SELECT"]

    get_db().execute("DELETE FROM acupoints")
    empty = Counting(get_db())
    added = seed.seed_acupoints(empty).inserted  # type: ignore[arg-type]
    assert len(added) == len(seed.load_acupoints_seed())
    assert empty.verbs.count("INSERT") <= 10 and empty.verbs.count("COMMIT") == 1
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
    assert len(data["points"]) == len(seed.load_acupoints_seed())
    assert data["points"]["SI3"]["channel_en"] == "Small Intestine"
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
    assert "Zusanli" not in js and "זו סאן לי" not in js, "reference text belongs in the database"
    assert "PREGNANCY_CAUTION" not in js
    assert "/api/acupoints" in (ts.WEB / "static/js/treatment/point-info.js").read_text(
        encoding="utf-8"
    )
