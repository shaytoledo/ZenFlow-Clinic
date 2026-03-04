import asyncio
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

from bot.config import OLLAMA_HOST, OLLAMA_MODEL, TELEGRAM_TOKEN
from bot.patient_bot.cancel import confirm_cancel, show_appointments
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
from bot.patient_bot.start import back_to_main, start
from bot.patient_bot.therapist import (
    end_chat,
    relay_to_therapist,
    show_therapist_for_contact,
    start_relay,
)
from bot.therapist_bot.main import build_therapist_app
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


# ── logging ──────────────────────────────────────────────────────────────────

class _SingleLineFormatter(logging.Formatter):
    """Collapses every log record — including exceptions — to exactly one line."""
    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info:
            record.exc_text = repr(record.exc_info[1])
            record.exc_info = None
        record.stack_info = None
        return super().format(record).replace("\n", " | ")


def setup_logging() -> None:
    log_path = Path(__file__).parent.parent / "botLogs.text"
    fmt = _SingleLineFormatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="w")
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)


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


# ── app builder ───────────────────────────────────────────────────────────────

def build_patient_app() -> Application:
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_ensure_ollama)
        .build()
    )

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.ALL & ~filters.COMMAND, start),
        ],
        states={
            SELECTING: [
                CallbackQueryHandler(show_therapist_choice,      pattern="^schedule$"),
                CallbackQueryHandler(show_appointments,          pattern="^cancel$"),
                CallbackQueryHandler(show_therapist_for_contact, pattern="^therapist$"),
            ],
            THERAPIST_SELECT: [
                CallbackQueryHandler(select_therapist_and_continue, pattern="^sel_t_"),
                CallbackQueryHandler(back_to_main, pattern="^back_main$"),
            ],
            SCHEDULE_WEEK: [
                CallbackQueryHandler(show_days,        pattern="^week_"),
                CallbackQueryHandler(show_week_choice, pattern="^back_week$"),
                CallbackQueryHandler(back_to_main,     pattern="^back_main$"),
            ],
            SCHEDULE_DAY: [
                CallbackQueryHandler(show_hours,      pattern="^day_"),
                CallbackQueryHandler(show_week_choice, pattern="^back_week$"),
            ],
            SCHEDULE_HOUR: [
                CallbackQueryHandler(confirm_appointment, pattern="^hour_"),
                CallbackQueryHandler(show_days,           pattern="^back_days$"),
            ],
            INTAKE_CONFIRM: [
                CallbackQueryHandler(start_intake, pattern="^intake_yes$"),
                CallbackQueryHandler(skip_intake,  pattern="^intake_no$"),
            ],
            INTAKE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_intake_answer),
            ],
            CANCEL_SELECT: [
                CallbackQueryHandler(confirm_cancel, pattern="^cancel_apt_"),
                CallbackQueryHandler(back_to_main,   pattern="^back_main$"),
            ],
            THERAPIST_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, start_relay),
            ],
            THERAPIST_RELAY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, relay_to_therapist),
                CallbackQueryHandler(end_chat, pattern="^therapist_end$"),
            ],
        },
        fallbacks=[
            CommandHandler("start", start),
            MessageHandler(filters.ALL, start),
        ],
        allow_reentry=False,
    )

    app.add_handler(conv)
    return app


async def _run(patient_app: Application, therapist_app: Application | None) -> None:
    if therapist_app is None:
        patient_app.run_polling(allowed_updates=Update.ALL_TYPES)
        return

    async with patient_app, therapist_app:
        await patient_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await therapist_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        await patient_app.start()
        await therapist_app.start()
        logger.info("Both bots running — press Ctrl+C to stop")
        await asyncio.Event().wait()


def main() -> None:
    logger.info("Starting ZenFlow Clinic Bot...")
    asyncio.run(_run(build_patient_app(), build_therapist_app()))


if __name__ == "__main__":
    main()
