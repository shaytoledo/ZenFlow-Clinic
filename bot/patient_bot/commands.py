"""`/cancel` and `/help` (BOT_AUDIT B6, plan 2.2).

`/cancel` is the explicit way out of any flow — booking, intake or a therapist chat — and `/help`
says which commands exist at all. Before this, `/start` was the only way out and it said nothing
about the booking it had just dropped.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from bot.locales import get_lang, t
from bot.states import SELECTING
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)

# Everything that belongs to one in-flight booking or chat. `selected_therapist` is deliberately
# not here: the patient's choice of therapist outlives any single flow.
IN_FLIGHT_KEYS = (
    "selected_day",
    "selected_time",
    "selected_week",
    "intake_count",
    "apts_to_cancel",
    "therapist_flow",
)


def clear_in_flight(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Drop any half-finished flow. Returns True if there was one."""
    user_data = context.user_data
    if user_data is None:
        return False
    had = any(key in user_data for key in IN_FLIGHT_KEYS)
    for key in IN_FLIGHT_KEYS:
        user_data.pop(key, None)
    return had


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Abandon whatever the patient was in the middle of and show the menu."""
    lang = get_lang((context.user_data or {}).get("selected_therapist"))
    had = clear_in_flight(context)
    user = update.effective_user
    logger.info(f"[{user.id if user else '?'}] /cancel (in-flight flow: {had})")
    if update.message is not None:
        await update.message.reply_text(
            t("bot_cancelled_flow", lang) if had else t("bot_what_to_do_menu", lang),
            reply_markup=get_main_keyboard(lang),
        )
    return SELECTING


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List the commands. Deliberately not a state change — `/help` interrupts nothing."""
    lang = get_lang((context.user_data or {}).get("selected_therapist"))
    if update.message is not None:
        await update.message.reply_text(t("bot_help", lang), parse_mode="Markdown")
