"""Plan 9.7 — Telegram Markdown injection in the recommendation messages.

The two patient-facing recommendation messages are sent with `parse_mode="Markdown"` and interpolate
a category and a recommendation `text` into `*{cat}:* {text}`. That `text` is AI-generated advice,
and AI output is steerable by whatever a patient typed into the intake (SF-011). Unbalanced Markdown
(`*`, `_`, `[`, backtick) makes Telegram reject the whole message (HTTP 400) — so the patient would
get *no* recommendations — and a crafted `[label](http://evil)` would render a link. The dynamic
parts must be escaped; the static labels stay formatted.

(The patient→therapist relay is already immune: the channel is plain-text by default, B2.)
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.security

# bold, italic, a link and inline code — every Markdown-v1 metacharacter
EVIL = "Drink *lots* of _water_ [now](http://evil.example) `rm -rf`"


def _assert_neutralised(out: str) -> None:
    assert out.startswith("*Your post-treatment"), "the static header bold is kept"
    assert "\\*lots\\*" in out, "bold in the dynamic text is escaped"
    assert "\\_water\\_" in out, "italic is escaped"
    assert "\\[now](http" in out, "the link's opening bracket is escaped, so it is not a link"
    assert "\\`rm -rf\\`" in out, "inline code is escaped"


def test_followup_recommendations_escape_dynamic_markdown() -> None:
    from bot.services.followup_scheduler import _recommendations_telegram_text

    out = _recommendations_telegram_text([{"category": "Diet*", "text": EVIL}])
    _assert_neutralised(out)
    assert "Diet\\*:" in out, "a stray * in the category is escaped inside its bold label"


def test_treatment_recommendations_escape_dynamic_markdown() -> None:
    from web.routers.api.treatment import _format_recommendations_for_telegram

    out = _format_recommendations_for_telegram([{"category": "Sleep", "text": EVIL}])
    _assert_neutralised(out)
    assert "*Sleep:*" in out, "a clean category keeps its bold label"
