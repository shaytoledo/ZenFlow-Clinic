"""
ZenFlow Clinic — Launch everything
===================================
Run this one file to set up and start the whole system:

    python launch.py

What it does:
  1. Checks Python version (3.11+)
  2. Creates / updates the virtual environment
  3. Installs / updates dependencies from requirements.txt
  4. Validates the .env file
  5. Checks Redis is running  (required for cache, relay sessions, intake history)
  6. Starts Ollama (if not already running) and pulls the model if needed
  7. Launches the Telegram bots + the therapist web dashboard in parallel

Individual services (for development):
    python run_bots.py    # Telegram bots only
    python run_web.py     # Web dashboard only  →  http://localhost:8000

Services started:
    Telegram patient bot  — patients book, cancel, and chat
    Telegram therapist bot — therapist receives and replies to patient messages
    Web dashboard         — http://localhost:8000
      /               dashboard (today's schedule + stats)
      /schedule       FullCalendar availability manager
      /patients       patient list + session history
      /messages       active relay conversations
      /settings       Google Calendar + therapist registration
      /register       therapist self-registration (public, no login needed)
      /treatment/...  per-session treatment notes

Prerequisites:
    Redis   — install once:  winget install Redis.Redis
              launch.py starts it automatically on each run (not auto-start on boot)
    Ollama  — install from https://ollama.com  (auto-started by this script)
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── Force UTF-8 output on Windows ─────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT   = Path(__file__).parent
VENV   = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe" if sys.platform == "win32" else VENV / "bin" / "python"
PIP    = VENV / "Scripts" / "pip.exe"    if sys.platform == "win32" else VENV / "bin" / "pip"
REQ    = ROOT / "requirements.txt"
ENV    = ROOT / ".env"

TOTAL   = 6
DIVIDER = "─" * 54


def step(n: int, msg: str) -> None:
    print(f"\n{DIVIDER}\n  [{n}/{TOTAL}]  {msg}\n{DIVIDER}")


def _in_venv() -> bool:
    return sys.prefix != sys.base_prefix or str(VENV) in sys.executable


# ── Re-exec with venv Python if we're not in it yet ───────────────────────────
if not _in_venv() and PYTHON.exists():
    os.execv(str(PYTHON), [str(PYTHON)] + sys.argv)


# ══════════════════════════════════════════════════════════════════════════════
# SETUP PHASE
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n{DIVIDER}")
print("   ZenFlow Clinic — Starting up")
print(DIVIDER)

# ── 1. Python version ─────────────────────────────────────────────────────────
step(1, "Python version")
major, minor = sys.version_info[:2]
if (major, minor) < (3, 11):
    print(f"  !!  Python 3.11+ required (you have {major}.{minor})")
    sys.exit(1)
print(f"  OK  Python {major}.{minor}")


# ── 2. Virtual environment ────────────────────────────────────────────────────
step(2, "Virtual environment")
if not PYTHON.exists():
    print("  Creating .venv ...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("  OK  Created .venv")
    os.execv(str(PYTHON), [str(PYTHON)] + sys.argv)
else:
    print("  OK  .venv exists")


# ── 3. Dependencies ───────────────────────────────────────────────────────────
step(3, "Dependencies")
result = subprocess.run(
    [str(PIP), "install", "-r", str(REQ), "--quiet"],
    capture_output=True, text=True,
)
if result.returncode != 0:
    print(f"  !!  pip error:\n{result.stderr}")
    sys.exit(1)
print("  OK  Dependencies up to date")


# ── 4. .env file ──────────────────────────────────────────────────────────────
step(4, ".env configuration")
if not ENV.exists():
    print("  !!  .env not found — create one based on the template in CLAUDE.md")
    sys.exit(1)

env_vars = {
    k: v for line in ENV.read_text().splitlines()
    if "=" in line and not line.strip().startswith("#")
    for k, v in [line.strip().split("=", 1)]
}
token         = env_vars.get("TELEGRAM_TOKEN", "")
therapist_tok = env_vars.get("THERAPIST_BOT_TOKEN", "")
model         = env_vars.get("OLLAMA_MODEL", "gemma3:latest")
redis_url     = env_vars.get("REDIS_URL", "redis://localhost:6379/0")

if not token or "<" in token:
    print("  !!  TELEGRAM_TOKEN is not set in .env")
    sys.exit(1)
if not therapist_tok:
    print("  ??  THERAPIST_BOT_TOKEN not set — therapist bot will be disabled")
else:
    print("  OK  Both bot tokens found")
print(f"  OK  Ollama model: {model}")
print(f"  OK  Redis URL:    {redis_url}")


# ── 5. Redis ──────────────────────────────────────────────────────────────────
step(5, "Redis")

import socket as _socket

_REDIS_PATHS = [
    r"C:\Program Files\Redis\redis-server.exe",
    r"C:\Program Files (x86)\Redis\redis-server.exe",
]


def _redis_running(url: str) -> bool:
    """Try a raw socket connection to the Redis port."""
    try:
        parts = url.replace("redis://", "").split("/")[0].split(":")
        host  = parts[0] or "localhost"
        port  = int(parts[1]) if len(parts) > 1 else 6379
        with _socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def _start_redis(url: str) -> bool:
    """Try to start Redis — via Windows service first, then direct binary."""
    # 1. Try Windows service (installed via winget)
    try:
        result = subprocess.run(
            ["sc", "start", "Redis"],
            capture_output=True, text=True,
        )
        if result.returncode in (0, 1056):  # 1056 = already running
            for _ in range(8):
                time.sleep(1)
                if _redis_running(url):
                    return True
    except Exception:
        pass

    # 2. Try launching redis-server.exe directly
    for path in _REDIS_PATHS:
        if Path(path).exists():
            subprocess.Popen(
                [path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(8):
                time.sleep(1)
                if _redis_running(url):
                    return True

    return False


if _redis_running(redis_url):
    print(f"  OK  Redis already running at {redis_url}")
else:
    print("  Starting Redis...")
    if _start_redis(redis_url):
        print(f"  OK  Redis started at {redis_url}")
    else:
        print("  !!  Could not start Redis.")
        print(f"      URL configured: {redis_url}")
        print()
        print("      Install Redis for Windows:")
        print("        winget install Redis.Redis")
        sys.exit(1)


# ── 6. Ollama ─────────────────────────────────────────────────────────────────
step(6, "Ollama")
host = env_vars.get("OLLAMA_HOST", "http://localhost:11434")


def ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=3)
        return True
    except Exception:
        return False


if not ollama_running():
    print("  Starting ollama serve ...")
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(10):
        time.sleep(1)
        if ollama_running():
            print("  OK  Ollama started")
            break
    else:
        print("  !!  Could not start Ollama — install from https://ollama.com")
        sys.exit(1)
else:
    print("  OK  Ollama is running")

try:
    resp      = urllib.request.urlopen(f"{host}/api/tags", timeout=5)
    tags      = json.loads(resp.read())
    installed = [m["name"] for m in tags.get("models", [])]
    if not any(model in m for m in installed):
        print(f"  Pulling model '{model}' (may take a few minutes) ...")
        subprocess.run(["ollama", "pull", model], check=True)
        print(f"  OK  Model '{model}' ready")
    else:
        print(f"  OK  Model '{model}' installed")
except Exception as e:
    print(f"  ??  Could not verify model: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# LAUNCH PHASE
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n{DIVIDER}")
print("   Launching services")
print(f"{DIVIDER}")
print("   Bot logs   →  botLogs.text")
print("   Dashboard  →  http://localhost:8000")
print("   Register   →  http://localhost:8000/register")
print("   Stop with Ctrl+C\n")

os.chdir(ROOT)
processes: list[tuple[str, subprocess.Popen]] = []

try:
    bot_proc = subprocess.Popen([str(PYTHON), "run_bots.py"], cwd=ROOT)
    processes.append(("Telegram bots", bot_proc))
    print(f"   OK  Telegram bots    (PID {bot_proc.pid})")

    time.sleep(2)

    web_proc = subprocess.Popen([str(PYTHON), "run_web.py"], cwd=ROOT)
    processes.append(("Web dashboard", web_proc))
    print(f"   OK  Web dashboard    (PID {web_proc.pid})")

    print(f"\n   All services running.  Press Ctrl+C to stop.\n")

    while True:
        for name, proc in processes:
            code = proc.poll()
            if code is not None:
                print(f"\n   !!  {name} exited unexpectedly (code {code})")
                raise SystemExit(1)
        time.sleep(1)

except KeyboardInterrupt:
    print("\n\n   Shutting down...")
finally:
    for name, proc in processes:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            print(f"   Stopped: {name}")
    print("\n   Done.\n")
