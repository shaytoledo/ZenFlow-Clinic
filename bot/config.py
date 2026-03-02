import os

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
