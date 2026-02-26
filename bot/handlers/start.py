from telegram import Update
from telegram.ext import ContextTypes

from bot.states import SELECTING
from bot.utils import get_main_keyboard


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — show welcome message and main menu."""
    user = update.effective_user
    text = (
        f"Hello {user.first_name}! 👋\n"
        f"Welcome to *ZenFlow Clinic*.\n\n"
        f"What would you like to do?"
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=get_main_keyboard()
        )
    return SELECTING


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback handler for the '⬅️ Back' button that returns to the main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("What would you like to do?", reply_markup=get_main_keyboard())
    return SELECTING
