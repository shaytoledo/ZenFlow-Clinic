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
  5. Starts Ollama (if not already running) and pulls the model if needed
  6. Launches the Telegram bots + the therapist web dashboard in parallel

Individual services (for development):
    python run_bots.py    # Telegram bots only
    python run_web.py     # Web dashboard only  →  http://localhost:8000
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

DIVIDER = "─" * 54


def step(n: int, total: int, msg: str) -> None:
    print(f"\n{DIVIDER}\n  [{n}/{total}]  {msg}\n{DIVIDER}")


def _in_venv() -> bool:
    return sys.prefix != sys.base_prefix or str(VENV) in sys.executable


# ── Re-exec with venv Python if we're not in it yet ───────────────────────────
# (setup_and_run.py used os.execv for the same reason — packages live in the venv)
if not _in_venv() and PYTHON.exists():
    os.execv(str(PYTHON), [str(PYTHON)] + sys.argv)


# ══════════════════════════════════════════════════════════════════════════════
# SETUP PHASE  (only runs when called directly, skipped if venv already active)
# ══════════════════════════════════════════════════════════════════════════════

print(f"\n{DIVIDER}")
print("   ZenFlow Clinic — Starting up")
print(DIVIDER)

# ── 1. Python version ─────────────────────────────────────────────────────────
step(1, 5, "Python version")
major, minor = sys.version_info[:2]
if (major, minor) < (3, 11):
    print(f"  !!  Python 3.11+ required (you have {major}.{minor})")
    sys.exit(1)
print(f"  OK  Python {major}.{minor}")


# ── 2. Virtual environment ────────────────────────────────────────────────────
step(2, 5, "Virtual environment")
if not PYTHON.exists():
    print("  Creating .venv ...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("  OK  Created .venv")
    # Re-exec now that the venv exists so we continue inside it
    os.execv(str(PYTHON), [str(PYTHON)] + sys.argv)
else:
    print("  OK  .venv exists")


# ── 3. Dependencies ───────────────────────────────────────────────────────────
step(3, 5, "Dependencies")
result = subprocess.run([str(PIP), "install", "-r", str(REQ), "--quiet"],
                        capture_output=True, text=True)
if result.returncode != 0:
    print(f"  !!  pip error:\n{result.stderr}")
    sys.exit(1)
print("  OK  Dependencies up to date")


# ── 4. .env file ──────────────────────────────────────────────────────────────
step(4, 5, ".env configuration")
if not ENV.exists():
    print("  !!  .env not found — create one with:")
    print("        TELEGRAM_TOKEN=<your token>")
    print("        OLLAMA_MODEL=gemma3:latest")
    sys.exit(1)

env_vars = {
    k: v for line in ENV.read_text().splitlines()
    if "=" in line and not line.strip().startswith("#")
    for k, v in [line.strip().split("=", 1)]
}
token = env_vars.get("TELEGRAM_TOKEN", "")
model = env_vars.get("OLLAMA_MODEL", "gemma3:latest")

if not token or "<" in token:
    print("  !!  TELEGRAM_TOKEN is not set in .env")
    sys.exit(1)
print(f"  OK  Token found  |  Model: {model}")


# ── 5. Ollama ─────────────────────────────────────────────────────────────────
step(5, 5, "Ollama")
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
    resp = urllib.request.urlopen(f"{host}/api/tags", timeout=5)
    tags = json.loads(resp.read())
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
print("   Bot logs  →  botLogs.text")
print("   Dashboard →  http://localhost:8000")
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
