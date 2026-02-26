import logging

from telegram.ext import Application, MessageHandler, filters

from bot.config import THERAPIST_BOT_TOKEN, THERAPIST_TELEGRAM_ID
from bot.therapist_bot.handlers import handle_therapist_reply

logger = logging.getLogger(__name__)


def build_therapist_app() -> Application | None:
    """Build the therapist-facing bot. Returns None if token is not configured."""
    if not THERAPIST_BOT_TOKEN:
        logger.warning("THERAPIST_BOT_TOKEN not set — therapist bot disabled")
        return None

    app = Application.builder().token(THERAPIST_BOT_TOKEN).build()

    if THERAPIST_TELEGRAM_ID:
        app.add_handler(
            MessageHandler(
                filters.User(user_id=THERAPIST_TELEGRAM_ID) & filters.TEXT & ~filters.COMMAND,
                handle_therapist_reply,
            )
        )
        logger.info(f"Therapist bot ready for user ID {THERAPIST_TELEGRAM_ID}")
    else:
        logger.warning("THERAPIST_TELEGRAM_ID not set — therapist bot will not route replies")

    return app
