"""
ZenFlow — Web dashboard only (development).

    python startup/run_web.py

Opens at: http://localhost:8000

Routes:
    /               Dashboard — today's schedule + stats
    /schedule       FullCalendar availability manager (Google Calendar)
    /patients       Patient list + session history
    /messages       Active relay conversations
    /sessions       Session history with sorting (Name / Date / Last Access)
    /settings       Google Calendar auth + therapist registration link
    /register       Therapist self-registration (public, no login needed)
    /treatment/...  Per-session treatment notes (tongue, pulse, points, AI diagnosis)

Requires Redis to be running:
    redis-server          (Windows: start Redis service or use WSL)
    redis-cli ping        → should return PONG
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

# startup/run_web.py is one level inside the project root — logs folder is at root
_LOG_FILE = str(Path(__file__).resolve().parent.parent / "logs" / "webLogs.text")

_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": False,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
            "use_colors": False,
        },
        "file": {
            "format": "%(asctime)s %(levelname)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
        "file": {
            "formatter": "file",
            "class": "logging.FileHandler",
            "filename": _LOG_FILE,
            "mode": "a",
            "encoding": "utf-8",
        },
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["default", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {
            "handlers": ["access", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}

if __name__ == "__main__":
    uvicorn.run("web.app:app", host="0.0.0.0", port=8000, reload=True, log_config=_LOG_CONFIG)
