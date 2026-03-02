"""
ZenFlow Clinic — Unified Launcher
──────────────────────────────────
Starts all services in parallel:
  [1] Telegram bots  (patient + therapist)
  [2] Therapist web dashboard  → http://localhost:8000

How to run all the system?
Usage:
    python start.py

To start individual services:
    python run.py         # Telegram bots only
    python run_web.py     # Web dashboard only
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
PYTHON = sys.executable

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DIVIDER = "=" * 54


def main() -> None:
    print(f"\n{DIVIDER}")
    print("   ZenFlow Clinic — All Services")
    print(DIVIDER)
    print("   [1] Telegram bots  (patient + therapist)")
    print("   [2] Web dashboard  → http://localhost:8000")
    print("   Press Ctrl+C to stop everything")
    print(f"{DIVIDER}\n")

    processes: list[tuple[str, subprocess.Popen]] = []

    try:
        # ── 1. Telegram bots (patient + therapist run in the same process) ──
        bot_proc = subprocess.Popen([PYTHON, "run.py"], cwd=ROOT)
        processes.append(("Telegram bots", bot_proc))
        print(f"   OK  Telegram bots started  (PID {bot_proc.pid})")

        # Brief delay so bot logs appear before web server logs
        time.sleep(2)

        # ── 2. Therapist web dashboard ────────────────────────────────────
        web_proc = subprocess.Popen([PYTHON, "run_web.py"], cwd=ROOT)
        processes.append(("Web dashboard", web_proc))
        print(f"   OK  Web dashboard started  (PID {web_proc.pid})")

        print(f"\n   All services running.  Ctrl+C to stop.\n")

        # ── Monitor — restart notice if a service exits unexpectedly ──────
        while True:
            for name, proc in processes:
                code = proc.poll()
                if code is not None:
                    print(f"\n   !! {name} exited unexpectedly (code {code})")
                    raise SystemExit(1)
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\n   Shutting down all services...")
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


if __name__ == "__main__":
    main()
