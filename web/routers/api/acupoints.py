"""
web/routers/api/acupoints.py
─────────────────────────────
Acupoint reference data for the treatment page (Phase 4.3a), replacing the JS literal.
"""

import asyncio
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from web.deps import require_active_therapist
from web.repositories import acupoint_repo

router = APIRouter(prefix="/api")


@router.get("/acupoints")
async def get_acupoints(request: Request, lang: str = "") -> Response:
    """Every reference point in `lang` (default: the therapist's language), cacheable by ETag."""
    therapist = require_active_therapist(request)
    points = await asyncio.to_thread(acupoint_repo.all_points)
    payload = acupoint_repo.reference(points, lang or str(therapist.get("language") or "en"))
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)
