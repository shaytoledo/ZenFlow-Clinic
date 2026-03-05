import logging

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPIST_BOT_TOKEN, THERAPIST_BY_ID, THERAPISTS
from bot.patient_bot.services.relay import end_relay, save_relay_mapping
from bot.states import SELECTING, THERAPIST_INPUT, THERAPIST_RELAY, THERAPIST_SELECT
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)

_END_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔚 End Chat", callback_data="therapist_end")]])

# Therapist bot instance used to forward patient messages
_therapist_bot: Bot | None = Bot(token=THERAPIST_BOT_TOKEN) if THERAPIST_BOT_TOKEN else None


def _get_therapist(context) -> dict | None:
    """Return the therapist dict for the patient's selected_therapist, or fall back to first active."""
    tid = context.user_data.get("selected_therapist")
    if tid and tid in THERAPIST_BY_ID:
        return THERAPIST_BY_ID[tid]
    active = [t for t in THERAPISTS if t.get("active")]
    return active[0] if active else None


async def show_therapist_for_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask patient which therapist they want to contact, then prompt for message."""
    query = update.callback_query
    await query.answer()

    active = [t for t in THERAPISTS if t.get("active")]
    if not active:
        await query.edit_message_text(
            "No therapists are available right now. Please try again later.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    if len(active) == 1:
        context.user_data["selected_therapist"] = active[0]["id"]
        await query.edit_message_text("What would you like to say to the therapist?\n\nType your message below:")
        return THERAPIST_INPUT

    # Already chose a therapist this session — skip re-selection
    existing = context.user_data.get("selected_therapist")
    if existing and any(t["id"] == existing for t in active):
        await query.edit_message_text("What would you like to say to the therapist?\n\nType your message below:")
        return THERAPIST_INPUT

    context.user_data["therapist_flow"] = "contact"
    keyboard = [
        [InlineKeyboardButton(t["name"], callback_data=f"sel_t_{t['id']}")]
        for t in active
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
    await query.edit_message_text(
        "Choose your therapist:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return THERAPIST_SELECT


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
    therapist = _get_therapist(context)

    if not _therapist_bot or not therapist:
        logger.error("Therapist bot not configured or no active therapist found")
        await update.message.reply_text(
            "Sorry, the therapist connection is not configured yet.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    try:
        sent = await _therapist_bot.send_message(
            chat_id=therapist["telegram_id"],
            text=(
                f"💬 *New message from {user.full_name or user.first_name}* (ID: `{user.id}`)\n\n"
                f"{update.message.text}"
            ),
            parse_mode="Markdown",
        )
        save_relay_mapping(sent.message_id, user.id, therapist["id"])
        logger.info(f"[{user.id}] relay opened via therapist bot, msg_id={sent.message_id}, therapist={therapist['id']}")
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
    therapist = _get_therapist(context)

    if not _therapist_bot or not therapist:
        await update.message.reply_text("⚠️ Therapist not available.", reply_markup=_END_KB)
        return THERAPIST_RELAY

    try:
        sent = await _therapist_bot.send_message(
            chat_id=therapist["telegram_id"],
            text=f"💬 *{user.full_name or user.first_name}:*\n{update.message.text}",
            parse_mode="Markdown",
        )
        save_relay_mapping(sent.message_id, user.id, therapist["id"])
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
