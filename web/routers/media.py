"""
web/routers/media.py
─────────────────────
Serves LocalStorage media (Phase 4.3b) to signed-in therapists. Remote stores (S3, Phase 4.3c)
hand out their own signed links instead.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from web.deps import require_signed_in
from zenflow.storage import LocalStorage, StorageError, content_type_of, get_storage

router = APIRouter(dependencies=[Depends(require_signed_in)])


@router.get("/media/{key:path}", include_in_schema=False)
async def media(key: str) -> FileResponse:
    try:
        storage = get_storage()
        if not isinstance(storage, LocalStorage):
            raise StorageError("not a local store")
        path = storage.path(key)
        media_type = content_type_of(key)
    except StorageError:  # remote stores hand out their own (presigned) links
        raise HTTPException(status_code=404) from None
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )
