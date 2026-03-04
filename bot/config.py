import json as _json
import os
import pathlib as _pl

from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3:latest")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
USE_AI = os.getenv("USE_AI", "ollama")

_therapist_id = os.getenv("THERAPIST_TELEGRAM_ID", "0")
THERAPIST_TELEGRAM_ID = int(_therapist_id) if _therapist_id.isdigit() else 0

THERAPIST_BOT_TOKEN = os.getenv("THERAPIST_BOT_TOKEN", "")

_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_base, "data", "appointments")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/callback")
GOOGLE_REG_REDIRECT_URI = os.getenv("GOOGLE_REG_REDIRECT_URI", "http://localhost:8000/register/google/callback")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_SECRET = os.getenv("SESSION_SECRET", "changeme-set-in-dotenv")

# Therapist registry — loaded from data/therapists.json
_tpath = _pl.Path(_base) / "data" / "therapists.json"
THERAPISTS: list[dict] = _json.loads(_tpath.read_text(encoding="utf-8")) if _tpath.exists() else []
# Lookup by telegram_id (int) → therapist dict
THERAPIST_MAP: dict[int, dict] = {t["telegram_id"]: t for t in THERAPISTS if t.get("active")}
# Lookup by therapist id string ("t1", …) → therapist dict
THERAPIST_BY_ID: dict[str, dict] = {t["id"]: t for t in THERAPISTS if t.get("active")}

# ── One-time migration: google_token.json → google_token_{id}.json ─────────────
# The legacy file belongs to the single originally-active therapist.
# Once copied, is_authenticated() and _resolve_token_file() find the specific file.
_legacy_token = _pl.Path(_base) / "data" / "google_token.json"
if _legacy_token.exists():
    _active = [t for t in THERAPISTS if t.get("active")]
    if len(_active) == 1:                          # only safe to migrate in single-therapist setup
        _specific = _pl.Path(_base) / "data" / f"google_token_{_active[0]['id']}.json"
        if not _specific.exists():
            import shutil as _shutil
            _specific.parent.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(str(_legacy_token), str(_specific))
