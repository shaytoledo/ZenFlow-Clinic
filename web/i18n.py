"""
web/i18n.py
───────────
Minimal JSON-based translation loader.

Usage in routes:
    from web.i18n import get_t
    t = get_t(therapist.get("language", "en"))
    # templates receive `t` in the context; access with {{ t.key }}

Usage in Python code (bot, services):
    from web.i18n import translate
    msg = translate("bot_greeting", "he", name="Maya")
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from string import Formatter

logger = logging.getLogger(__name__)

_LOCALES_DIR = Path(__file__).parent.parent / "locales"
_SUPPORTED = {"en", "he"}
_DEFAULT = "en"

_cache: dict[str, dict[str, str]] = {}


def _load(lang: str) -> dict[str, str]:
    if lang in _cache:
        return _cache[lang]
    path = _LOCALES_DIR / f"{lang}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _cache[lang] = data
        return data
    except Exception as e:
        logger.warning(f"[i18n] Could not load {path}: {e}")
        _cache[lang] = {}
        return {}


class _TranslationProxy:
    """Wraps a translation dict so Jinja2 can access keys as attributes."""

    def __init__(self, data: dict[str, str]) -> None:
        self._data = data

    def __getattr__(self, key: str) -> str:
        return self._data.get(key, key)

    def __getitem__(self, key: str) -> str:
        return self._data.get(key, key)

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key, default)


def get_t(lang: str | None = None) -> _TranslationProxy:
    """Return a translation proxy for the given language code."""
    resolved = lang if lang in _SUPPORTED else _DEFAULT
    # Always ensure fallback keys from English are present
    en = _load(_DEFAULT)
    if resolved == _DEFAULT:
        return _TranslationProxy(en)
    target = _load(resolved)
    merged = {**en, **target}   # target overrides English fallback
    return _TranslationProxy(merged)


def translate(key: str, lang: str | None = None, **kwargs) -> str:
    """Return the translated string, formatting with kwargs if provided."""
    t = get_t(lang)
    raw = t[key]
    if kwargs:
        try:
            return raw.format(**kwargs)
        except (KeyError, ValueError):
            return raw
    return raw


def reload() -> None:
    """Clear the cache — useful in development to pick up locale file changes."""
    _cache.clear()
