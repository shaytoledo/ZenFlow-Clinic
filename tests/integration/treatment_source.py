"""The treatment page's source, wherever Phase 4.1 put it.

The page is `templates/treatment.html` plus the partials it includes plus the ordered scripts it
loads. Tests that inspect the page's markup or JavaScript read it through here, so moving code
between those files never silently turns a check into a no-op.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"
ENTRY = WEB / "templates" / "treatment.html"


def includes() -> list[Path]:
    """Every partial the page includes, nested ones too, in the order they are met."""
    found: list[Path] = []
    pending = [ENTRY]
    while pending:
        text = pending.pop(0).read_text(encoding="utf-8")
        for name in re.findall(r'{%\s*include\s+"([^"]+)"\s*%}', text):
            path = WEB / "templates" / name
            if path not in found:
                found.append(path)
                pending.append(path)
    return found


def scripts() -> list[Path]:
    srcs = re.findall(r'<script src="/static/([^"]+)"', ENTRY.read_text(encoding="utf-8"))
    return [WEB / "static" / src for src in srcs]


def stylesheets() -> list[Path]:
    hrefs = re.findall(
        r'<link rel="stylesheet" href="/static/([^"]+)"', ENTRY.read_text(encoding="utf-8")
    )
    return [WEB / "static" / href for href in hrefs]


def markup() -> str:
    """The entry template and every partial, concatenated."""
    return "\n".join(p.read_text(encoding="utf-8") for p in [ENTRY, *includes()])


def javascript() -> str:
    """Every script the page loads, in load order."""
    return "\n".join(p.read_text(encoding="utf-8") for p in scripts())


def source() -> str:
    """Markup and JavaScript together (what the old single template contained)."""
    return markup() + "\n" + javascript()
