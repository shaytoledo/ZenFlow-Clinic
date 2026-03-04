import logging

from telegram.ext import Application, MessageHandler, filters

from bot.config import THERAPIST_BOT_TOKEN, THERAPISTS
from bot.therapist_bot.handlers import handle_therapist_message

logger = logging.getLogger(__name__)


def build_therapist_app() -> Application | None:
    """Build the therapist-facing bot. Returns None if token is not configured."""
    if not THERAPIST_BOT_TOKEN:
        logger.warning("THERAPIST_BOT_TOKEN not set — therapist bot disabled")
        return None

    app = Application.builder().token(THERAPIST_BOT_TOKEN).build()

    # Single dynamic handler — routing is done at call time so newly registered
    # therapists are activated immediately without a bot restart.
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_therapist_message)
    )

    active = [t for t in THERAPISTS if t.get("active")]
    logger.info(
        f"Therapist bot started with {len(active)} active therapist(s). "
        "New therapists can self-register via the web portal."
    )
    return app
