"""
web/services/followup_view.py
──────────────────────────────
The "24h follow-up" card of a session (Phase 6.5, docs/FOLLOWUP.md §5): a `followups` row turned
into plain, translated values for `templates/partials/followup_card.html`, which both the live
treatment page and the read-only session archive include.

Every value here is text; the template escapes it. Nothing is rendered in JavaScript.
"""

from __future__ import annotations

import re
from typing import Any

from bot.services import followup_checkin as checkin
from web.i18n import get_t
from zenflow import clock

#: card states (the row's `sent` and `in_progress` are both "awaiting" for the therapist)
STATES = ("scheduled", "awaiting", "completed", "expired", "no_channel")
#: colour tones a value can take; each is a `fu-tone-*` class in static/css/followup.css
TONES = ("good", "fair", "poor", "neutral")
_ANSWER_KEYS = (
    "pain_level",
    "improvement_rating",
    "side_effects",
    "sleep_quality",
    "adherence",
    "free_text",
)
_IMPROVEMENT_TONE = {1: "poor", 2: "poor", 3: "fair", 4: "good", 5: "good"}
_SLEEP_TONE = {"worse": "poor", "same": "neutral", "better": "good"}
_ADHERENCE_TONE = {"yes": "good", "partly": "fair", "no": "poor"}


def _when(value: Any) -> str:
    return clock.format_clinic(value, "%Y-%m-%d %H:%M") if value else ""


def pain_tone(pain: int) -> str:
    return "good" if pain <= 3 else "fair" if pain <= 6 else "poor"


def _plain(text: str) -> str:
    """The bot's Telegram Markdown markers are not part of what the patient read."""
    return re.sub(r"[*_]", "", text)


def _answered(answers: dict[str, Any]) -> int:
    return sum(1 for key in _ANSWER_KEYS if answers.get(key) not in (None, "", []))


def delivery_view(rows: list[dict[str, Any]], lang: str | None) -> dict[str, Any] | None:
    """The session's message list (Phase 6.6, both directions since 8.3), or None when there is
    nothing to show.

    A patient answering a four-question check-in is four received messages; the therapist wants to
    read "the patient answered", so a run of identical entries is one line with a count.
    """
    if not rows:
        return None
    t = get_t(lang)
    items: list[dict[str, Any]] = []
    for row in rows:
        received = str(row.get("direction") or "out") == "in"
        item = {
            "time": _when(row.get("ts")),
            "what": t[f"dl_kind_{row.get('kind')}"],
            "channel": t[f"dl_channel_{row.get('channel')}"],
            "direction": "in" if received else "out",
            "status": str(row.get("status") or ""),
            "status_label": t["dl_dir_in"] if received else t[f"dl_status_{row.get('status')}"],
            "error": row.get("error") or "",
            "count": 1,
            "repeat": "",
        }
        if items and _same_line(items[-1], item):
            items[-1]["count"] += 1
            items[-1]["repeat"] = f"×{items[-1]['count']}"
            continue
        items.append(item)
    return {"title": t["dl_title"], "items": items}


def _same_line(previous: dict[str, Any], item: dict[str, Any]) -> bool:
    """Two entries a therapist would read as one — the same thing, the same way, no error."""
    keys = ("what", "channel", "direction", "status")
    return not previous["error"] and not item["error"] and all(previous[k] == item[k] for k in keys)


