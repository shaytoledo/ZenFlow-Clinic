"""Phase 11.1 (UNIT) — i18n resolution and locale-file completeness.

The clinic is bilingual (English + Hebrew, RTL). `web/i18n.py` resolves a language code to a string,
falling back to English key-by-key, and never raising when a key or an interpolation argument is
missing (a raise here would surface as a broken page or bot message). These pure-logic tests pin that
behaviour with controlled data, then assert the real `locales/en.json` and `locales/he.json` are in
full parity — the guard that stops a new English string shipping without its Hebrew translation (or a
placeholder drifting between the two), which would silently render English text in a Hebrew UI or drop
an interpolated value.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import web.i18n as i18n
from web.i18n import get_t, translate

_LOCALES_DIR = Path(__file__).parent.parent.parent / "locales"

# Controlled data — HE deliberately omits `only_en` (must fall back) and overrides `shared`.
_EN = {
    "greeting": "Hello",
    "only_en": "English only",
    "with_name": "Hi {name}",
    "shared": "EN shared",
}
_HE = {
    "greeting": "שלום",
    "with_name": "שלום {name}",
    "shared": "HE shared",
}


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seed the translation cache with controlled dicts so resolution is tested without disk I/O."""
    monkeypatch.setattr(i18n, "_cache", {"en": dict(_EN), "he": dict(_HE)})


def test_english_returns_english(seeded: None) -> None:
    assert get_t("en").greeting == "Hello"


def test_hebrew_overrides_english(seeded: None) -> None:
    assert get_t("he").greeting == "שלום"
    assert get_t("he").shared == "HE shared"


def test_hebrew_falls_back_to_english_key_by_key(seeded: None) -> None:
    # `only_en` has no Hebrew translation — the English string must show, not the raw key.
    assert get_t("he").only_en == "English only"


@pytest.mark.parametrize("lang", ["fr", "zz", "", "EN", None])
def test_an_unknown_or_missing_language_falls_back_to_default(seeded: None, lang) -> None:
    assert get_t(lang).greeting == "Hello"


def test_a_missing_key_returns_the_key_not_a_crash(seeded: None) -> None:
    t = get_t("en")
    assert t.does_not_exist == "does_not_exist"  # __getattr__
    assert t["does_not_exist"] == "does_not_exist"  # __getitem__
    assert t.get("does_not_exist", "fallback") == "fallback"  # .get(default)
    assert t.get("does_not_exist") == ""  # .get default is ""


def test_translate_interpolates_kwargs(seeded: None) -> None:
    assert translate("with_name", "he", name="Maya") == "שלום Maya"
    assert translate("with_name", "en", name="Sara") == "Hi Sara"


def test_translate_with_a_missing_placeholder_arg_returns_raw_and_does_not_raise(
    seeded: None,
) -> None:
    # A caller that forgets `name=` must get the un-interpolated string, never a KeyError 500.
    assert translate("with_name", "he") == "שלום {name}"


def test_translate_ignores_extra_kwargs_when_there_is_no_placeholder(seeded: None) -> None:
    assert translate("greeting", "en", unused="x") == "Hello"


def test_translate_of_an_unknown_key_returns_the_key(seeded: None) -> None:
    assert translate("no_such_key", "he") == "no_such_key"


def test_reload_clears_the_cache(seeded: None) -> None:
    assert i18n._cache != {}
    i18n.reload()
    assert i18n._cache == {}


# ── Real locale files ────────────────────────────────────────────────────────────


def _load_real(lang: str) -> dict:
    return json.loads((_LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))


def test_the_real_locale_files_load_and_translate_end_to_end() -> None:
    i18n.reload()  # drop any seeded/cached data so the disk path is exercised
    en = _load_real("en")
    # A real key resolves to an actual Hebrew string (not the key, not the English text).
    sample = next(iter(en))
    assert translate(sample, "en") == en[sample]
    i18n.reload()


def test_english_and_hebrew_have_identical_key_sets() -> None:
    en, he = _load_real("en"), _load_real("he")
    missing_in_he = set(en) - set(he)
    orphan_in_he = set(he) - set(en)
    assert not missing_in_he, f"English keys with no Hebrew translation: {sorted(missing_in_he)}"
    assert not orphan_in_he, f"Hebrew keys not present in English: {sorted(orphan_in_he)}"


def test_every_placeholder_matches_between_the_two_languages() -> None:
    en, he = _load_real("en"), _load_real("he")
    mismatches = {
        k: (sorted(re.findall(r"{(\w+)}", en[k])), sorted(re.findall(r"{(\w+)}", he[k])))
        for k in set(en) & set(he)
        if set(re.findall(r"{(\w+)}", en[k])) != set(re.findall(r"{(\w+)}", he[k]))
    }
    assert not mismatches, f"placeholder drift between en/he: {mismatches}"


@pytest.mark.parametrize("lang", ["en", "he"])
def test_every_locale_value_is_a_string(lang: str) -> None:
    data = _load_real(lang)
    bad = {k: type(v).__name__ for k, v in data.items() if not isinstance(v, str)}
    assert not bad, f"non-string locale values would break .format()/proxy access: {bad}"
