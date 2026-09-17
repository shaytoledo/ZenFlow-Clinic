"""Phase 4.4 — the sidebar user card opens Settings.

It used to be a plain <div>. Now it is a real link: keyboard- and screen-reader-reachable, with
hover/focus states and the same "current page" highlight as the nav items.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.integration

PW = "pw-Test-123"


def _card(html: str) -> str:
    found = re.search(r'<a [^>]*class="zf-user-card[^"]*"[^>]*>.*?</a>', html, re.DOTALL)
    assert found is not None, "the user card is not a link"
    return found.group(0)


async def test_the_user_card_links_to_settings(make_therapist, login_as, fake_redis) -> None:
    client = await login_as(
        make_therapist(name="Dana Cohen", email="card@example.com", password=PW)
    )
    page = await client.get("/patients")
    card = _card(page.text)
    assert 'href="/settings"' in card
    assert "Dana Cohen" in card
    assert 'class="zf-sr-only"' in card, "the link says where it goes, not just who"
    assert "aria-current" not in card and 'class="zf-user-card"' in card
    assert (await client.get("/settings")).status_code == 200


async def test_the_card_is_current_on_the_settings_page(
    make_therapist, login_as, fake_redis
) -> None:
    client = await login_as(make_therapist(email="card-active@example.com", password=PW))
    card = _card((await client.get("/settings")).text)
    assert 'class="zf-user-card active"' in card
    assert 'aria-current="page"' in card


def test_the_card_has_hover_and_focus_states() -> None:
    from tests.integration import treatment_source as ts

    css = (ts.WEB / "static/style.css").read_text(encoding="utf-8")
    for selector in (
        ".zf-user-card:hover",
        ".zf-user-card:focus-visible",
        ".zf-user-card.active",
        ".zf-sr-only",
    ):
        assert selector in css, selector
