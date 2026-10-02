"""
bot/webhooks.py — the bots process's own HTTP endpoint (Phase 12.2.5, ADR-49).

    GET  /healthz              200 while every bot application is running, else 503 (both modes)
    POST /telegram/patient     Telegram's updates for the patient bot     (ZF_WEBHOOK_MODE=1 only)
    POST /telegram/therapist   … for the therapist bot

In polling mode (the default, and local development) the two POST routes answer 404, and /healthz
gives the load balancer / ECS something to check. In webhook mode each bot is registered with
`setWebhook(url, secret_token)`. Telegram echoes the token in `X-Telegram-Bot-Api-Secret-Token`,
and a request without the right one is refused (403) before its body is read.

Each bot has its own token, derived from TELEGRAM_WEBHOOK_SECRET, so a header seen by one bot
cannot feed the other. Bodies are capped (`MAX_BODY_BYTES`) and never logged: they carry patients'
messages. A verified update is queued on the bot application, exactly as polling would deliver it,
and Telegram gets its 200 at once. Handlers run afterwards, so a slow AI call never makes Telegram
retry the delivery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from telegram import Update

from bot.interfaces.telegram_channel import SECRET_HEADER

logger = logging.getLogger(__name__)

#: Telegram's updates are a few KB; a file is a file_id, never the bytes
MAX_BODY_BYTES = 1_000_000


def bot_secret(name: str, master: str) -> str:
    """The secret_token for one bot: HMAC(master, name), 64 hex chars (Telegram allows A-Za-z0-9_-)."""
    return hmac.new(
        master.encode(), f"zenflow-telegram-webhook:{name}".encode(), hashlib.sha256
    ).hexdigest()


def webhook_path(name: str) -> str:
    return f"/telegram/{name}"


def build_app(apps: dict[str, Any], *, webhook_mode: bool, master_secret: str) -> Starlette:
    """The ASGI app serving /healthz and, in webhook mode, one POST route per bot application."""

    async def telegram(request: Request) -> Response:
        name = request.path_params["name"]
        application = apps.get(name)
        if not webhook_mode or application is None:
            return Response(status_code=404)
        given = request.headers.get(SECRET_HEADER, "")
        if not master_secret or not hmac.compare_digest(
            given.encode(), bot_secret(name, master_secret).encode()
        ):
            logger.warning("webhook %s: refused a request without the right secret token", name)
            return Response(status_code=403)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return Response(status_code=413)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            return Response(status_code=413)
        try:
            update = Update.de_json(json.loads(body), application.bot)
        except (ValueError, TypeError, KeyError):
            logger.warning("webhook %s: an unreadable update was refused", name)
            return Response(status_code=400)
        if update is None:
            return Response(status_code=400)
        await application.update_queue.put(update)
        return Response(status_code=200)

    async def healthz(_request: Request) -> JSONResponse:
        down = sorted(name for name, application in apps.items() if not application.running)
        body = {"ok": not down, "mode": "webhook" if webhook_mode else "polling", "down": down}
        return JSONResponse(body, status_code=503 if down else 200)

    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/telegram/{name}", telegram, methods=["POST"]),
        ]
    )
