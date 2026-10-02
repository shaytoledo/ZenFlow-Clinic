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

# The therapist registry is `bot.therapists` (read from the database on every call, 12.2.4).
