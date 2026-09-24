"""Phase 9.10 — supply-chain scanning wired into pre-commit and CI.

These assert the config exists and names the right tools; the tools themselves run in CI (GitHub's
runner) and in pre-commit's own hook envs, not in this test process.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _text(rel: str) -> str:
    path = ROOT / rel
    assert path.exists(), f"missing {rel}"
    return path.read_text(encoding="utf-8")


def _yaml(rel: str) -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(_text(rel))


def test_dependabot_covers_pip_and_github_actions() -> None:
    cfg = _yaml(".github/dependabot.yml")
    ecosystems = {u["package-ecosystem"] for u in cfg["updates"]}
    assert {"pip", "github-actions"} <= ecosystems, f"dependabot ecosystems: {ecosystems}"


def test_ci_workflow_is_valid_yaml_with_jobs() -> None:
    cfg = _yaml(".github/workflows/ci.yml")
    assert isinstance(cfg, dict) and cfg.get("jobs"), "the CI workflow needs at least one job"


def test_ci_runs_the_gate_and_the_scanners() -> None:
    text = _text(".github/workflows/ci.yml")
    for tool in ("black", "ruff", "mypy", "pytest", "bandit", "pip-audit", "gitleaks"):
        assert tool in text, f"CI should run {tool}"


def test_ci_bandit_fails_only_on_high() -> None:
    text = _text(".github/workflows/ci.yml")
    assert (
        "severity-level high" in text
    ), "bandit must fail the build on HIGH (not the LOW baseline)"


def test_pre_commit_wires_the_local_scanners() -> None:
    text = _text(".pre-commit-config.yaml")
    for tool in ("gitleaks", "bandit", "pip-audit"):
        assert tool in text, f"pre-commit should include {tool}"