def followup_view(row: dict[str, Any] | None, lang: str | None) -> dict[str, Any] | None:
    """The card's content, or None when the session has no check-in (not completed yet)."""
    if not row:
        return None
    t = get_t(lang)
    words = checkin.TEXT[checkin.lang_of(lang)]
    status = str(row.get("status") or "")
    state = "awaiting" if status in ("sent", "in_progress") else status
    if state not in STATES:
        return None
    answers = {key: row.get(key) for key in _ANSWER_KEYS}
    manual = row.get("source") == "therapist_manual"

    if state == "scheduled":
        detail = t["fu_detail_scheduled"].format(time=_when(row.get("scheduled_for")))
    elif state == "awaiting":
        n = _answered(answers)
        key = "fu_detail_progress" if n else "fu_detail_sent"
        detail = t[key].format(time=_when(row.get("sent_at")), n=n)
    elif state == "completed":
        key = "fu_detail_manual" if manual else "fu_detail_completed"
        detail = t[key].format(time=_when(row.get("completed_at")))
    elif state == "expired":
        if row.get("sent_at"):
            detail = t["fu_detail_expired"].format(time=_when(row.get("sent_at")))
        else:
            detail = t["fu_detail_unsent"]
    else:
        detail = t["fu_detail_no_channel"].format(time=_when(row.get("scheduled_for")))

    if row.get("auto") and not manual:
        detail = f"{detail} {t['fu_auto_note']}"

    chips = []
    rating = answers.get("improvement_rating")
    if rating:
        chips.append(
            {
                "label": t["fu_change"],
                "value": f"{rating}/5 — {words['improvement'].get(int(rating), '')}",
                "tone": _IMPROVEMENT_TONE.get(int(rating), "neutral"),
            }
        )
    effects = answers.get("side_effects") or []
    if effects or (state == "completed" and not manual):
        chips.append(
            {
                "label": t["fu_side_effects"],
                "value": ", ".join(words["side_effects"].get(e, e) for e in effects)
                or words["side_effects"]["none"],
                "tone": "poor" if set(effects) & checkin.RED_FLAG_SIDE_EFFECTS else "neutral",
            }
        )
    sleep = str(answers.get("sleep_quality") or "")
    if sleep in _SLEEP_TONE:
        chips.append(
            {
                "label": t["fu_sleep"],
                "value": words["sleep"][sleep],
                "tone": _SLEEP_TONE[sleep],
            }
        )
    adherence = str(answers.get("adherence") or "")
    if adherence in _ADHERENCE_TONE:
        chips.append(
            {
                "label": t["fu_adherence"],
                "value": words["adherence"][adherence],
                "tone": _ADHERENCE_TONE[adherence],
            }
        )

    flags = []
    if row.get("needs_attention"):
        pain = answers.get("pain_level")
        if isinstance(pain, int) and pain >= checkin.PAIN_RED_FLAG:
            flags.append(t["fu_flag_pain"].format(n=pain))
        if rating == checkin.IMPROVEMENT_RED_FLAG:
            flags.append(t["fu_flag_worse"])
        if set(effects) & checkin.RED_FLAG_SIDE_EFFECTS:
            flags.append(t["fu_flag_fainting"])
        flags = flags or [t["fu_attention_generic"]]

    transcript = [
        {
            "side": "patient" if message.get("role") == "user" else "bot",
            "who": t["fu_who_patient"] if message.get("role") == "user" else t["fu_who_bot"],
            "text": (
                str(message.get("content") or "")
                if message.get("role") == "user"
                else _plain(str(message.get("content") or ""))
            ),
        }
        for message in row.get("conversation") or []
        if isinstance(message, dict)
    ]

    pain = answers.get("pain_level")
    return {
        "state": state,
        "title": t["fu_title"],
        "state_label": t[f"fu_state_{state}"],
        "detail": detail,
        "record_link": t["fu_record_link"] if state == "no_channel" else "",
        "attention": t["fu_attention"],
        "flags": flags,
        "pain": pain,
        "pain_label": t["fu_pain"],
        "pain_tone": pain_tone(int(pain)) if isinstance(pain, int) else "neutral",
        "chips": chips,
        "summary_label": t["fu_summary"],
        "summary": (row.get("ai_summary") or "") if state == "completed" else "",
        "note_label": t["fu_therapist_note"] if manual else t["fu_note"],
        "note": answers.get("free_text") or "",
        "transcript_label": t["fu_transcript"],
        "transcript": transcript,
    }
