"""
ZenFlow — Telegram bots only (development).

    python startup/run_bots.py

Starts two bots concurrently in the same process:
    Patient bot    (TELEGRAM_TOKEN)       — patients book, cancel, chat
    Therapist bot  (THERAPIST_BOT_TOKEN)  — therapist receives and replies to messages

Logs written to: logs/botLogs.text

Requires Redis to be running:
    redis-server          (Windows: start Redis service or use WSL)
    redis-cli ping        → should return PONG
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.main import main

if __name__ == "__main__":
    main()
