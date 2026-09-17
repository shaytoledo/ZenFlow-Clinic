"""Phase 6.5 — the 24h follow-up card: one server-rendered view for the live page and the archive.

The presenter (`web/services/followup_view.py`) is checked state by state; the pages are checked
for the card, its escaping, and the archive sharing the same partial. Browser checks for each
state live in tests/e2e/test_followup_card.py.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from web.repositories import followup_repo
from web.services.followup_view import STATES, followup_view

pytestmark = pytest.mark.integration

PW = "pw-Test-123"


def _row(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "status": "completed",
        "source": "patient",
        "scheduled_for": "2026-03-02T12:00:00Z",
        "sent_at": "2026-03-02T12:00:00Z",
        "completed_at": "2026-03-02T12:30:00Z",
        "pain_level": None,
        "improvement_rating": None,
        "side_effects": [],
        "sleep_quality": None,
        "adherence": None,
        "free_text": None,
        "ai_summary": None,
        "needs_attention": False,
        "conversation": [],
    }
    base.update(fields)
    return base


# ── the presenter, state by state ──
def test_no_checkin_no_card() -> None:
    assert followup_view(None, "en") is None
    assert followup_view(_row(status="lost"), "en") is None
    assert set(STATES) == {"scheduled", "awaiting", "completed", "expired", "no_channel"}


def test_scheduled_shows_when_it_goes_out_in_clinic_time() -> None:
    view = followup_view(_row(status="scheduled", sent_at=None, completed_at=None), "en")
    assert view is not None
    assert (view["state"], view["state_label"]) == ("scheduled", "Scheduled")
    assert view["detail"] == "The check-in goes out 2026-03-02 14:00."  # Asia/Jerusalem
    assert view["pain"] is None and view["chips"] == [] and view["transcript"] == []


def test_awaiting_counts_the_answers_so_far() -> None:
    sent = followup_view(_row(status="sent", completed_at=None), "en")
    assert sent is not None and sent["state"] == "awaiting"
    assert sent["detail"] == "Sent 2026-03-02 14:00 — waiting for the patient's answers."
    progress = followup_view(
        _row(status="in_progress", completed_at=None, pain_level=4, improvement_rating=3), "en"
    )
    assert progress is not None
    assert progress["detail"] == "Sent 2026-03-02 14:00 — 2 of 6 questions answered so far."
    assert progress["pain"] == 4 and progress["pain_tone"] == "fair"
    assert [c["label"] for c in progress["chips"]] == ["Change"]


def test_completed_by_the_patient() -> None:
    view = followup_view(
        _row(
            pain_level=2,
            improvement_rating=5,
            side_effects=[],
            sleep_quality="better",
            adherence="partly",
            free_text="Much calmer",
            ai_summary="Pain 2/10 · Much better (5/5)\nno side effects · note: “Much calmer”",
            conversation=[
                {"role": "ai", "content": "How much pain, on a scale of *0–10*?\n_(0 = none)_"},
                {"role": "user", "content": "2 *really*"},
            ],
        ),
        "en",
    )
    assert view is not None
    assert view["detail"] == "Answered 2026-03-02 14:30."
    assert (view["pain"], view["pain_tone"]) == (2, "good")
    assert view["chips"] == [
        {"label": "Change", "value": "5/5 — Much better", "tone": "good"},
        {"label": "Side effects", "value": "None", "tone": "neutral"},
        {"label": "Sleep", "value": "Better", "tone": "good"},
        {"label": "Advice followed", "value": "Partly", "tone": "fair"},
    ]
    assert view["summary"].startswith("Pain 2/10")
    assert (view["note_label"], view["note"]) == ("Patient's note", "Much calmer")
    assert view["transcript"] == [
        {"side": "bot", "who": "ZenFlow", "text": "How much pain, on a scale of 0–10?\n(0 = none)"},
        {"side": "patient", "who": "Patient", "text": "2 *really*"},
    ], "the bot's Markdown markers go; the patient's words stay as typed"
    assert view["flags"] == [] and view["record_link"] == ""


def test_completed_by_the_therapist() -> None:
    view = followup_view(
        _row(source="therapist_manual", improvement_rating=2, free_text="Phoned: sore"), "en"
    )
    assert view is not None
    assert view["detail"] == "Recorded by the therapist 2026-03-02 14:30."
    assert view["note_label"] == "Therapist's note"
    assert [c["label"] for c in view["chips"]] == ["Change"], "no side-effects claim for a call"


def test_expired_says_whether_it_ever_went_out() -> None:
    sent = followup_view(_row(status="expired", completed_at=None, pain_level=5), "en")
    assert sent is not None and sent["state_label"] == "No reply"
    assert sent["detail"] == "Sent 2026-03-02 14:00; the patient did not finish within 48 hours."
    assert sent["pain"] == 5, "what was answered stays visible"
    unsent = followup_view(_row(status="expired", sent_at=None, completed_at=None), "en")
    assert unsent is not None and unsent["detail"] == "The check-in was not sent in time."


def test_no_channel_points_at_the_outcome_form() -> None:
    view = followup_view(_row(status="no_channel", sent_at=None, completed_at=None), "en")
    assert view is not None
    assert view["state_label"] == "Phone follow-up"
    assert view["detail"] == (
        "This patient cannot be messaged — call them and record the outcome. Due 2026-03-02 14:00."
    )
    assert view["record_link"] == "Record the outcome"


def test_red_flags_are_spelled_out() -> None:
    view = followup_view(
        _row(needs_attention=True, pain_level=9, improvement_rating=1, side_effects=["fainting"]),
        "en",
    )
    assert view is not None
    assert view["flags"] == ["pain 9/10", "much worse since the treatment", "reported fainting"]
    assert view["pain_tone"] == "poor"
    assert {c["label"]: c["tone"] for c in view["chips"]}["Side effects"] == "poor"
    generic = followup_view(_row(needs_attention=True, pain_level=2), "en")
    assert generic is not None and generic["flags"] == ["flagged during the check-in"]


def test_hebrew() -> None:
    view = followup_view(_row(improvement_rating=4, adherence="yes", pain_level=3), "he")
    assert view is not None
    assert (view["title"], view["state_label"]) == ("מעקב 24 שעות", "הושלם")
    assert view["chips"][0] == {"label": "שינוי", "value": "4/5 — השתפר בניכר", "tone": "good"}
    assert {c["label"]: c["value"] for c in view["chips"]}["ביצוע ההמלצות"] == "כן"


def test_every_card_string_is_translated() -> None:
    import json

    from web.i18n import _LOCALES_DIR

    source = (_LOCALES_DIR.parent / "web/services/followup_view.py").read_text(encoding="utf-8")
    keys = set(re.findall(r't\["(fu_[a-z_]+)"\]', source))
    keys |= {f"fu_state_{s}" for s in STATES}
    for lang in ("en", "he"):
        data = json.loads((_LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert sorted(k for k in keys if not data.get(k)) == [], lang


# ── the pages ──
@pytest.fixture
def clinic(make_therapist, make_appointment, make_treatment_notes, login_as):
    async def _make(lang: str = "en") -> dict[str, Any]:
        therapist = make_therapist(email=f"card-{lang}@example.com", password=PW, language=lang)
        patient = {"patient_id": 900_000_801, "name": "Noa Katz", "source": "telegram"}
        apt = make_appointment(therapist=therapist, patient=patient, apt_time="09:00")
        make_treatment_notes(apt)
        return {"therapist": therapist, "apt": apt, "client": await login_as(therapist)}

    return _make


def _checkin(apt_id: int, **answers: Any) -> None:
    followup_repo.schedule(apt_id, "2026-03-02T12:00:00Z")
    followup_repo.mark_sent(apt_id, [{"role": "ai", "content": "Pain? *0–10*"}])
    followup_repo.complete(
        apt_id,
        conversation=[
            {"role": "ai", "content": "Pain? *0–10*"},
            {"role": "user", "content": "<img src=x onerror=alert(1)>"},
        ],
        answers=answers,
        needs_attention=True,
        ai_summary="Pain 9/10\nno note",
    )


def _card(html: str) -> str:
    match = re.search(r'<section id="followup-card".*?</section>', html, re.DOTALL)
    assert match is not None
    return match.group(0)


async def test_the_treatment_page_renders_the_card_on_the_server(clinic) -> None:
    c = await clinic()
    apt = c["apt"]
    url = f"/treatment/{apt['patient_id']}/{apt['date']}/09-00"
    empty = _card((await c["client"].get(url)).text)
    assert "hidden" in empty and "fu-head" not in empty, "no check-in yet"

    _checkin(apt["id"], pain_level=9, free_text="<b>bold</b> & more")
    page = await c["client"].get(url)
    card = _card(page.text)
    assert 'class="zf-card fu fu-state-completed"' in card
    assert (
        '<p class="fu-alert" role="alert"><strong>Needs your attention:</strong> pain 9/10</p>'
        in card
    )
    assert 'value="9">9/10</meter>' in card
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in card and "<b>bold</b>" not in card
    assert "&lt;img src=x onerror=alert(1)&gt;" in card and "<img" not in card
    assert "Pain? 0–10" in card
    assert "/static/css/followup.css" in page.text


async def test_the_archive_shows_the_same_card(clinic) -> None:
    c = await clinic("he")
    apt = c["apt"]
    url = f"/patients/{apt['patient_id']}/session/{apt['id']}"
    assert 'id="followup-card"' not in (await c["client"].get(url)).text, "no check-in, no card"

    _checkin(apt["id"], improvement_rating=4)
    page = await c["client"].get(url)
    card = _card(page.text)
    assert "fu-state-completed" in card and "מעקב 24 שעות" in card
    assert "4/5 — השתפר בניכר" in card
    assert '<link rel="stylesheet" href="/static/css/followup.css" />' in page.text


async def test_the_no_channel_card_links_to_the_form(
    clinic, make_patient, make_appointment
) -> None:
    c = await clinic()
    manual = make_appointment(
        therapist=c["therapist"], patient=make_patient("Manual", manual=True), apt_time="11:00"
    )
    followup_repo.schedule(manual["id"], "2026-03-02T12:00:00Z")
    page = await c["client"].get(f"/treatment/{manual['patient_id']}/{manual['date']}/11-00")
    card = _card(page.text)
    assert "fu-state-no-channel" in card
    assert '<a class="fu-link" href="#manual-feedback-card">Record the outcome</a>' in card


def test_the_old_json_renderer_is_gone() -> None:
    from tests.integration import treatment_source as ts

    js = ts.javascript()
    assert "renderFollowupResults" not in js and "followup-body" not in ts.source()
    archive = (ts.WEB / "templates/session_archive.html").read_text(encoding="utf-8")
    assert '{% include "partials/followup_card.html" %}' in archive
    assert "followup_conversation" not in archive.split("<!-- 24h follow-up")[1]
