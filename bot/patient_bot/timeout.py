"""What happens when a patient goes quiet mid-flow (BOT_AUDIT B10).

Nothing used to expire. A patient left in `THERAPIST_RELAY` had every later message forwarded to
their therapist days afterwards — "I want to book Tuesday" arriving as a clinical message — and an
abandoned intake waited forever, so the next thing they typed was read as answer number three.

`ZF_CONV_TIMEOUT_MINUTES` (default 30) is how long a flow may sit idle; `0` switches expiry off.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from bot.locales import get_lang, t
from bot.patient_bot.commands import clear_in_flight
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)


async def on_conversation_timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Close an idle flow, tell the patient, and leave them at the menu.

    Ending the conversation does not cut the patient off from their therapist: a therapist reply
    is delivered by the patient bot directly, not through the conversation state.
    """
    lang = get_lang((context.user_data or {}).get("selected_therapist"))
    clear_in_flight(context)
    user = update.effective_user
    logger.info(f"[{user.id if user else '?'}] conversation timed out")

    text = t("bot_timed_out", lang)
    keyboard = get_main_keyboard(lang)
    if update.message is not None:
        await update.message.reply_text(text, reply_markup=keyboard)
    elif update.effective_chat is not None:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text=text, reply_markup=keyboard
        )
    return ConversationHandler.END
