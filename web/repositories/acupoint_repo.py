"""
web/repositories/acupoint_repo.py
──────────────────────────────────
Read access to the `acupoints` reference table (Phase 4.3a; schema and seed in zenflow/seed.py).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any

#: languages the reference data carries translations for (English is the base row)
LANGUAGES = ("en", "he")
_JSON_DEFAULTS: dict[str, Any] = {"aliases": [], "contraindications": [], "translations": {}}


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def _decode(row: Mapping[str, Any]) -> dict[str, Any]:
    point = dict(row)
    for column, default in _JSON_DEFAULTS.items():
        value = point.get(column, default)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = default
        point[column] = value if isinstance(value, type(default)) else default
    return point


def all_points() -> list[dict[str, Any]]:
    """Every point, JSON columns decoded, ordered by code."""
    rows = _conn().execute("SELECT * FROM acupoints ORDER BY code").fetchall()
    return [_decode(r) for r in rows]


def images_by_code() -> dict[str, list[dict[str, Any]]]:
    """Stored image rows per point code, primary first (Phase 4.3b)."""
    rows = (
        _conn()
        .execute("""SELECT point_code, storage_key, thumb_key, kind, width, height, credit, licence,
                      licence_url, source_url, is_primary
               FROM acupoint_images ORDER BY point_code, is_primary DESC, id""")
        .fetchall()
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["point_code"]), []).append(dict(row))
    return grouped


def reference(
    points: Iterable[Mapping[str, Any]],
    lang: str,
    images: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """The page's view of the reference data: text in `lang` (falling back to English), the
    English channel for colour themes, an alias → code map, and each point's `images` (already
    turned into links by the caller; empty when images are off)."""
    lang = lang if lang in LANGUAGES else "en"
    by_code: dict[str, dict[str, Any]] = {}
    aliases: dict[str, str] = {}
    for raw in points:
        point = _decode(raw)
        text = point["translations"].get(lang, {}) if lang != "en" else {}
        by_code[point["code"]] = {
            "code": point["code"],
            "name": text.get("name") or point["name_pinyin"],
            "name_pinyin": point["name_pinyin"],
            "name_cn": point["name_cn"],
            "name_en": point["name_en"],
            "channel": text.get("channel") or point["channel"],
            "channel_en": point["channel"],
            "location": text.get("location") or point["location"],
            "actions": text.get("actions") or point["actions"],
            "needle_depth": point["needle_depth"],
            "needle_angle": point["needle_angle"],
            "contraindications": list(point["contraindications"]),
            "source": point["source"],
            "licence": point["licence"],
            "images": list((images or {}).get(point["code"], [])),
        }
        for alias in point["aliases"]:
            aliases[str(alias)] = point["code"]
    return {"lang": lang, "points": by_code, "aliases": aliases}
