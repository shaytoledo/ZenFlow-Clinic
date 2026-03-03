import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import TELEGRAM_TOKEN
from bot.therapist_bot.services.relay import get_patient_for_msg

_END_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔚 End Chat", callback_data="therapist_end")]])

logger = logging.getLogger(__name__)

# Patient bot instance used to deliver therapist replies
_patient_bot = Bot(token=TELEGRAM_TOKEN)


async def handle_therapist_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a therapist reply back to the correct patient.

    The therapist must reply to the forwarded message — this is what identifies
    which patient to send to when multiple conversations are active.
    """
    msg = update.message
    therapist_name = msg.from_user.full_name or "Therapist"

    if not msg.reply_to_message:
        await msg.reply_text(
            "⚠️ Please *reply directly* to the patient's message so I know who to send it to.",
            parse_mode="Markdown",
        )
        return

    patient_id = get_patient_for_msg(msg.reply_to_message.message_id)
    if patient_id is None:
        await msg.reply_text(
            "⚠️ Could not find the patient for this message. They may have restarted the bot."
        )
        return

    try:
        await _patient_bot.send_message(
            chat_id=patient_id,
            text=f"👨‍⚕️ *{therapist_name}:*\n{msg.text}",
            parse_mode="Markdown",
            reply_markup=_END_KB,
        )
        await msg.reply_text("✅ Delivered to patient.")
        logger.info(f"Therapist reply delivered to patient {patient_id}")
    except Exception as e:
        logger.error(f"Could not deliver therapist reply to patient {patient_id}: {e}")
        await msg.reply_text(f"⚠️ Could not deliver to patient {patient_id}.")
