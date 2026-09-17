import asyncio
import contextlib
import logging
import subprocess
from pathlib import Path

import ollama
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import BaseRequest

from bot.config import OLLAMA_HOST, OLLAMA_MODEL, TELEGRAM_TOKEN
from bot.errors import on_error, stale_button
from bot.patient_bot.cancel import confirm_cancel, show_appointments
from bot.patient_bot.commands import cancel_command, help_command
from bot.patient_bot.followup import handle_followup_button, handle_followup_reply
from bot.patient_bot.schedule import (
    confirm_appointment,
    handle_intake_answer,
    select_therapist_and_continue,
    show_days,
    show_hours,
    show_therapist_choice,
    show_week_choice,
    skip_intake,
    start_intake,
)
from bot.patient_bot.start import back_to_main, change_therapist, start
from bot.patient_bot.therapist import (
    end_chat,
    relay_to_therapist,
    relay_unsupported_media,
    show_therapist_for_contact,
    start_relay,
)
from bot.patient_bot.timeout import on_conversation_timeout
from bot.persistence import SqlitePersistence
from bot.states import (
    CANCEL_SELECT,
    INTAKE,
    INTAKE_CONFIRM,
    SCHEDULE_DAY,
    SCHEDULE_HOUR,
    SCHEDULE_WEEK,
    SELECTING,
    THERAPIST_INPUT,
    THERAPIST_RELAY,
    THERAPIST_SELECT,
)
from bot.therapist_bot.main import build_therapist_app

# ── logging ──────────────────────────────────────────────────────────────────


def setup_logging() -> None:
    """Structured, redacted logging (zenflow.logging). Console in dev, JSON otherwise."""
    from zenflow import logging as zlog

    zlog.configure_logging(
        "bots", file_path=Path(__file__).parent.parent / "logs" / "botLogs.text", file_mode="w"
    )


setup_logging()
logger = logging.getLogger(__name__)


# ── Ollama startup ────────────────────────────────────────────────────────────


async def _ensure_ollama(app: Application) -> None:
    logger.info("Checking Ollama...")
    try:
        client = ollama.AsyncClient(host=OLLAMA_HOST)
        info = await asyncio.wait_for(client.list(), timeout=5)
        available = [m.model for m in info.models]
        logger.info(f"Ollama running. Available models: {available}")
        if not any(OLLAMA_MODEL in m for m in available):
            logger.warning(f"Model '{OLLAMA_MODEL}' not found — run: ollama pull {OLLAMA_MODEL}")
    except Exception as e:
        logger.warning(f"Ollama not reachable ({e}). Attempting to start...")
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(4)
            logger.info("Ollama serve launched.")
        except FileNotFoundError:
            logger.error("'ollama' not found. Install from https://ollama.com")
        except Exception as e2:
            logger.error(f"Could not start Ollama: {e2}")


async def _post_init(app: Application) -> None:
    """Run on startup before the updater begins polling.

    Order matters: Ollama first (existing behaviour) so handlers don't race on a
    cold model, then the 24h follow-up scheduler as a long-lived background task.
    """
    await _ensure_ollama(app)
    try:
        from bot.services.followup_scheduler import start_followup_scheduler

        # Stash the task on the app so it shares the application's lifecycle —
        # the asyncio event loop tears it down when the app stops.
        app.bot_data["_followup_task"] = start_followup_scheduler()
    except Exception as e:
        logger.error(f"Could not start follow-up scheduler: {e}")
    try:
        from zenflow.settings import get_settings
        from zenflow.worker import start_in_process

        if get_settings().flags.queue_backend == "inprocess":
            # Durable job worker (Phase 1.2) shares the bot process on a single box.
            app.bot_data["_worker_task"] = start_in_process()
    except Exception as e:
        logger.error(f"Could not start job worker: {e}")


async def _post_shutdown(app: Application) -> None:
    """Cancel our background tasks so a job in flight is released, not left locked."""
    for key in ("_worker_task", "_followup_task"):
        task = app.bot_data.get(key)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


# ── app builder ───────────────────────────────────────────────────────────────


def _timeout_minutes() -> int:
    """Idle minutes before a patient flow is closed (0 = never)."""
    from zenflow.settings import get_settings

    return max(0, get_settings().flags.conv_timeout_minutes)


def wire_bots(patient_bot: object, therapist_bot: object) -> None:
    """Point each relay module at the running application's own Bot client (BOT_AUDIT B14).

    Both modules used to build their own `Bot(token=...)` at import time: never initialised, never
    shut down (a leaked httpx pool on exit) and outside the application's rate limiter. The
    applications already own a properly managed client each, so the relay borrows those.
    """
    import bot.patient_bot.therapist as patient_side
    import bot.therapist_bot.handlers as therapist_side

    patient_side._therapist_bot = therapist_bot  # patient → therapist
    therapist_side._patient_bot = patient_bot  # therapist → patient
    logger.info("Relay wired to the running applications' bot clients")


