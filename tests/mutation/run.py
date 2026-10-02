"""Prove each recorded fix is real: undo it, and its regression test must fail.

    python tests/mutation/run.py [id-substring ...]      # or: python tasks.py verify-fixes

For every spec in `specs.py` (one per fix recorded in SECURITY_FINDINGS / BOT_AUDIT / PROGRESS): the
fix's code must be present; its tests must pass; with the fix undone they must FAIL; the file is
restored afterwards (also on Ctrl-C). Exit status 1 if a fix is missing or a revert survives.
Run it on a clean checkout — it edits source files in place while it works. Results are written to
mutation_results.json. See docs/FIX_VERIFICATION.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from specs import SPECS  # noqa: E402

PY = sys.executable
ROOT = Path(__file__).resolve().parents[2]
ONLY = [a.lower() for a in sys.argv[1:]]


def run(tests: list[str]) -> tuple[int, str]:
    cmd = [PY, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider", "-o", "addopts="]
    r = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    lines = [line for line in (r.stdout + r.stderr).splitlines() if line.strip()]
    marks = (" passed", " failed", " error", "no tests ran")
    summary = next(
        (line for line in reversed(lines) if any(m in line for m in marks)),
        lines[-1] if lines else "",
    )
    return r.returncode, summary.strip("= ")


def apply(text: str, spec: dict[str, Any]) -> str | None:
    """The text with the fix undone, or None when the fix's code is not there."""
    anchor = spec.get("anchor")
    if anchor and anchor not in text:
        return None
    start = text.index(anchor) if anchor else 0
    head, tail = text[:start], text[start:]
    for old, new, replace_all in spec["edits"]:
        if old not in tail:
            return None
        tail = tail.replace(old, new) if replace_all else tail.replace(old, new, 1)
    return head + tail


def main() -> int:
    results: list[dict[str, Any]] = []
    baseline: dict[tuple[str, ...], tuple[int, str]] = {}
    for spec in SPECS:
        if ONLY and not any(o in spec["id"].lower() for o in ONLY):
            continue
        path = ROOT / spec["file"]
        original = path.read_text(encoding="utf-8")
        mutated = apply(original, spec)
        row: dict[str, Any] = {"id": spec["id"], "file": spec["file"], "tests": spec["tests"]}
        if mutated is None:
            row["verdict"] = "FIX_NOT_FOUND"
        else:
            key = tuple(spec["tests"])
            if key not in baseline:
                baseline[key] = run(spec["tests"])
            base_rc, base_summary = baseline[key]
            try:
                path.write_text(mutated, encoding="utf-8")
                mut_rc, mut_summary = run(spec["tests"])
            finally:
                path.write_text(original, encoding="utf-8")
            if base_rc != 0:
                row["verdict"] = "TESTS_FAIL_WITH_THE_FIX"
            elif mut_rc == 0:
                row["verdict"] = "SURVIVED"
            else:
                row["verdict"] = "VERIFIED"
            row.update(with_fix=base_summary, without_fix=mut_summary)
        results.append(row)
        print(f"{spec['id']:42} {row['verdict']:24} {row.get('without_fix', '')}", flush=True)

    (ROOT / "mutation_results.json").write_text(
        json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("SUMMARY", counts)
    return 0 if set(counts) <= {"VERIFIED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
