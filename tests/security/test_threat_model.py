"""Phase 10.1/10.4 — the STRIDE threat model exists and the `security` target is HIGH-gated.

`docs/THREAT_MODEL.md` enumerates the attack scenarios A1–A14; each must be present (so the doc stays
in step with the plan), and the repeatable `python tasks.py security` target must fail only on HIGH
bandit findings — otherwise the pre-existing LOW/MEDIUM baseline (debt T1) would keep it, and
`tasks.py all`, red.
"""

from __future__ import annotations

import pathlib

import pytest

pytestmark = pytest.mark.security

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCENARIOS = [f"A{i}" for i in range(1, 15)]  # A1..A14


def test_threat_model_names_every_attack_scenario() -> None:
    text = (ROOT / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")
    missing = [tag for tag in SCENARIOS if tag not in text]
    assert missing == [], f"THREAT_MODEL.md is missing scenarios: {missing}"


def test_threat_model_has_the_stride_axes() -> None:
    text = (ROOT / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8").lower()
    for axis in ("spoofing", "tampering", "repudiation", "information", "denial", "elevation"):
        assert axis in text, f"THREAT_MODEL.md should cover STRIDE: {axis}"


def _command_steps() -> list[list[str]]:
    import tasks

    return [step for step in tasks.TARGETS["security"] if isinstance(step, list)]


def test_security_target_gates_bandit_on_high() -> None:
    bandit_steps = [s for s in _command_steps() if any("bandit" in a for a in s)]
    assert bandit_steps, "the security target must run bandit"
    for step in bandit_steps:
        assert (
            "--severity-level" in step and "high" in step
        ), "bandit in the security target must fail only on HIGH (debt T1), not the LOW baseline"


def test_security_target_runs_the_security_suite() -> None:
    assert any(
        any("tests/security" in a for a in step) for step in _command_steps()
    ), "the security target must run tests/security/"
