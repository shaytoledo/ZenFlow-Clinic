import os

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:latest")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
USE_AI = os.getenv("USE_AI", "ollama")

THERAPIST_BOT_TOKEN = os.getenv("THERAPIST_BOT_TOKEN", "")

_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_base, "data")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
GOOGLE_REG_REDIRECT_URI = os.getenv("GOOGLE_REG_REDIRECT_URI", "http://localhost:8000/register/google/callback")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_SECRET = os.getenv("SESSION_SECRET", "changeme-set-in-dotenv")

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
    t["telegram_id"]: t
    for t in THERAPISTS
    if t.get("active") and t.get("telegram_id")
}
# Lookup by therapist id string ("t1", …) → therapist dict
THERAPIST_BY_ID: dict[str, dict] = {t["id"]: t for t in THERAPISTS if t.get("active")}

