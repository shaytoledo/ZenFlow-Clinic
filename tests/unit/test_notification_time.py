"""R1 review fix: the notification bell's "time ago" must read canonical UTC instants.

Stored instants end in `Z` (ADR-19); legacy rows are `YYYY-MM-DD HH:MM:SS` (UTC, no zone). The
formatter used to append a `Z` to everything, so a canonical value became `…ZZ` → "Invalid Date" on
every notification. This runs the real function from base.html under Node.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[2] / "web" / "templates" / "base.html"


def _format_notif_time_source() -> str:
    src = BASE.read_text(encoding="utf-8")
    match = re.search(r"\n  function _formatNotifTime\(iso\) \{.*?\n  \}\n", src, re.S)
    assert match, "base.html no longer defines _formatNotifTime"
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize(
    "value",
    [
        "MINUTES_AGO_Z",  # canonical: …Z
        "MINUTES_AGO_LEGACY",  # legacy: 'YYYY-MM-DD HH:MM:SS', UTC without a zone
    ],
)
def test_canonical_and_legacy_instants_both_read_as_minutes_ago(value: str) -> None:
    program = (
        "const _ZF_LANG = 'en';\n"
        "const _ZF_NOTIF_STR = {just_now: 'just now', min_ago: 'min ago', h_ago: 'h ago'};\n"
        + _format_notif_time_source()
        + "\nconst t = new Date(Date.now() - 5 * 60000).toISOString().slice(0, 19);\n"
        + "const inputs = {MINUTES_AGO_Z: t + 'Z', MINUTES_AGO_LEGACY: t.replace('T', ' ')};\n"
        + f"process.stdout.write(_formatNotifTime(inputs[{json.dumps(value)}]));"
    )
    out = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, check=True, timeout=30
    ).stdout
    assert out == "5 min ago", out
