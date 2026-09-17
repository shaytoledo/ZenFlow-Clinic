"""
bot/services/followup_checkin.py
─────────────────────────────────
The 24h check-in script (Phase 6.2, docs/FOLLOWUP.md §3): six short questions, their buttons,
how a typed answer is read, the red-flag rule and the two-line summary.

Everything here is deterministic and free of I/O. The AI may only word the summary; it never
adds a question or a score (plan 6.2).

Steps:
  1 pain            0–10                                        required
  2 improvement     1–5, labelled                               required
  3 side effects    none / soreness / bruising / dizziness /    several may be chosen
                    fatigue / fainting / other
  4 sleep           worse / same / better                       optional (Skip)
  5 adherence       yes / partly / no                           required
  6 notes           free text                                   optional (Skip)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

LAST_STEP = 6
SIDE_EFFECTS = ("none", "soreness", "bruising", "dizziness", "fatigue", "fainting", "other")
SLEEP = ("worse", "same", "better")
ADHERENCE = ("yes", "partly", "no")
#: the red-flag rule (plan 6.2): any of these makes the check-in `needs_attention`
PAIN_RED_FLAG = 8
IMPROVEMENT_RED_FLAG = 1
RED_FLAG_SIDE_EFFECTS = frozenset({"fainting"})
CALLBACK_PREFIX = "fu"

Buttons = list[list[tuple[str, str]]]


@dataclass(frozen=True)
class Prompt:
    """A message to the patient and its inline buttons (label, callback data)."""

    text: str
    buttons: Buttons = field(default_factory=list)


TEXT: dict[str, dict[str, Any]] = {
    "en": {
        "greeting": "🌿 Hi {name}! Following up on your acupuncture session yesterday.\n\n",
        "q1": (
            "How much pain or discomfort do you have right now, on a scale of *0–10*?\n"
            "_(0 = none · 10 = very severe)_"
        ),
        "q2": "Thank you! 🙏\n\nOverall, how has your condition *changed* since the treatment?",
        "q3": (
            "Have you noticed any side effects since the treatment?\n"
            "_(Choose all that apply, then tap *Done*)_"
        ),
        "q4": "How has your *sleep* been since the treatment?",
        "q5": "Were you able to follow the *lifestyle recommendations*?",
        "q6": (
            "Last question — anything you'd like to tell your therapist?\n"
            "_(Type a message, or tap *Skip*)_"
        ),
        "complete": (
            "🙏 Thank you for your feedback! Your therapist will review it before your next "
            "session.\n\nTake good care of yourself. See you soon! 🌿"
        ),
        "improvement": {
            1: "Much worse",
            2: "Slightly worse",
            3: "About the same",
            4: "Noticeably better",
            5: "Much better",
        },
        "side_effects": {
            "none": "None",
            "soreness": "Soreness",
            "bruising": "Bruising",
            "dizziness": "Dizziness",
            "fatigue": "Fatigue",
            "fainting": "Fainting",
            "other": "Other",
        },
        "sleep": {"worse": "Worse", "same": "The same", "better": "Better"},
        "adherence": {"yes": "Yes", "partly": "Partly", "no": "No"},
        "done": "Done ✓",
        "skip": "Skip",
        "err_pain": "Please choose a number from *0* to *10*. 🙏",
        "err_improvement": "Please choose a number from *1* to *5*. 🙏",
        "err_choice": "Please choose one of the options. 🙏",
        "stale": "That question was already answered.",
        "skip_words": ("skip", "s", "-", "no", "none"),
        "summary_pain": "Pain {pain}/10",
        "summary_sleep": "sleep {sleep}",
        "summary_adherence": "advice followed: {adherence}",
        "summary_no_effects": "no side effects",
        "summary_effects": "side effects: {effects}",
        "summary_note": "note: “{note}”",
        "summary_no_note": "no note",
    },
    "he": {
        "greeting": "🌿 שלום {name}! מתעדכנים אחרי הטיפול אתמול.\n\n",
        "q1": (
            "כמה כאב או אי-נוחות יש לך כרגע, בסולם *0–10*?\n" "_(0 = ללא כאב · 10 = כאב חמור מאוד)_"
        ),
        "q2": "תודה! 🙏\n\nבסך הכל, איך *השתנה* מצבך מאז הטיפול?",
        "q3": "היו תופעות לוואי מאז הטיפול?\n_(אפשר לבחור כמה, ואז *סיום*)_",
        "q4": "איך *ישנת* מאז הטיפול?",
        "q5": "הצלחת לפעול לפי *ההמלצות לאורח חיים*?",
        "q6": "שאלה אחרונה — יש משהו שתרצה/י לספר למטפל/ת?\n_(כתוב/כתבי הודעה, או *דלג*)_",
        "complete": (
            "🙏 תודה על המשוב! המטפל/ת יעבור/תעבור עליו לפני הפגישה הבאה.\n\n"
            "תשמור/י על עצמך. להתראות! 🌿"
        ),
        "improvement": {
            1: "הורע מאוד",
            2: "הורע מעט",
            3: "ללא שינוי",
            4: "השתפר בניכר",
            5: "השתפר מאוד",
        },
        "side_effects": {
            "none": "אין",
            "soreness": "רגישות/כאב מקומי",
            "bruising": "שטף דם",
            "dizziness": "סחרחורת",
            "fatigue": "עייפות",
            "fainting": "התעלפות",
            "other": "אחר",
        },
        "sleep": {"worse": "פחות טוב", "same": "אותו דבר", "better": "טוב יותר"},
        "adherence": {"yes": "כן", "partly": "חלקית", "no": "לא"},
        "done": "סיום ✓",
        "skip": "דלג",
        "err_pain": "אנא בחר/י מספר בין *0* ל־*10*. 🙏",
        "err_improvement": "אנא בחר/י מספר בין *1* ל־*5*. 🙏",
        "err_choice": "אנא בחר/י אחת מהאפשרויות. 🙏",
        "stale": "על השאלה הזאת כבר ענית.",
        "skip_words": ("דלג", "לא", "-", "skip"),
        "summary_pain": "כאב {pain}/10",
        "summary_sleep": "שינה: {sleep}",
        "summary_adherence": "ביצוע ההמלצות: {adherence}",
        "summary_no_effects": "ללא תופעות לוואי",
        "summary_effects": "תופעות לוואי: {effects}",
        "summary_note": "הערה: “{note}”",
        "summary_no_note": "ללא הערה",
    },
}
# Words a typed answer may use, in either language, for the choice questions.
_WORDS: dict[str, dict[str, str]] = {
    "side_effects": {
        "none": "none", "no": "none", "nothing": "none", "אין": "none", "לא": "none",
        "soreness": "soreness", "sore": "soreness", "pain": "soreness", "רגישות": "soreness",
        "bruising": "bruising", "bruise": "bruising", "bruised": "bruising", "שטף": "bruising",
        "dizziness": "dizziness", "dizzy": "dizziness", "סחרחורת": "dizziness",
        "fatigue": "fatigue", "tired": "fatigue", "tiredness": "fatigue", "עייפות": "fatigue",
        "fainting": "fainting", "fainted": "fainting", "faint": "fainting",
        "התעלפות": "fainting", "התעלפתי": "fainting",
        "other": "other", "אחר": "other",
    },
    "sleep": {
        "worse": "worse", "bad": "worse", "גרוע": "worse", "פחות": "worse",
        "same": "same", "אותו": "same",
        "better": "better", "good": "better", "טוב": "better",
    },
    "adherence": {
        "yes": "yes", "y": "yes", "כן": "yes",
        "partly": "partly", "partially": "partly", "some": "partly", "חלקית": "partly",
        "no": "no", "n": "no", "לא": "no",
    },
}  # fmt: skip


def lang_of(lang: str | None) -> str:
    return "he" if lang == "he" else "en"


def _t(lang: str) -> dict[str, Any]:
    return TEXT[lang_of(lang)]


def callback(appointment_id: int, step: int, value: str) -> str:
    return f"{CALLBACK_PREFIX}:{int(appointment_id)}:{int(step)}:{value}"


def parse_callback(data: str) -> tuple[int, int, str] | None:
    """`fu:<appointment>:<step>:<value>` → its parts, or None for anything else."""
    match = re.fullmatch(rf"{CALLBACK_PREFIX}:(\d+):([1-6]):([a-z0-9:]{{1,24}})", data or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), match.group(3)


# ── questions ──
def question(
    step: int,
    appointment_id: int,
    lang: str,
    *,
    name: str = "",
    selected: tuple[str, ...] | list[str] = (),
) -> Prompt:
    t = _t(lang)
    text = str(t[f"q{step}"])
    if step == 1:
        text = str(t["greeting"]).format(name=name or "there") + text

    def btn(label: str, value: str) -> tuple[str, str]:
        return (label, callback(appointment_id, step, value))

    buttons: Buttons
    if step == 1:
        buttons = [
            [btn(str(n), str(n)) for n in range(0, 6)],
            [btn(str(n), str(n)) for n in range(6, 11)],
        ]
    elif step == 2:
        buttons = [[btn(f"{n} — {label}", str(n))] for n, label in t["improvement"].items()]
    elif step == 3:
        labels = t["side_effects"]
        chosen = set(selected)
        toggles = [
            btn(("✓ " if key in chosen else "") + labels[key], f"t:{key}")
            for key in SIDE_EFFECTS
            if key != "none"
        ]
        buttons = [
            [btn(labels["none"], "none")],
            toggles[:3],
            toggles[3:],
            [btn(t["done"], "done")],
        ]
    elif step == 4:
        buttons = [[btn(t["sleep"][key], key) for key in SLEEP], [btn(t["skip"], "skip")]]
    elif step == 5:
        buttons = [[btn(t["adherence"][key], key) for key in ADHERENCE]]
    else:
        buttons = [[btn(t["skip"], "skip")]]
    return Prompt(text, buttons)


def completion(lang: str) -> Prompt:
    return Prompt(str(_t(lang)["complete"]))


def error(step: int, lang: str) -> str:
    t = _t(lang)
    if step == 1:
        return str(t["err_pain"])
    if step == 2:
        return str(t["err_improvement"])
    return str(t["err_choice"])


def stale(lang: str) -> str:
    return str(_t(lang)["stale"])


# ── answers ──
def _int_in(text: str, low: int, high: int) -> int | None:
    try:
        n = int(text.strip())
    except (TypeError, ValueError):
        return None
    return n if low <= n <= high else None


def _is_skip(text: str, lang: str) -> bool:
    words = set(TEXT["en"]["skip_words"]) | set(_t(lang)["skip_words"])
    return text.strip().lower() in words


def parse_text(step: int, text: str, lang: str) -> tuple[bool, Any]:
    """A typed answer → (understood, value). The value is what `answer()` takes."""
    raw = (text or "").strip()
    if step == 1:
        n = _int_in(raw, 0, 10)
        return (n is not None, n)
    if step == 2:
        n = _int_in(raw, 1, 5)
        return (n is not None, n)
    if step == 3:
        words = [w for w in re.split(r"[\s,;/]+", raw.lower()) if w]
        numbered = {str(i): key for i, key in enumerate(SIDE_EFFECTS, start=1)}
        found = [numbered.get(w) or _WORDS["side_effects"].get(w) for w in words]
        effects = [f for f in found if f]
        if not effects:
            return (False, None)
        chosen = sorted(set(effects) - {"none"}, key=SIDE_EFFECTS.index)
        return (True, chosen)
    if step in (4, 5):
        key = "sleep" if step == 4 else "adherence"
        options = SLEEP if step == 4 else ADHERENCE
        if step == 4 and _is_skip(raw, lang):
            return (True, None)
        numbered = {str(i): v for i, v in enumerate(options, start=1)}
        word = raw.lower().split()[0] if raw else ""
        value = numbered.get(word) or _WORDS[key].get(word)
        return (value is not None, value)
    return (True, None if _is_skip(raw, lang) else raw[:2000])


def parse_button(step: int, value: str) -> tuple[bool, Any]:
    """A tapped answer → (understood, value). Toggles are handled by the caller."""
    if step == 1:
        n = _int_in(value, 0, 10)
        return (n is not None, n)
    if step == 2:
        n = _int_in(value, 1, 5)
        return (n is not None, n)
    if step == 3:
        return (value == "none", [])  # "done" is resolved with the current selection
    if step == 4:
        if value == "skip":
            return (True, None)
        return (value in SLEEP, value if value in SLEEP else None)
    if step == 5:
        return (value in ADHERENCE, value if value in ADHERENCE else None)
    return (value == "skip", None)


def answer(step: int, value: Any) -> tuple[dict[str, Any], int | None]:
    """The columns an answer fills and the next step (None: the check-in is complete)."""
    column = {
        1: "pain_level",
        2: "improvement_rating",
        3: "side_effects",
        4: "sleep_quality",
        5: "adherence",
        6: "free_text",
    }[step]
    return {column: value}, (step + 1 if step < LAST_STEP else None)


def label(step: int, value: Any, lang: str) -> str:
    """How an answer reads in the transcript."""
    t = _t(lang)
    if value is None:
        return str(t["skip"])
    if step == 2:
        return f"{value} — {t['improvement'][int(value)]}"
    if step == 3:
        return ", ".join(t["side_effects"][v] for v in value) or t["side_effects"]["none"]
    if step == 4:
        return str(t["sleep"][value])
    if step == 5:
        return str(t["adherence"][value])
    return str(value)


# ── the red-flag rule and the summary ──
def red_flags(answers: dict[str, Any]) -> list[str]:
    reasons = []
    pain = answers.get("pain_level")
    if isinstance(pain, int) and pain >= PAIN_RED_FLAG:
        reasons.append(f"pain {pain}/10")
    if answers.get("improvement_rating") == IMPROVEMENT_RED_FLAG:
        reasons.append("much worse since the treatment")
    for effect in answers.get("side_effects") or []:
        if effect in RED_FLAG_SIDE_EFFECTS:
            reasons.append(f"reported {effect}")
    return reasons


def summary(answers: dict[str, Any], lang: str) -> str:
    """Two plain lines for the therapist, built only from the answers."""
    t = _t(lang)
    first = []
    if answers.get("pain_level") is not None:
        first.append(t["summary_pain"].format(pain=answers["pain_level"]))
    if answers.get("improvement_rating"):
        n = int(answers["improvement_rating"])
        first.append(f"{t['improvement'][n]} ({n}/5)")
    if answers.get("sleep_quality"):
        first.append(t["summary_sleep"].format(sleep=t["sleep"][answers["sleep_quality"]]))
    if answers.get("adherence"):
        first.append(t["summary_adherence"].format(adherence=t["adherence"][answers["adherence"]]))
    effects = answers.get("side_effects") or []
    second = [
        (
            t["summary_effects"].format(effects=", ".join(t["side_effects"][e] for e in effects))
            if effects
            else t["summary_no_effects"]
        )
    ]
    note = (answers.get("free_text") or "").strip()
    if note:
        short = note if len(note) <= 80 else note[:79] + "…"
        second.append(t["summary_note"].format(note=short))
    else:
        second.append(t["summary_no_note"])
    return " · ".join(first) + "\n" + " · ".join(second)
