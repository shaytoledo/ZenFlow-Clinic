"""
ZenFlow Clinic Bot — Setup & Run
Run this script to automatically check, update, and start everything.
    python setup_and_run.py
"""
import io
import os
import subprocess
import sys
import time
from pathlib import Path

# Force UTF-8 output on Windows so print() never fails on special chars
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent
VENV = ROOT / ".venv"
PYTHON = VENV / "Scripts" / "python.exe" if sys.platform == "win32" else VENV / "bin" / "python"
PIP = VENV / "Scripts" / "pip.exe"    if sys.platform == "win32" else VENV / "bin" / "pip"
REQ = ROOT / "requirements.txt"
ENV = ROOT / ".env"


def step(msg: str) -> None:
    print(f"\n{'-'*50}\n  {msg}\n{'-'*50}")


def run(cmd: list, check=True, capture=False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


# ── 1. Python version ─────────────────────────────────────────────────────────
step("1 / 6  Checking Python version")
major, minor = sys.version_info[:2]
if major < 3 or minor < 11:
    print(f"  !!  Python 3.11+ required. You have {major}.{minor}")
    sys.exit(1)
print(f"  OK  Python {major}.{minor}")


# ── 2. Virtual environment ────────────────────────────────────────────────────
step("2 / 6  Checking virtual environment")
if not PYTHON.exists():
    print("  Creating .venv ...")
    run([sys.executable, "-m", "venv", str(VENV)])
    print("  OK  Created .venv")
else:
    print("  OK  .venv exists")


# ── 3. Dependencies ───────────────────────────────────────────────────────────
step("3 / 6  Installing / updating dependencies")
result = run([str(PIP), "install", "-r", str(REQ), "--quiet"], capture=True)
if result.returncode == 0:
    print("  OK  Dependencies up to date")
else:
    print(f"  !!  pip error:\n{result.stderr}")
    sys.exit(1)


# ── 4. .env file ──────────────────────────────────────────────────────────────
step("4 / 6  Checking .env configuration")
if not ENV.exists():
    print("  !!  .env file not found!")
    print("  Create a .env file in the project root with:")
    print("      TELEGRAM_TOKEN=<your token>")
    print("      OLLAMA_MODEL=gemma3:latest")
    sys.exit(1)

env_vars = dict(
    line.strip().split("=", 1)
    for line in ENV.read_text().splitlines()
    if "=" in line and not line.strip().startswith("#")
)
token = env_vars.get("TELEGRAM_TOKEN", "")
model = env_vars.get("OLLAMA_MODEL", "gemma3:latest")

if not token or token == "<your token from @BotFather>":
    print("  !!  TELEGRAM_TOKEN is not set in .env")
    sys.exit(1)
print(f"  OK  Token found  |  Model: {model}")


# ── 5. Ollama ─────────────────────────────────────────────────────────────────
step("5 / 6  Checking Ollama")
host = env_vars.get("OLLAMA_HOST", "http://localhost:11434")

import urllib.request, urllib.error  # noqa: E402

def ollama_running() -> bool:
    try:
        urllib.request.urlopen(f"{host}/api/tags", timeout=3)
        return True
    except Exception:
        return False

if not ollama_running():
    print("  Ollama not running — starting 'ollama serve' ...")
    subprocess.Popen(
        ["ollama", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for i in range(10):
        time.sleep(1)
        if ollama_running():
            print("  OK  Ollama started")
            break
    else:
        print("  !!  Could not start Ollama. Install from https://ollama.com")
        sys.exit(1)
else:
    print("  OK  Ollama is running")

# Check / pull model
try:
    import json as _json
    resp = urllib.request.urlopen(f"{host}/api/tags", timeout=5)
    tags = _json.loads(resp.read())
    installed = [m["name"] for m in tags.get("models", [])]
    if not any(model in m for m in installed):
        print(f"  Pulling model '{model}' (this may take a few minutes) ...")
        run(["ollama", "pull", model])
        print(f"  OK  Model '{model}' ready")
    else:
        print(f"  OK  Model '{model}' already installed")
except Exception as e:
    print(f"  ??  Could not verify model: {e}")


# ── 6. Start bot ──────────────────────────────────────────────────────────────
step("6 / 6  Starting ZenFlow Clinic Bot")
print("  Logs  ->  botLogs.text")
print("  Stop with Ctrl+C\n")

os.chdir(ROOT)
os.execv(str(PYTHON), [str(PYTHON), "run.py"])