def build_patient_app(*, request: BaseRequest | None = None) -> Application:
    """The patient-facing application.

    `request` replaces the HTTP layer (tests drive a real Application offline with it).
    """
    builder = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        # In-flight flows survive a restart (plan 2.3); flows idle past the timeout are not resumed.
        .persistence(SqlitePersistence(stale_after_seconds=(_timeout_minutes() * 60) or None))
    )
    if request is None:
        builder = builder.connect_timeout(30.0).read_timeout(30.0)
    else:
        builder = builder.request(request).get_updates_request(request)
    app = builder.build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel_command),
            # A button pressed with no conversation state — every keyboard is in that position
            # after a restart, since the state lives in memory (B11).
            CallbackQueryHandler(stale_button),
            MessageHandler(filters.ALL & ~filters.COMMAND, start),
        ],
        states={
            SELECTING: [
                CallbackQueryHandler(show_therapist_choice, pattern="^schedule$"),
                CallbackQueryHandler(show_appointments, pattern="^cancel$"),
                CallbackQueryHandler(show_therapist_for_contact, pattern="^therapist$"),
                CallbackQueryHandler(change_therapist, pattern="^change_therapist$"),
            ],
            THERAPIST_SELECT: [
                CallbackQueryHandler(select_therapist_and_continue, pattern="^sel_t_"),
                CallbackQueryHandler(back_to_main, pattern="^back_main$"),
            ],
            SCHEDULE_WEEK: [
                CallbackQueryHandler(show_days, pattern="^week_"),
                CallbackQueryHandler(show_week_choice, pattern="^back_week$"),
                CallbackQueryHandler(back_to_main, pattern="^back_main$"),
            ],
            SCHEDULE_DAY: [
                CallbackQueryHandler(show_hours, pattern="^day_"),
                CallbackQueryHandler(show_week_choice, pattern="^back_week$"),
            ],
            SCHEDULE_HOUR: [
                CallbackQueryHandler(confirm_appointment, pattern="^hour_"),
                CallbackQueryHandler(show_days, pattern="^back_days$"),
            ],
            INTAKE_CONFIRM: [
                CallbackQueryHandler(start_intake, pattern="^intake_yes$"),
                CallbackQueryHandler(skip_intake, pattern="^intake_no$"),
            ],
            INTAKE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_intake_answer),
            ],
            CANCEL_SELECT: [
                CallbackQueryHandler(confirm_cancel, pattern="^cancel_apt_"),
                CallbackQueryHandler(back_to_main, pattern="^back_main$"),
            ],
            THERAPIST_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, start_relay),
            ],
            THERAPIST_RELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, relay_to_therapist),
                MessageHandler(~filters.TEXT & ~filters.COMMAND, relay_unsupported_media),
                CallbackQueryHandler(end_chat, pattern="^therapist_end$"),
            ],
            # Reached when nothing has been heard for `conversation_timeout` (B10). Without a
            # handler here PTB would end the conversation without telling anyone.
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, on_conversation_timeout),
                CallbackQueryHandler(on_conversation_timeout),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            CommandHandler("cancel", cancel_command),
            CommandHandler("help", help_command),
            # Last resort inside a conversation: a button no state handler claimed (B11).
            CallbackQueryHandler(stale_button),
            MessageHandler(filters.ALL, start),
        ],
        allow_reentry=False,
        name="patient",
        persistent=True,
        # 0 minutes = never expire, the clinic's call (ZF_CONV_TIMEOUT_MINUTES).
        conversation_timeout=(_timeout_minutes() * 60) or None,
    )

    # Group -1 runs before the conversation: a 24h follow-up answer is consumed wherever the
    # patient happens to be, instead of being eaten by INTAKE or forwarded to a therapist (B3).
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_followup_reply), group=-1
    )
    # …and the check-in's buttons (Phase 6.2), before any stale-button fallback sees them.
    app.add_handler(CallbackQueryHandler(handle_followup_button, pattern=r"^fu:"), group=-1)
    app.add_handler(conv)
    # Without this an exception reaches only the log; the patient gets silence (B8).
    app.add_error_handler(on_error)
    return app


async def _run(patient_app: Application, therapist_app: Application | None) -> None:
    if therapist_app is None:
        async with patient_app:
            await patient_app.start()
            await patient_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            logger.info("Patient bot running — press Ctrl+C to stop")
            await asyncio.Event().wait()
        return

    wire_bots(patient_app.bot, therapist_app.bot)
    async with patient_app, therapist_app:
        await patient_app.start()
        await therapist_app.start()
        await patient_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await therapist_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logger.info("Both bots running — press Ctrl+C to stop")
        await asyncio.Event().wait()


def main() -> None:
    logger.info("Starting ZenFlow Clinic Bot...")
    asyncio.run(_run(build_patient_app(), build_therapist_app()))


if __name__ == "__main__":
    main()
