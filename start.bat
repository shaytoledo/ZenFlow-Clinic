@echo off
REM ZenFlow Clinic — one-click start (Windows)
REM Double-click this file or run: start.bat
REM
REM What it does:
REM   1. Activates the venv (creates one if missing via launch.py)
REM   2. Starts Redis + Ollama if not running
REM   3. Launches Telegram bots + web dashboard at http://localhost:8000
REM
REM Logs:   logs\botLogs.text  +  logs\webLogs.text
REM Stop:   Ctrl+C in this window

cd /d "%~dp0"
python startup\launch.py
pause
