"""
web/routers/api/acupoints.py
─────────────────────────────
Acupoint reference data for the treatment page (Phase 4.3a), replacing the JS literal.
"""

import asyncio
import hashlib
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from web.deps import require_active_therapist
from web.repositories import acupoint_repo
from zenflow.settings import get_settings
from zenflow.storage import get_storage

router = APIRouter(prefix="/api")

#: what the page learns about each image (storage keys stay on the server)
IMAGE_FIELDS = ("kind", "width", "height", "credit", "licence", "licence_url", "source_url")


def _image_links() -> dict[str, list[dict[str, Any]]]:
    """Stored images per point with links from the configured store (ZF_POINT_IMAGES)."""
    storage = get_storage()
    return {
        code: [
            {
                "url": storage.url(row["storage_key"]),
                "thumb_url": storage.url(row["thumb_key"]),
                "primary": bool(row["is_primary"]),
                **{name: row[name] for name in IMAGE_FIELDS},
            }
            for row in rows
        ]
        for code, rows in acupoint_repo.images_by_code().items()
    }


@router.get("/acupoints")
async def get_acupoints(request: Request, lang: str = "") -> Response:
    """Every reference point in `lang` (default: the therapist's language), cacheable by ETag."""
    therapist = require_active_therapist(request)
    points = await asyncio.to_thread(acupoint_repo.all_points)
    images = await asyncio.to_thread(_image_links) if get_settings().flags.point_images else None
    payload = acupoint_repo.reference(
        points, lang or str(therapist.get("language") or "en"), images
    )
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    etag = '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(payload, headers=headers)
