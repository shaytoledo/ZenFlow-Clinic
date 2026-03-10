from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPISTS
from bot.states import SELECTING, THERAPIST_SELECT
from bot.utils import get_main_keyboard


async def change_therapist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clear current therapist selection and re-run therapist selection."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("selected_therapist", None)

    # Always load fresh from SQLite — avoids stale in-memory THERAPISTS list
    import asyncio as _asyncio
    def _load_active():
        from bot.db import get_db
        rows = get_db().execute(
            "SELECT id, name FROM therapists WHERE active=1 ORDER BY name"
        ).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    active = await _asyncio.to_thread(_load_active)

    if not active:
        await query.edit_message_text(
            "No therapists are currently available.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_main")]]),
        )
        return SELECTING

    if len(active) == 1:
        context.user_data["selected_therapist"] = active[0]["id"]
        await query.edit_message_text(
            f"You are working with *{active[0]['name']}*. 🌿\n\nWhat would you like to do?",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(show_change_therapist=False),
        )
        return SELECTING

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — welcome message, then ask which therapist (if >1 active)."""
    user = update.effective_user
    active = [t for t in THERAPISTS if t.get("active")]

    # Therapist already chosen this session → go straight to menu
    if context.user_data.get("selected_therapist"):
        text = f"What would you like to do, {user.first_name}? 🌿"
        if update.message:
            await update.message.reply_text(text, reply_markup=get_main_keyboard())
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, reply_markup=get_main_keyboard())
        return SELECTING

    greeting = f"Hello {user.first_name}! 👋 Welcome to *ZenFlow Clinic*. 🌿\n\n"

    if not active:
        text = greeting + "No therapists are available at the moment. Please try again later."
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown")
        return SELECTING

    if len(active) == 1:
        # Only one therapist — auto-select, show menu
        context.user_data["selected_therapist"] = active[0]["id"]
        text = greeting + f"Your therapist is *{active[0]['name']}*.\n\nWhat would you like to do?"
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard())
        return SELECTING

    # Multiple therapists — ask which one
    context.user_data["therapist_flow"] = "welcome"
    keyboard = [
        [InlineKeyboardButton(t["name"], callback_data=f"sel_t_{t['id']}")]
        for t in active
    ]
    text = greeting + "First, let's check out your therapist — who would you like to work with?"
    if update.message:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return THERAPIST_SELECT


async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback handler for the '⬅️ Back' button that returns to the main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("What would you like to do?", reply_markup=get_main_keyboard())
    return SELECTING
