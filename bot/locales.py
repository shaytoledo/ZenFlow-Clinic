"""
bot/locales.py
──────────────
Thin translation bridge for the Telegram bot.

Usage:
    from bot.locales import get_lang, t

    lang = get_lang(therapist_id)          # "en" or "he" from DB
    msg  = t("bot_welcome", lang, name="שרה")
"""
from __future__ import annotations


def get_lang(therapist_id: str | None) -> str:
    """Return the therapist's stored language preference ('en' or 'he').

    Reads live from SQLite on every call so a language change in the web
    dashboard is reflected immediately in the next bot message.
    """
    if not therapist_id:
        return "en"
    try:
        from bot.db import get_db
        row = get_db().execute(
            "SELECT language FROM therapists WHERE id=?", (therapist_id,)
        ).fetchone()
        return (dict(row).get("language") if row else None) or "en"
    except Exception:
        return "en"


def t(key: str, lang: str = "en", **kwargs) -> str:
    """Translate *key* to *lang*, interpolating any keyword arguments."""
    from web.i18n import translate
    return translate(key, lang, **kwargs)
