"""What the bots do when something goes wrong (BOT_AUDIT B8, B11).

Neither application had an error handler: an exception reached PTB's own log line and the person
on the other end got nothing at all — no reply, and on a button press a spinner that never stops.
Both applications install `on_error`, and `stale_button` catches presses on keyboards the bot no
longer has any state for (every keyboard, after a restart).
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.locales import get_lang, t
from bot.states import SELECTING
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    """The patient's language, falling back to English.

    `get_lang` reads the therapist registry, which is exactly the kind of thing that may be
    failing when the error handler runs — so it must not be able to raise from here.
    """
    try:
        user_data = getattr(context, "user_data", None) or {}
        return get_lang(user_data.get("selected_therapist"))
    except Exception:
        return "en"


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log the failure with its update, then tell the person something went wrong.

    Everything here is defensive: the error handler is the last line, so it must not raise. A
    callback query is answered first — an unanswered one leaves the button spinning in the client
    even if the reply lands.
    """
    logger.exception("Unhandled error while processing an update", exc_info=context.error)

    # Errors also arrive for things that are not updates at all (a failing job, a bad webhook
    # payload); those have nobody to answer.
    reply_to = getattr(update, "message", None)
    query = getattr(update, "callback_query", None)
    if reply_to is None and query is None:
        return

    lang = _lang(context)
    message = t("bot_error", lang)

    if query is not None:
        try:
            await query.answer()
        except Exception as e:  # the query may be too old to answer
            logger.warning(f"could not answer the callback query after an error: {e}")

    try:
        if reply_to is not None:
            await reply_to.reply_text(message, reply_markup=get_main_keyboard(lang))
        elif query is not None:
            await query.edit_message_text(message, reply_markup=get_main_keyboard(lang))
    except Exception as e:
        logger.error(f"could not deliver the error message to the user: {e}")


async def stale_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """A button whose conversation is gone: answer it and hand back a working menu (B11).

    Conversation state lives in memory, so every inline keyboard from before a restart lands here,
    as does any button belonging to a state the patient has already left.
    """
    query = update.callback_query
    if query is None:  # registered only for callback updates; belt and braces
        return SELECTING

    lang = _lang(context)
    user = update.effective_user
    chat = update.effective_chat
    await query.answer()
    logger.info(f"[{user.id if user else '?'}] stale button: {query.data!r}")
    try:
        await query.edit_message_text(
            t("bot_button_expired", lang), reply_markup=get_main_keyboard(lang)
        )
    except Exception as e:
        # The original message may be gone, or identical to what we are about to write.
        logger.info(f"could not edit the stale keyboard ({e}); sending a new menu")
        if chat is not None:
            await context.bot.send_message(
                chat_id=chat.id,
                text=t("bot_button_expired", lang),
                reply_markup=get_main_keyboard(lang),
            )
    return SELECTING
