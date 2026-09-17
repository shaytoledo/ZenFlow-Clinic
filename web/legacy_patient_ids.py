"""
web/legacy_patient_ids.py
──────────────────────────
One release of compatibility for pre-7.2 patient ids in API paths (Phase 7.2).

Before 7.2 a patient id was a Telegram user id, or a negative number for a manual booking. A
browser tab opened before the upgrade still calls `/api/treatment-notes/<old id>/<date>/<time>…`;
this middleware rewrites such an id to the patient's internal id before routing, so the routes
only ever see internal ids and keep their own tenant checks (another therapist's patient is still
a 404). HTML pages redirect instead (`web/routers/pages.py`, `web/routers/patients.py`).

Remove this module one release after the migration (`patients.legacy_id` goes with it).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

#: API paths that carry a patient id: `…/{patient}/{YYYY-MM-DD}/…` and `/api/patients/{patient}`
_PATIENT_PATH = re.compile(
    r"^/api/(?:treatment-notes|appointment)/(?P<pid>-?\d+)/\d{4}-\d{2}-\d{2}(?:/|$)"
    r"|^/api/patients/(?P<pid2>-?\d+)$"
)

Scope = dict[str, Any]


class LegacyPatientIdMiddleware:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope.get("type") == "http":
            scope = await _rewrite(scope)
        await self.app(scope, receive, send)


async def _rewrite(scope: Scope) -> Scope:
    path = str(scope.get("path") or "")
    match = _PATIENT_PATH.match(path)
    if not match:
        return scope
    group = "pid" if match.group("pid") is not None else "pid2"
    old = int(match.group(group))
    from web.repositories import patient_repo

    new = await asyncio.to_thread(patient_repo.canonical_id, old)
    if new == old:
        return scope
    start, end = match.span(group)
    new_path = f"{path[:start]}{new}{path[end:]}"
    rewritten = dict(scope)
    rewritten["path"] = new_path
    raw = scope.get("raw_path")
    if isinstance(raw, bytes):
        # the id is plain digits, so it appears verbatim in the raw path at the same place
        prefix = path[:start].encode("latin-1", "ignore")
        if raw.startswith(prefix):
            rewritten["raw_path"] = prefix + str(new).encode() + raw[len(prefix) + (end - start) :]
    return rewritten
