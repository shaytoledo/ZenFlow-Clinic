#!/usr/bin/env bash
# ZenFlow Clinic — one-click start (macOS/Linux/Git Bash)
# Run from project root:   ./start.sh
#
# What it does:
#   1. Activates the venv (creates one if missing via launch.py)
#   2. Starts Redis + Ollama if not running
#   3. Launches Telegram bots + web dashboard at http://localhost:8000
#
# Logs:   logs/botLogs.text  +  logs/webLogs.text
# Stop:   Ctrl+C in this window
set -e
cd "$(dirname "$0")"
python startup/launch.py
