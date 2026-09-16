"""
bot/config.py — module-level constants for the bot and web layers.

Since Phase 0.4 every value comes from `zenflow.settings` (the ONE place env vars are read).
The names below are kept because ~20 modules import them; new code should call
`zenflow.settings.get_settings()` directly.
"""

import os

from zenflow.settings import get_settings

_s = get_settings()  # raises SettingsError → the process refuses to boot on a bad config

ENV = _s.env
TELEGRAM_TOKEN = _s.telegram_token or None  # legacy: None when unset
OLLAMA_MODEL = _s.ollama_model
OLLAMA_HOST = _s.ollama_host
USE_AI = _s.ai_provider

THERAPIST_BOT_TOKEN = _s.therapist_bot_token

_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_base, "data")

GOOGLE_CLIENT_ID = _s.google_client_id
GOOGLE_CLIENT_SECRET = _s.google_client_secret
GOOGLE_REDIRECT_URI = _s.google_redirect_uri
GOOGLE_REG_REDIRECT_URI = _s.google_reg_redirect_uri
GOOGLE_GMAIL_REDIRECT_URI = _s.google_gmail_redirect_uri

REDIS_URL = _s.redis_url
SESSION_SECRET = _s.session_secret

# Initialize SQLite DB (creates tables, seeds from JSON if empty)
from bot.db import init_db as _init_db

_init_db()


def _load_therapists_from_db() -> list[dict]:
    from bot.db import get_db

    conn = get_db()
    rows = conn.execute("SELECT * FROM therapists").fetchall()
    result = [dict(row) for row in rows]
    for t in result:
        t["active"] = bool(t.get("active"))
    return result


# Therapist registry — loaded from SQLite
THERAPISTS: list[dict] = _load_therapists_from_db()
# Lookup by telegram_id (int) → therapist dict (exclude telegram_id=0)
THERAPIST_MAP: dict[int, dict] = {
    t["telegram_id"]: t for t in THERAPISTS if t.get("active") and t.get("telegram_id")
}
# Lookup by therapist id string ("t1", …) → therapist dict
THERAPIST_BY_ID: dict[str, dict] = {t["id"]: t for t in THERAPISTS if t.get("active")}


def reload_therapists() -> None:
    """Refresh the in-memory therapist registry from SQLite.

    Mutates the three containers in place. Handler modules hold direct references to them
    (`from bot.config import THERAPIST_BY_ID`), so rebinding the globals would leave those modules
    reading a registry frozen at import time — a therapist deactivated in the dashboard would still
    receive patient messages (BOT_AUDIT B5).
    """
    rows = _load_therapists_from_db()
    THERAPISTS.clear()
    THERAPISTS.extend(rows)
    THERAPIST_MAP.clear()
    THERAPIST_MAP.update(
        {t["telegram_id"]: t for t in rows if t.get("active") and t.get("telegram_id")}
    )
    THERAPIST_BY_ID.clear()
    THERAPIST_BY_ID.update({t["id"]: t for t in rows if t.get("active")})
