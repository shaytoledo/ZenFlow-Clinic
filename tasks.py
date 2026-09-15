"""Cross-platform mirror of the Makefile targets (Windows has no `make`).

Usage:  python tasks.py <target> [target ...]
Targets: install fmt lint type test test-fast security all lock hooks
"""

from __future__ import annotations

import subprocess
import sys

SRC = ["bot", "web", "startup", "tests"]
PY = [sys.executable, "-m"]

TARGETS: dict[str, list[list[str]]] = {
    "install": [PY + ["pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt"]],
    "fmt": [PY + ["black", *SRC], PY + ["ruff", "check", "--fix", *SRC]],
    "lint": [PY + ["black", "--check", *SRC], PY + ["ruff", "check", *SRC]],
    "type": [PY + ["mypy"]],
    "test": [PY + ["pytest", "--cov", "--cov-report=term-missing"]],
    "test-fast": [PY + ["pytest", "-m", "not slow", "-x", "-q"]],
    "security": [
        PY + ["bandit", "-q", "-r", "bot", "web", "startup", "-x", "tests"],
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
    ],
    "hooks": [PY + ["pre_commit", "install"]],
}
TARGETS["all"] = TARGETS["lint"] + TARGETS["type"] + TARGETS["test"] + TARGETS["security"]


def main(argv: list[str]) -> int:
    if not argv or any(a not in TARGETS for a in argv):
        print(__doc__)
        return 2
    for target in argv:
        for cmd in TARGETS[target]:
            print("+", " ".join(cmd[2:] if cmd[:2] == PY else cmd), flush=True)
            rc = subprocess.run(cmd, check=False).returncode  # noqa: S603
            if rc:
                return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
