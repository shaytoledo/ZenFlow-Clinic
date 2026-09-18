"""
web/services/history_view.py
─────────────────────────────
The session's history card (Phase 8.5): `audit_log` rows (8.1) and the session's model calls (8.2)
turned into plain, translated lines for `templates/partials/session_history.html`, shared by the
live treatment page and the read-only archive.

What a therapist gets is *who changed this record, when, and what kind of change it was* — not a
second copy of the record. The clinical text is already on the page above; repeating it in a
history card would spread the same sensitive words across more of the DOM and more of the print
view for no gain, so a line names the fields that moved and stops there.

Everything is text by the time it reaches the template, which escapes it. Nothing is rendered in
JavaScript.
"""

from __future__ import annotations

from typing import Any

from web.i18n import get_t
from zenflow import clock

#: audit actions we have words for; anything else is turned into a sentence from its own name
ACTIONS = (
    "appointment.created",
    "appointment.cancelled",
    "treatment_notes.updated",
    "session.completed",
    "followup.answered",
    "followup.recorded",
)
#: actor types we have words for (`therapist` splits into "you" and "another therapist")
ACTORS = ("patient", "ai", "system")
#: how many changed field names a line shows before it counts the rest
MAX_FIELDS = 4


def history_view(
    rows: list[dict[str, Any]],
    ai_rows: list[dict[str, Any]],
    lang: str | None,
    *,
    therapist_id: str = "",
) -> dict[str, Any] | None:
    """The card's content, or None when nothing has happened to this session yet."""
    if not rows and not ai_rows:
        return None
    t = get_t(lang)
    return {
        "title": t["hist_title"],
        "intro": t["hist_intro"],
        "items": [_line(row, t, therapist_id) for row in rows],
        "ai": _ai(ai_rows, t),
    }


def _line(row: dict[str, Any], t: Any, therapist_id: str) -> dict[str, Any]:
    actor_type = str(row.get("actor_type") or "system")
    actor_id = str(row.get("actor_id") or "")
    return {
        "time": _when(row.get("ts")),
        "who": _who(actor_type, actor_id, t, therapist_id),
        "actor": actor_type,
        "what": _what(str(row.get("action") or ""), t),
        "fields": _fields(row, t),
    }


def _who(actor_type: str, actor_id: str, t: Any, therapist_id: str) -> str:
    """A person, not an id. A client of the booking API is named — that is the point of its key."""
    if actor_type == "therapist":
        key = "hist_who_you" if actor_id and actor_id == therapist_id else "hist_who_other"
        return str(t[key])
    if actor_type == "api":
        return actor_id or str(t["hist_who_api"])
    if actor_type in ACTORS:
        return str(t[f"hist_who_{actor_type}"])
    return str(t["hist_who_system"])


def _what(action: str, t: Any) -> str:
    """The translated wording, or a readable sentence built from an action we do not know yet."""
    if action in ACTIONS:
        return str(t[f"hist_what_{action.replace('.', '_')}"])
    entity, _, verb = action.partition(".")
    words = f"{entity.replace('_', ' ')} {verb.replace('_', ' ')}".strip()
    return words[:1].upper() + words[1:] if words else str(t["hist_what_unknown"])


def _fields(row: dict[str, Any], t: Any) -> str:
    """The names of the fields that changed — never their values (they are the clinical record)."""
    import json

    names: list[str] = []
    for key in ("after_json", "before_json"):
        raw = row.get(key)
        if not raw:
            continue
        try:
            parsed = json.loads(str(raw))
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            names.extend(str(name) for name in parsed if str(name) not in names)
    if not names:
        return ""
    shown = ", ".join(names[:MAX_FIELDS])
    rest = len(names) - MAX_FIELDS
    return shown if rest <= 0 else str(t["hist_fields_more"].format(fields=shown, n=rest))


def _ai(ai_rows: list[dict[str, Any]], t: Any) -> dict[str, Any] | None:
    """One line for the model work behind this session: how many calls, how long, how many failed."""
    if not ai_rows:
        return None
    failures = sum(1 for row in ai_rows if str(row.get("status") or "ok") != "ok")
    seconds = round(sum(int(row.get("duration_ms") or 0) for row in ai_rows) / 1000, 1)
    models = [str(row.get("model") or "") for row in ai_rows if row.get("model")]
    model = max(set(models), key=models.count) if models else ""
    summary = str(t["hist_ai"].format(calls=len(ai_rows), seconds=seconds, model=model or "—"))
    if failures:
        summary = f"{summary} {t['hist_ai_failures'].format(n=failures)}"
    return {
        "calls": len(ai_rows),
        "failures": failures,
        "seconds": seconds,
        "model": model,
        "summary": summary,
    }


def _when(value: Any) -> str:
    return clock.format_clinic(value, "%Y-%m-%d %H:%M") if value else ""
