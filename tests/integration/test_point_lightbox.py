"""Phase 4.3d — the point lightbox: images with attribution, a placeholder when there are none.

`lightboxHtml()` in static/js/treatment/lightbox.js runs in node here (same harness as the card
tests); the open/close behaviour of the native <dialog> is covered in tests/e2e.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.integration import treatment_source as ts
from tests.integration.test_point_cards import run_js

pytestmark = pytest.mark.integration

IMAGE = {
    "url": "/media/acupoints/li4/aaa.webp",
    "thumb_url": "/media/acupoints/li4/aaa-thumb.webp",
    "kind": "diagram",
    "width": 800,
    "height": 600,
    "credit": "Drawn for ZenFlow",
    "licence": "CC BY 4.0",
    "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    "source_url": "https://example.org/li4",
    "primary": True,
}


def lightbox(
    code: str, images: list[dict[str, Any]] | None = None, lang: str = "en", **extra: Any
) -> str:
    args = {"code": code, "images": images or [], **extra}
    html = run_js(
        f"lightboxHtml({{ ...{json.dumps(args)}, info: getPointInfo({json.dumps(code)}) }})", lang
    )
    assert isinstance(html, str)
    return html


def test_no_image_shows_a_placeholder_not_a_broken_icon() -> None:
    html = lightbox("LI4")
    assert 'class="pl-placeholder" role="img" aria-label="No image for LI4 yet"' in html
    assert "<img" not in html and "pl-credit" not in html and "pl-zoom" not in html


def test_the_header_names_the_point_in_every_script() -> None:
    html = lightbox("LI4")
    assert 'id="pl-title">Hegu <span class="pl-hanzi" lang="zh">合谷</span></h2>' in html
    assert "Joining Valley · Large Intestine" in html
    assert "tp-ch-large-intestine" in html
    assert 'data-action="close-point-lightbox" aria-label="Close"' in html


def test_an_image_comes_with_its_attribution() -> None:
    html = lightbox("LI4", [IMAGE])
    assert 'src="/media/acupoints/li4/aaa.webp" alt="LI4 Hegu diagram"' in html
    assert 'width="800" height="600"' in html
    assert "Drawn for ZenFlow · Licence: " in html
    assert (
        '<a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" '
        'rel="license noopener noreferrer">CC BY 4.0</a>' in html
    )
    assert (
        '<a href="https://example.org/li4" target="_blank" rel="noopener noreferrer">Source</a>'
        in html
    )
    assert 'data-action="lightbox-zoom"' in html
    assert "pl-thumbs" not in html, "one image needs no thumbnails"


def test_several_images_get_thumbnails_and_the_chosen_one_is_shown() -> None:
    second = dict(IMAGE, url="/media/acupoints/li4/bbb.webp", thumb_url="", kind="photo")
    html = lightbox("LI4", [IMAGE, second], index=1)
    assert 'src="/media/acupoints/li4/bbb.webp" alt="LI4 Hegu photo"' in html
    assert html.count('class="pl-thumb"') == 2
    assert 'data-index="1" aria-pressed="true" aria-label="Image 2 of 2"' in html
    assert 'data-index="0" aria-pressed="false"' in html
    assert '<img src="/media/acupoints/li4/bbb.webp" alt="">' in html, "no thumb → the image itself"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:image/svg+xml,<svg onload=alert(1)>",
        "//evil.example/x.png",
        "x.png",
    ],
)
def test_only_http_links_and_our_media_paths_are_used(url: str) -> None:
    html = lightbox("LI4", [dict(IMAGE, url=url)])
    assert "pl-placeholder" in html, "an image with an unusable link is not shown"
    unsafe_licence = lightbox("LI4", [dict(IMAGE, licence_url=url, source_url=url)])
    assert "<a " not in unsafe_licence and ">CC BY 4.0<" not in unsafe_licence.split("Licence:")[0]
    assert "Licence: CC BY 4.0" in unsafe_licence
    assert run_js(f"safeUrl({json.dumps(url)})") == ""
    assert run_js("safeUrl(' https://example.org/a.png ')") == "https://example.org/a.png"


def test_attribution_text_is_escaped() -> None:
    probe = '<img src=x onerror=alert(1)>"'
    html = lightbox("LI4", [dict(IMAGE, credit=probe, licence=probe, kind=probe)])
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;&quot;" in html


def test_the_detail_keeps_cautions_and_the_ai_rationale() -> None:
    html = lightbox("LI4", rationale="Command point for the face")
    assert "Traditionally avoided in pregnancy" in html
    assert "AI rationale for this session" in html and "Command point for the face" in html
    assert "pc-caution" not in lightbox("LR3")
    unknown = lightbox("XX9")
    assert "No reference data for XX9." in unknown and "pl-hanzi" not in unknown


def test_hebrew_labels() -> None:
    html = lightbox("LI4", lang="he")
    assert 'aria-label="סגור"' in html and "אין עדיין תמונה עבור LI4" in html
    with_image = lightbox("LI4", [IMAGE], lang="he")
    assert "רישיון" in with_image and "מקור" in with_image and "הגדל" in with_image
    keys = run_js("[Object.keys(LIGHTBOX_TEXT.en).sort(), Object.keys(LIGHTBOX_TEXT.he).sort()]")
    assert keys[0] == keys[1]


def test_the_page_uses_a_native_dialog_opened_from_cards_and_tags() -> None:
    markup = ts.markup()
    assert (
        '<dialog id="point-lightbox" class="tp pl" aria-labelledby="pl-title"></dialog>' in markup
    )
    js = ts.javascript()
    assert "dialog.showModal()" in js
    assert 'class="pc-code" data-action="open-point-lightbox"' in js
    assert 'class="pc-tag-label" data-action="open-point-lightbox"' in js
    names = [p.name for p in ts.scripts()]
    assert names.index("lightbox.js") > names.index("render-points.js")


def test_the_card_badge_says_what_it_opens() -> None:
    html = run_js('pointCardHtml({ code: "LR3" }, false)')
    assert 'title="Show LR3 details" aria-haspopup="dialog">LR3</button>' in html
    assert 'title="פרטי LR3"' in run_js('pointCardHtml({ code: "LR3" }, false)', "he")
