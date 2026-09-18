"""
web/routers/api/admin.py
─────────────────────────
Operator endpoints. Auth required (any signed-in, active therapist); never returns secrets.

- `GET /api/admin/flags`   — current feature-flag state (Phase 0.4)
- `GET /api/admin/metrics` — jobs, follow-ups, AI, messages, relay, dependencies (Phase 8.4),
  and, with `ZF_METRICS_PROMETHEUS=1`, `?format=prometheus` for a scraper.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from web.deps import require_active_therapist
from web.services import metrics
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


@router.get("/metrics")
async def get_metrics(request: Request, format: str = "json", hours: float = 24.0) -> Response:
    """How the system is doing over the last `hours`. The endpoint that reports outages must not
    fail because of one: every part degrades to a value rather than an error."""
    therapist = require_active_therapist(request)
    window = min(max(float(hours), 1.0), 24.0 * 30)
    data = await metrics.snapshot(hours=window, therapist_id=str(therapist.get("id") or ""))

    if format == "json":
        return JSONResponse(data)
    if format == "prometheus" and get_settings().flags.metrics_prometheus:
        return PlainTextResponse(
            metrics.prometheus_text(data), media_type="text/plain; version=0.0.4; charset=utf-8"
        )
    # An unknown format, or Prometheus while the flag is off: the route simply does not exist.
    return JSONResponse({"code": "not_found", "detail": "No such metrics format"}, status_code=404)
