"""Phase 13.1 — the documentation cannot quietly drift from the code.

"A PR that changes behaviour and not the docs is incomplete" (plan, Phase 13). These catch the
mechanical half of that: a command the docs tell an operator to run must exist, every document
must be findable from CLAUDE.md's index, and every variable in CLAUDE.md's environment table must
be one the app reads. The other half — saying what changed — is the PR's job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLAUDE = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
DOCS = sorted((ROOT / "docs").glob("*.md"))


def _quoted_modules() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in [*DOCS, ROOT / "CLAUDE.md", ROOT / "startup" / "START.md"]:
        for module in re.findall(r"python -m (zenflow\.[a-z_]+)", path.read_text(encoding="utf-8")):
            found.setdefault(module, []).append(path.name)
    return found


@pytest.mark.parametrize("module", sorted(_quoted_modules()))
def test_every_command_the_docs_quote_exists(module: str) -> None:
    source = ROOT / (module.replace(".", "/") + ".py")
    assert source.is_file(), f"{module} (quoted in {_quoted_modules()[module]}) does not exist"
    text = source.read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in text, f"{module} cannot be run with python -m"


def test_every_document_is_in_the_claude_md_index() -> None:
    listed = set(re.findall(r"\| `docs/([A-Za-z_]+\.md)` \|", CLAUDE))
    unlisted = sorted(p.name for p in DOCS if p.name not in listed)
    assert not unlisted, f"add these to the documentation table in CLAUDE.md: {unlisted}"


def test_every_variable_in_the_environment_table_is_a_setting() -> None:
    from zenflow.settings import ALL_ENV_VARS

    section = CLAUDE.split("## Environment variables", 1)[1].split("\n## ", 1)[0]
    names: set[str] = set()
    for row in section.splitlines():
        if row.startswith("| `"):
            names |= set(re.findall(r"`([A-Z][A-Z0-9_]+)`", row.split(" | ")[0]))
    assert names, "the environment table moved — update this test"
    assert not names - set(
        ALL_ENV_VARS
    ), f"documented but not read: {sorted(names - set(ALL_ENV_VARS))}"


def test_every_adr_is_numbered_once_and_in_order() -> None:
    text = (ROOT / "docs" / "TECHNICAL_DECISIONS.md").read_text(encoding="utf-8")
    numbers = [int(n) for n in re.findall(r"^## ADR-(\d+)", text, re.M)]
    assert len(numbers) == len(set(numbers)), "an ADR number is used twice"
    assert numbers == sorted(numbers), "ADRs are appended in order"
