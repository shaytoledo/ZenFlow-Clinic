"""Phase 0.1 — repo hygiene regression tests.

These guard against re-committing files that must never be tracked:
Redis snapshots, SQLite databases, runtime logs, .env files and the
per-developer Claude permissions file (which has leaked bot tokens before).
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_TRACKED_PATTERNS = [
    "*.rdb",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    ".env",
    "*/.env",
    "logs/*.text",
    "logs/*.out",
    "logs/*.log",
    ".claude/settings.local.json",
    "data/google_tokens/*",
]

REQUIRED_GITIGNORE_RULES = [
    "*.rdb",
    ".claude/settings.local.json",
    "logs/*.text",
    ".env",
    "data/",
]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_no_forbidden_files_are_tracked() -> None:
    tracked = _tracked_files()
    offenders = sorted(
        f for f in tracked if any(fnmatch.fnmatch(f, pat) for pat in FORBIDDEN_TRACKED_PATTERNS)
    )
    assert offenders == [], f"forbidden files are tracked in git: {offenders}"


def test_gitignore_contains_required_rules() -> None:
    rules = {
        line.strip()
        for line in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    missing = [r for r in REQUIRED_GITIGNORE_RULES if r not in rules]
    assert missing == [], f".gitignore is missing rules: {missing}"
