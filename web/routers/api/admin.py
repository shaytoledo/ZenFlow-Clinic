"""
web/routers/api/admin.py
─────────────────────────
Operator endpoints. Phase 0.4: `GET /api/admin/flags` — current feature-flag state.
Auth required (any signed-in, active therapist). Never returns secrets.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from web.deps import require_active_therapist
from zenflow.settings import get_settings

router = APIRouter(prefix="/api/admin")


@router.get("/flags")
async def get_flags(request: Request) -> JSONResponse:
    require_active_therapist(request)
    s = get_settings()
    return JSONResponse(
        {
            "env": s.env,
            "flags": s.flags.snapshot(),
            "ai_provider": s.ai_provider,
            "messaging_channel": s.messaging_channel,
        }
    )
