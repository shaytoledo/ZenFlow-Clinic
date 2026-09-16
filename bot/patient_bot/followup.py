"""The 24h follow-up conversation gate (BOT_AUDIT B3).

The follow-up is not part of the booking conversation: it arrives on its own schedule and the
patient answers whenever they read it — which may be in the middle of an intake, inside a
therapist chat, or at the main menu. Consuming it only in `start()` meant a "7" typed while in
`INTAKE` was fed to the intake LLM and a "much better" typed in `THERAPIST_RELAY` was forwarded to
the therapist, while the follow-up itself never advanced.

This handler is registered in a lower handler group than the ConversationHandler, so it sees every
text message first. When the message belongs to an open follow-up it answers and raises
`ApplicationHandlerStop`, which keeps the message away from the conversation *without* changing
the patient's state — they stay exactly where they were. Otherwise it does nothing and the normal
flow continues.
"""

import logging

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from bot.locales import get_lang, t
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)


async def handle_followup_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer a follow-up message, or let the update through untouched."""
    message = update.message
    user = update.effective_user
    if message is None or user is None or not message.text:
        return

    from bot.services.followup_scheduler import consume_followup_conversation

    patient_id = user.id
    consumed, reply = await consume_followup_conversation(patient_id, message.text)
    if not consumed:
        return

    lang = await _followup_lang(patient_id)
    if reply:
        await message.reply_text(reply, parse_mode="Markdown")
    else:
        await message.reply_text(
            t("bot_feedback_received", lang), reply_markup=get_main_keyboard(lang)
        )
    logger.info(f"[{patient_id}] follow-up answer consumed before the conversation handler")
    raise ApplicationHandlerStop


async def _followup_lang(patient_id: int) -> str:
    """The therapist's language for this follow-up, or English."""
    try:
        from bot.services.followup_scheduler import _get_conv_state

        state = await _get_conv_state(patient_id)
    except Exception:  # Redis unavailable — the reply still has to go out
        return "en"
    return get_lang(state.get("therapist_id")) if state else "en"
