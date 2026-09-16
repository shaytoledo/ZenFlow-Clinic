"""Phase 4.2 — the design tokens keep text readable in both themes (WCAG AA, 4.5:1).

`static/css/tokens.css` holds the palette; this test reads it the way a browser would (the
`:root` block, then the `[data-theme="dark"]` overrides) and checks every text/background pair
the point cards use.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parents[2] / "web/static/css/tokens.css"
AA = 4.5

CHANNELS = (
    "stomach",
    "large-intestine",
    "pericardium",
    "liver",
    "spleen",
    "governing-vessel",
    "heart",
    "kidney",
    "gallbladder",
    "triple-energizer",
    "bladder",
    "conception-vessel",
    "lung",
    "extra-point",
    "default",
)
#: (text token, background token)
TEXT_PAIRS = [
    ("--zf-text", "--zf-surface"),
    ("--zf-text-2", "--zf-surface"),
    ("--zf-text-muted", "--zf-surface"),
    ("--zf-text-muted", "--zf-surface-muted"),
    ("--zf-accent-text", "--zf-surface"),
    ("--zf-accent-text", "--zf-accent-soft"),
    ("--zf-on-accent", "--zf-accent-fill"),
    ("--zf-caution-text", "--zf-caution-soft"),
    ("--zf-danger-text", "--zf-danger-soft"),
    *[(f"--zf-ch-{c}-ink", f"--zf-ch-{c}-soft") for c in CHANNELS],
    *[(f"--zf-ch-{c}-ink", "--zf-surface") for c in CHANNELS],
]


def _block(css: str, selector: str) -> dict[str, str]:
    body = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.DOTALL)
    assert body is not None, selector
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body.group(1)))


def _theme(name: str) -> dict[str, str]:
    css = re.sub(r"/\*.*?\*/", "", TOKENS.read_text(encoding="utf-8"), flags=re.DOTALL)
    tokens = _block(css, ":root")
    if name == "dark":
        tokens |= _block(css, ':root[data-theme="dark"]')
    return tokens


def _luminance(hex_colour: str) -> float:
    value = hex_colour.strip().lstrip("#")
    assert re.fullmatch(r"[0-9A-Fa-f]{6}", value), f"not a solid #rrggbb colour: {hex_colour}"

    def channel(i: int) -> float:
        c = int(value[i : i + 2], 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_the_contrast_formula() -> None:
    assert contrast("#000000", "#FFFFFF") == pytest.approx(21)
    assert contrast("#0D9488", "#FFFFFF") == pytest.approx(3.74, abs=0.01)


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_text_pair_meets_aa(theme: str) -> None:
    tokens = _theme(theme)
    failing = {
        f"{fg} on {bg}": round(contrast(tokens[fg], tokens[bg]), 2)
        for fg, bg in TEXT_PAIRS
        if contrast(tokens[fg], tokens[bg]) < AA
    }
    assert failing == {}


def test_dark_theme_overrides_every_colour() -> None:
    """A colour the dark block forgets would stay light-on-light."""
    light, dark = _theme("light"), _block(
        re.sub(r"/\*.*?\*/", "", TOKENS.read_text(encoding="utf-8"), flags=re.DOTALL),
        ':root[data-theme="dark"]',
    )
    colours = {k for k, v in light.items() if v.strip().startswith("#")}
    assert sorted(colours - set(dark)) == []
