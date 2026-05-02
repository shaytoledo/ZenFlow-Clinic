from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPISTS
from bot.locales import get_lang, t
from bot.states import SELECTING, THERAPIST_SELECT
from bot.utils import get_main_keyboard


async def change_therapist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clear current therapist selection and re-run therapist selection."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("selected_therapist", None)

    import asyncio as _asyncio
    def _load_active():
        from bot.db import get_db
        rows = get_db().execute(
            "SELECT id, name FROM therapists WHERE active=1 ORDER BY name"
        ).fetchall()
        return [{"id": r["id"], "name": r["name"]} for r in rows]

    active = await _asyncio.to_thread(_load_active)

    # Language unknown at this point (therapist being re-selected) — default en
    lang = "en"

    if not active:
        await query.edit_message_text(
            t("bot_no_therapists_now", lang),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")]]),
        )
        return SELECTING

    keyboard = [
        [InlineKeyboardButton(th["name"], callback_data=f"sel_t_{th['id']}")]
        for th in active
    ]
    keyboard.append([InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")])
    header = t("bot_choose_therapist", lang) if len(active) > 1 else t("bot_available_therapists", lang)
    await query.edit_message_text(header, reply_markup=InlineKeyboardMarkup(keyboard))
    return THERAPIST_SELECT


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — welcome message, then ask which therapist (if >1 active)."""
    user = update.effective_user

    # Handle in-flight 24h follow-up conversation before falling to the normal menu
    if update.message and update.message.text:
        from bot.services.followup_scheduler import consume_followup_conversation
        consumed, reply = await consume_followup_conversation(user.id, update.message.text)
        if consumed:
            # Determine lang from follow-up state if possible
            _fu_lang = "en"
            try:
                from bot.services.followup_scheduler import _get_conv_state
                state = await _get_conv_state(user.id)
                if state:
                    _fu_lang = get_lang(state.get("therapist_id"))
            except Exception:
                pass
            if reply:
                await update.message.reply_text(reply, parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    t("bot_feedback_received", _fu_lang),
                    reply_markup=get_main_keyboard(_fu_lang),
                )
            return SELECTING

    active = [th for th in THERAPISTS if th.get("active")]
    existing_therapist_id = context.user_data.get("selected_therapist")

    # Therapist already chosen this session → go straight to menu
    if existing_therapist_id:
        lang = get_lang(existing_therapist_id)
        text = t("bot_what_to_do", lang, name=user.first_name)
        if update.message:
            await update.message.reply_text(text, reply_markup=get_main_keyboard(lang))
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, reply_markup=get_main_keyboard(lang))
        return SELECTING

    # No therapist selected yet — use default lang for the greeting
    lang = "en"
    welcome = t("bot_welcome", lang, name=user.first_name)

    if not active:
        text = welcome + "\n\n" + t("bot_no_therapists_later", lang)
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown")
        return SELECTING

    if len(active) == 1:
        # Only one therapist — auto-select, use their language immediately
        context.user_data["selected_therapist"] = active[0]["id"]
        lang = get_lang(active[0]["id"])
        welcome = t("bot_welcome", lang, name=user.first_name)
        text = welcome + "\n\n" + t("bot_therapist_assigned", lang, therapist=active[0]["name"])
        if update.message:
            await update.message.reply_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(lang))
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=get_main_keyboard(lang))
        return SELECTING

    # Multiple therapists — ask which one (language unknown until they pick)
    context.user_data["therapist_flow"] = "welcome"
    keyboard = [
        [InlineKeyboardButton(th["name"], callback_data=f"sel_t_{th['id']}")]
        for th in active
    ]
    text = welcome + "\n\n" + t("bot_choose_therapist_prompt", lang)
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
    lang = get_lang(context.user_data.get("selected_therapist"))
    await query.edit_message_text(t("bot_what_to_do_menu", lang), reply_markup=get_main_keyboard(lang))
    return SELECTING
