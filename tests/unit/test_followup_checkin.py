"""Phase 6.2 — the check-in script is deterministic: questions, buttons, parsing, red flags."""

from __future__ import annotations

import re

import pytest

from bot.services import followup_checkin as fc


def _values(prompt: fc.Prompt) -> list[str]:
    return [data.split(":", 3)[3] for row in prompt.buttons for _label, data in row]


# ── questions and buttons ──
def test_step1_greets_the_patient_and_offers_0_to_10() -> None:
    prompt = fc.question(1, 42, "en", name="Noa")
    assert prompt.text.startswith("🌿 Hi Noa!") and "*0–10*" in prompt.text
    assert _values(prompt) == [str(n) for n in range(11)]
    assert prompt.buttons[0][0] == ("0", "fu:42:1:0")


def test_every_question_has_a_hebrew_twin() -> None:
    assert set(fc.TEXT["en"]) == set(fc.TEXT["he"])
    assert fc.question(1, 1, "he", name="נועה").text.startswith("🌿 שלום נועה!")
    assert fc.question(2, 1, "he").buttons[0][0][0] == "1 — הורע מאוד"
    assert fc.lang_of("fr") == "en"


def test_improvement_buttons_carry_their_labels() -> None:
    prompt = fc.question(2, 7, "en")
    assert [row[0] for row in prompt.buttons][4] == ("5 — Much better", "fu:7:2:5")


def test_side_effect_buttons_toggle_and_show_the_selection() -> None:
    prompt = fc.question(3, 7, "en", selected=["soreness"])
    labels = [label for row in prompt.buttons for label, _ in row]
    assert labels[0] == "None" and labels[-1] == "Done ✓"
    assert "✓ Soreness" in labels and "Fatigue" in labels
    assert _values(prompt)[1:4] == ["t:soreness", "t:bruising", "t:dizziness"]


def test_optional_questions_can_be_skipped() -> None:
    assert _values(fc.question(4, 1, "en"))[-1] == "skip"
    assert _values(fc.question(5, 1, "en")) == ["yes", "partly", "no"]
    assert _values(fc.question(6, 1, "en")) == ["skip"]


def test_callback_data_fits_telegram_and_rejects_anything_else() -> None:
    longest = fc.callback(2**31 - 1, 3, "t:dizziness")
    assert len(longest.encode()) <= 64
    assert fc.parse_callback("fu:12:3:t:soreness") == (12, 3, "t:soreness")
    for bad in ("fu:12:7:1", "fu:x:1:1", "xx:12:1:1", "fu:12:1:DROP TABLE", "fu:12:1:", ""):
        assert fc.parse_callback(bad) is None, bad


# ── typed answers ──
@pytest.mark.parametrize(
    ("step", "text", "expected"),
    [
        (1, "0", (True, 0)),
        (1, " 10 ", (True, 10)),
        (1, "11", (False, None)),
        (1, "a lot", (False, None)),
        (2, "5", (True, 5)),
        (2, "0", (False, None)),
        (3, "none", (True, [])),
        (3, "אין", (True, [])),
        (3, "a little sore, tired", (True, ["soreness", "fatigue"])),
        (3, "fainted", (True, ["fainting"])),
        (3, "סחרחורת", (True, ["dizziness"])),
        (3, "2 5", (True, ["soreness", "fatigue"])),
        (3, "headache", (False, None)),
        (4, "better", (True, "better")),
        (4, "טוב", (True, "better")),
        (4, "skip", (True, None)),
        (4, "1", (True, "worse")),
        (4, "maybe", (False, None)),
        (5, "partly", (True, "partly")),
        (5, "כן", (True, "yes")),
        (5, "dunno", (False, None)),
        (6, "Slept well", (True, "Slept well")),
        (6, "skip", (True, None)),
        (6, "דלג", (True, None)),
    ],
)
def test_typed_answers_are_read_in_both_languages(step: int, text: str, expected: tuple) -> None:
    lang = "he" if re.search("[א-ת]", text) else "en"
    assert fc.parse_text(step, text, lang) == expected


def test_buttons_only_accept_their_own_values() -> None:
    assert fc.parse_button(1, "7") == (True, 7)
    assert fc.parse_button(1, "99") == (False, None)
    assert fc.parse_button(3, "none") == (True, [])
    assert fc.parse_button(4, "skip") == (True, None)
    assert fc.parse_button(4, "great") == (False, None)
    assert fc.parse_button(5, "maybe") == (False, None)
    assert fc.parse_button(6, "skip") == (True, None)


def test_each_answer_fills_one_column_and_moves_on() -> None:
    assert fc.answer(1, 4) == ({"pain_level": 4}, 2)
    assert fc.answer(3, ["soreness"]) == ({"side_effects": ["soreness"]}, 4)
    assert fc.answer(6, "ok") == ({"free_text": "ok"}, None)


def test_transcript_labels() -> None:
    assert fc.label(2, 4, "en") == "4 — Noticeably better"
    assert fc.label(3, [], "en") == "None"
    assert fc.label(3, ["soreness", "fatigue"], "he") == "רגישות/כאב מקומי, עייפות"
    assert fc.label(4, None, "en") == "Skip"


# ── safety ──
@pytest.mark.parametrize(
    ("answers", "reasons"),
    [
        ({"pain_level": 7, "improvement_rating": 2, "side_effects": ["dizziness"]}, []),
        ({"pain_level": 8}, ["pain 8/10"]),
        ({"improvement_rating": 1}, ["much worse since the treatment"]),
        ({"side_effects": ["soreness", "fainting"]}, ["reported fainting"]),
        (
            {"pain_level": 10, "improvement_rating": 1, "side_effects": ["fainting"]},
            ["pain 10/10", "much worse since the treatment", "reported fainting"],
        ),
    ],
)
def test_the_red_flag_rule(answers: dict, reasons: list[str]) -> None:
    assert fc.red_flags(answers) == reasons


def test_the_summary_is_two_lines_built_from_the_answers() -> None:
    answers = {
        "pain_level": 3,
        "improvement_rating": 4,
        "side_effects": ["soreness"],
        "sleep_quality": "better",
        "adherence": "partly",
        "free_text": "x" * 120,
    }
    text = fc.summary(answers, "en")
    first, second = text.split("\n")
    assert first == ("Pain 3/10 · Noticeably better (4/5) · sleep Better · advice followed: Partly")
    assert second.startswith("side effects: Soreness · note: “x")
    assert second.endswith("…”") and len(second) < 120
    assert fc.summary({"pain_level": 0}, "he") == "כאב 0/10\nללא תופעות לוואי · ללא הערה"
