import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.locales import get_lang, t
from bot.patient_bot.services.ai_intake import clear_intake
from bot.patient_bot.services.appointments import find_telegram_patient, get_patient_appointments
from bot.states import CANCEL_SELECT, SELECTING
from bot.utils import get_main_keyboard
from web.services.booking_service import cancel as cancel_booking
from zenflow.clock import today as clinic_today

logger = logging.getLogger(__name__)


async def show_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = get_lang(context.user_data.get("selected_therapist"))
    logger.info(f"[{user_id}] cancel: looking up appointments")

    patient_id = find_telegram_patient(user_id)
    appointments = get_patient_appointments(patient_id) if patient_id is not None else []
    today_str = clinic_today().isoformat()
    appointments = [apt for apt in appointments if apt.get("date", "") >= today_str]
    if not appointments:
        await query.edit_message_text(
            t("bot_no_appointments", lang),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")]]
            ),
        )
        return CANCEL_SELECT

    context.user_data["apts_to_cancel"] = appointments
    keyboard = [
        [
            InlineKeyboardButton(
                f"{date.fromisoformat(apt['date']).strftime('%A, %d %b')} at {apt['time']}",
                callback_data=f"cancel_apt_{i}",
            )
        ]
        for i, apt in enumerate(appointments)
    ]
    keyboard.append([InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")])
    await query.edit_message_text(
        t("bot_select_to_cancel", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CANCEL_SELECT


def _forget_intake(user_id: int) -> None:
    """Drop the cached intake history (keyed by the Telegram user), tolerating a Redis outage:
    the appointment is already cancelled, and a cache must never cost the patient that (B9)."""
    try:
        clear_intake(user_id)
    except Exception as e:
        logger.error(f"[{user_id}] intake history not cleared: {e}")


async def confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    idx = int(query.data.replace("cancel_apt_", ""))
    apt = context.user_data.get("apts_to_cancel", [])[idx]
    # One implementation (ADR-29): soft-delete, hand the hour back, delete the calendar event.
    await cancel_booking(int(apt["id"]))
    _forget_intake(update.effective_user.id)

    day_display = date.fromisoformat(apt["date"]).strftime("%A, %d %b")
    logger.info(f"[{update.effective_user.id}] cancelled appointment {apt['date']} {apt['time']}")
    selected_therapist = context.user_data.get("selected_therapist")
    lang = get_lang(selected_therapist)
    context.user_data.clear()
    if selected_therapist:
        context.user_data["selected_therapist"] = selected_therapist

    await query.edit_message_text(
        t("bot_cancelled_msg", lang, day=day_display, time=apt["time"]),
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(lang),
    )
    return SELECTING
