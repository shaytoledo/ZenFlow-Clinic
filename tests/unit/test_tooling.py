"""Phase 0.2 — code-quality tooling regression tests.

Pins the existence and key content of the tooling config so nobody silently
drops a linter, un-pins requirements, or reintroduces a plain-http index URL.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict[str, Any]:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_pyproject_has_all_tool_sections() -> None:
    tool = _pyproject()["tool"]
    for section in ("black", "ruff", "mypy", "pytest", "coverage"):
        assert section in tool, f"[tool.{section}] missing from pyproject.toml"
    assert tool["black"]["line-length"] == 100
    assert tool["ruff"]["line-length"] == 100
    assert tool["pytest"]["ini_options"]["asyncio_mode"] == "auto"
    assert "fail_under" in tool["coverage"]["report"]


def test_ruff_selects_required_rule_families() -> None:
    selected = set(_pyproject()["tool"]["ruff"]["lint"]["select"])
    required = {"E", "F", "W", "I", "B", "UP", "S", "ASYNC", "C4", "SIM"}
    assert required <= selected, f"ruff select is missing {required - selected}"


def test_mypy_is_strict_for_interfaces_and_repositories() -> None:
    overrides = _pyproject()["tool"]["mypy"].get("overrides", [])
    strict_modules: set[str] = set()
    for ov in overrides:
        if ov.get("strict"):
            mods = ov["module"]
            strict_modules |= set(mods if isinstance(mods, list) else [mods])
    assert "bot.interfaces.*" in strict_modules
    assert "web.repositories.*" in strict_modules


def test_precommit_config_has_required_hooks() -> None:
    text = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    for hook in (
        "black",
        "ruff",
        "end-of-file-fixer",
        "trailing-whitespace",
        "check-added-large-files",
        "detect-private-key",
        "gitleaks",
        "pytest",
    ):
        assert re.search(rf"id:\s*{re.escape(hook)}\b", text), f"pre-commit hook '{hook}' missing"


def _requirement_lines(name: str) -> list[str]:
    lines = (REPO_ROOT / name).read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]


def test_requirements_are_fully_pinned_and_use_https_index() -> None:
    lines = _requirement_lines("requirements.txt")
    index = [ln for ln in lines if ln.startswith("--index-url")]
    assert index and index[0].startswith("--index-url https://"), "index URL must be https"
    assert not any("http://" in ln for ln in lines), "plain http:// found in requirements.txt"
    specs = [ln for ln in lines if not ln.startswith("-")]
    unpinned = [ln for ln in specs if "==" not in ln]
    assert unpinned == [], f"unpinned requirements: {unpinned}"
    assert any(ln.lower().startswith("cryptography==") for ln in specs), "cryptography not pinned"


def test_dev_requirements_pin_the_toolchain() -> None:
    specs = [ln.split("==")[0].lower() for ln in _requirement_lines("requirements-dev.txt")]
    for tool in ("black", "ruff", "mypy", "pre-commit", "pytest", "pytest-asyncio", "pytest-cov"):
        assert tool in specs, f"{tool} missing from requirements-dev.txt"


def test_env_example_has_no_plain_http_outside_localhost() -> None:
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    # Only the VALUE part of KEY=value lines counts; comments may discuss http:// freely.
    values = [
        ln.split("#", 1)[0].split("=", 1)[1].strip()
        for ln in text.splitlines()
        if "=" in ln and not ln.lstrip().startswith("#")
    ]
    bad = [
        v for v in values if "http://" in v and not re.match(r"http://(localhost|127\.0\.0\.1)", v)
    ]
    assert bad == [], f"plain http:// URLs in .env.example values: {bad}"
    assert "SESSION_SECRET=" in text


def test_lock_headers_are_restamped_idempotently(tmp_path: Path, monkeypatch) -> None:
    """`tasks.py lock` runs pip-compile with --no-header, then puts the HTTPS index back."""
    import sys

    monkeypatch.syspath_prepend(str(REPO_ROOT))
    sys.modules.pop("tasks", None)
    import tasks

    monkeypatch.chdir(tmp_path)
    (tmp_path / "requirements.txt").write_text("pillow==12.3.0\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text(
        "# Locked by pip-compile from requirements-dev.in. old\n--index-url x\n\nblack==1\n",
        encoding="utf-8",
    )
    assert tasks.stamp_lock_headers() == 0
    once = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
    assert tasks.stamp_lock_headers() == 0
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == once
    assert once.splitlines()[1] == "--index-url https://pypi.org/simple"
    assert once.endswith("\n\npillow==12.3.0\n")
    dev = (tmp_path / "requirements-dev.txt").read_text(encoding="utf-8")
    assert dev.count("--index-url") == 1 and dev.endswith("\n\nblack==1\n")
    assert "requirements-dev.in" in dev.splitlines()[0]
