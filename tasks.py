"""Cross-platform mirror of the Makefile targets (Windows has no `make`).

Usage:  python tasks.py <target> [target ...]
Targets: install fmt lint type test test-fast security all lock hooks
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

SRC = ["bot", "web", "startup", "zenflow", "tests"]
PY = [sys.executable, "-m"]
LOCKS = (("requirements.txt", "requirements.in"), ("requirements-dev.txt", "requirements-dev.in"))
LOCK_HEADER = (
    "# Locked by pip-compile from {src}. Do not edit by hand: make lock / python tasks.py lock\n"
    "--index-url https://pypi.org/simple\n\n"
)


def stamp_lock_headers() -> int:
    """pip-compile runs with --no-header (no machine-specific noise); put our fixed header back,
    including the explicit HTTPS index (Phase 0.2)."""
    for out, src in LOCKS:
        path = Path(out)
        text = path.read_text(encoding="utf-8")
        if text.startswith("# Locked by pip-compile"):
            text = text.split("\n\n", 1)[1]
        path.write_text(LOCK_HEADER.format(src=src) + text, encoding="utf-8")
    return 0


Step = list[str] | Callable[[], int]
TARGETS: dict[str, list[Step]] = {
    "install": [PY + ["pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt"]],
    "fmt": [PY + ["black", *SRC], PY + ["ruff", "check", "--fix", *SRC]],
    "lint": [PY + ["black", "--check", *SRC], PY + ["ruff", "check", *SRC]],
    "type": [PY + ["mypy"]],
    "test": [PY + ["pytest", "--cov", "--cov-report=term-missing"]],
    "test-fast": [PY + ["pytest", "-m", "not slow", "-x", "-q"]],
    "security": [
        # Fail only on HIGH severity + HIGH confidence — matches the CI gate (9.10) and lets
        # `security` / `all` exit 0 despite the pre-existing LOW/MEDIUM baseline (debt T1).
        PY
        + [
            "bandit",
            "-q",
            "-r",
            "bot",
            "web",
            "zenflow",
            "startup",
            "-x",
            "tests",
            "--severity-level",
            "high",
            "--confidence-level",
            "high",
        ],
        PY + ["pytest", "tests/security", "-q"],
    ],
    "lock": [
        PY
        + [
            "piptools",
            "compile",
            "--strip-extras",
            "--no-header",
            "-o",
            "requirements.txt",
            "requirements.in",
        ],
        PY
        + [
            "piptools",
            "compile",
            "--strip-extras",
            "--no-header",
            "-o",
            "requirements-dev.txt",
            "requirements-dev.in",
        ],
        stamp_lock_headers,
    ],
    "hooks": [PY + ["pre_commit", "install"]],
}
TARGETS["all"] = TARGETS["lint"] + TARGETS["type"] + TARGETS["test"] + TARGETS["security"]


def main(argv: list[str]) -> int:
    if not argv or any(a not in TARGETS for a in argv):
        print(__doc__)
        return 2
    for target in argv:
        for step in TARGETS[target]:
            if callable(step):
                print("+", step.__name__, flush=True)
                rc = step()
            else:
                print("+", " ".join(step[2:] if step[:2] == PY else step), flush=True)
                rc = subprocess.run(step, check=False).returncode  # noqa: S603
            if rc:
                return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
