import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.patient_bot.services.ai_intake import clear_intake
from bot.patient_bot.services.appointments import cancel_appointment, get_patient_appointments
from bot.patient_bot.services.availability import restore_slot
from bot.states import CANCEL_SELECT, SELECTING
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)


async def show_appointments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    patient_id = update.effective_user.id
    logger.info(f"[{patient_id}] cancel: looking up appointments")

    appointments = get_patient_appointments(patient_id)
    if not appointments:
        await query.edit_message_text(
            "You have no upcoming appointments.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_main")]]),
        )
        return CANCEL_SELECT

    context.user_data["apts_to_cancel"] = appointments
    keyboard = [
        [InlineKeyboardButton(
            f"{date.fromisoformat(apt['date']).strftime('%A, %d %b')} at {apt['time']}",
            callback_data=f"cancel_apt_{i}",
        )]
        for i, apt in enumerate(appointments)
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])

    await query.edit_message_text(
        "Select an appointment to cancel:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CANCEL_SELECT


async def confirm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    idx = int(query.data.replace("cancel_apt_", ""))
    apt = context.user_data.get("apts_to_cancel", [])[idx]
    cancel_appointment(apt["_filepath"])
    clear_intake(apt["patient_id"])
    await restore_slot(
        date.fromisoformat(apt["date"]),
        apt["time"],
        apt.get("gcal_apt_event_id"),
        therapist_id=apt.get("therapist_id"),
    )

    day_display = date.fromisoformat(apt["date"]).strftime("%A, %d %b")
    logger.info(f"[{update.effective_user.id}] cancelled appointment {apt['date']} {apt['time']}")
    selected_therapist = context.user_data.get("selected_therapist")
    context.user_data.clear()
    if selected_therapist:
        context.user_data["selected_therapist"] = selected_therapist

    await query.edit_message_text(
        f"✅ Appointment on *{day_display}* at *{apt['time']}* has been cancelled.\n\n"
        f"What else can I help you with?",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )
    return SELECTING
