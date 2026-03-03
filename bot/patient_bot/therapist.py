import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPIST_BOT_TOKEN, THERAPIST_TELEGRAM_ID
from bot.patient_bot.services.relay import end_relay, save_relay_mapping
from bot.states import SELECTING, THERAPIST_INPUT, THERAPIST_RELAY
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)

_END_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔚 End Chat", callback_data="therapist_end")]])

# Therapist bot instance used to forward patient messages
_therapist_bot: Bot | None = Bot(token=THERAPIST_BOT_TOKEN) if THERAPIST_BOT_TOKEN else None


async def ask_therapist_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt the patient to type their first message."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "What would you like to say to the therapist?\n\nType your message below:"
    )
    return THERAPIST_INPUT


async def start_relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """First patient message — open two-way relay with the therapist."""
    user = update.effective_user

    if not _therapist_bot or not THERAPIST_TELEGRAM_ID:
        logger.error("Therapist bot not configured (THERAPIST_BOT_TOKEN / THERAPIST_TELEGRAM_ID missing)")
        await update.message.reply_text(
            "Sorry, the therapist connection is not configured yet.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    try:
        sent = await _therapist_bot.send_message(
            chat_id=THERAPIST_TELEGRAM_ID,
            text=(
                f"💬 *New message from {user.full_name or user.first_name}* (ID: `{user.id}`)\n\n"
                f"{update.message.text}"
            ),
            parse_mode="Markdown",
        )
        save_relay_mapping(sent.message_id, user.id)
        logger.info(f"[{user.id}] relay opened via therapist bot, msg_id={sent.message_id}")
    except Exception as e:
        logger.error(f"[{user.id}] failed to forward to therapist bot: {e}")
        await update.message.reply_text(
            "Could not reach the therapist right now. Please try again later.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    await update.message.reply_text(
        "✅ *Message sent to the therapist!*\n\n"
        "Keep typing here — your messages will be forwarded.\n"
        "Press *End Chat* when you're done.",
        parse_mode="Markdown",
        reply_markup=_END_KB,
    )
    return THERAPIST_RELAY


async def relay_to_therapist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Relay subsequent patient messages to the therapist."""
    user = update.effective_user
    try:
        sent = await _therapist_bot.send_message(
            chat_id=THERAPIST_TELEGRAM_ID,
            text=f"💬 *{user.full_name or user.first_name}:*\n{update.message.text}",
            parse_mode="Markdown",
        )
        save_relay_mapping(sent.message_id, user.id)
        logger.info(f"[{user.id}] relayed via therapist bot, msg_id={sent.message_id}")
        await update.message.reply_text("✅ Sent.", reply_markup=_END_KB)
    except Exception as e:
        logger.error(f"[{user.id}] relay failed: {e}")
        await update.message.reply_text("⚠️ Could not forward your message. Please try again.", reply_markup=_END_KB)
    return THERAPIST_RELAY


async def end_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Patient ends the relay session."""
    query = update.callback_query
    await query.answer()
    end_relay(update.effective_user.id)
    logger.info(f"[{update.effective_user.id}] ended therapist chat")
    await query.edit_message_text(
        "Chat ended. If the therapist replies you'll still receive it here.\n\n"
        "What else can I help you with?",
        reply_markup=get_main_keyboard(),
    )
    return SELECTING
