import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.services.ai_intake import (
    clear_intake,
    generate_summary,
    get_history_dicts,
    get_next_question,
    initialize_intake,
)
from bot.services.appointments import save_appointment
from bot.services.availability import book_slot, get_available_days, get_available_hours
from bot.states import INTAKE, INTAKE_CONFIRM, SCHEDULE_DAY, SCHEDULE_HOUR, SCHEDULE_WEEK, SELECTING
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)

OPENING_QUESTION = "What's the main issue or discomfort bringing you in today?"


# ── week / day / hour selection ───────────────────────────────────────────────

async def show_week_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 1 — ask whether the patient wants this week or next week."""
    query = update.callback_query
    await query.answer()
    logger.info(f"[{update.effective_user.id}] show_week_choice")

    keyboard = [
        [InlineKeyboardButton("📅 This week", callback_data="week_0")],
        [InlineKeyboardButton("📅 Next week", callback_data="week_1")],
        [InlineKeyboardButton("⬅️ Back",      callback_data="back_main")],
    ]
    await query.edit_message_text(
        "Which week would you like to book?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SCHEDULE_WEEK


async def show_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 2 — show days that have available slots in the chosen week.

    Triggered by:
      • week_0 / week_1  (initial selection)
      • back_days        (back from hour picker — reuses stored week)
    """
    query = update.callback_query
    await query.answer()

    if query.data.startswith("week_"):
        week_offset = int(query.data.replace("week_", ""))
        context.user_data["selected_week"] = week_offset
    else:
        week_offset = context.user_data.get("selected_week", 0)

    week_label = "This week" if week_offset == 0 else "Next week"
    logger.info(f"[{update.effective_user.id}] show_days week_offset={week_offset}")

    days = get_available_days(week_offset=week_offset)
    if not days:
        await query.edit_message_text(
            f"No available slots for {week_label.lower()}. Please try another week or contact the clinic directly.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_week")]]),
        )
        return SCHEDULE_WEEK

    keyboard = [
        [InlineKeyboardButton(d.strftime("%A, %d %b"), callback_data=f"day_{d.isoformat()}")]
        for d in days
    ]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_week")])
    await query.edit_message_text(
        f"📅 *{week_label}* — choose a day:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SCHEDULE_DAY


async def show_hours(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    day_iso = query.data.replace("day_", "")
    selected_day = date.fromisoformat(day_iso)
    context.user_data["selected_day"] = day_iso
    logger.info(f"[{update.effective_user.id}] show_hours for {day_iso}")

    hours = get_available_hours(selected_day)
    if not hours:
        await query.edit_message_text(
            "No available hours on this day. Please choose another.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_days")]]),
        )
        return SCHEDULE_DAY

    keyboard = [[InlineKeyboardButton(h, callback_data=f"hour_{h}")] for h in hours]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_days")])
    await query.edit_message_text(
        f"Available hours on *{selected_day.strftime('%A, %d %b')}*:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SCHEDULE_HOUR


# ── intake confirmation ───────────────────────────────────────────────────────

async def confirm_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Slot chosen — ask whether the patient wants the intake questionnaire."""
    query = update.callback_query
    await query.answer()

    time_slot = query.data.replace("hour_", "")
    context.user_data["selected_time"] = time_slot

    day = date.fromisoformat(context.user_data["selected_day"])
    logger.info(f"[{update.effective_user.id}] slot chosen {day} {time_slot}")

    keyboard = [
        [InlineKeyboardButton("✅ Yes, let's do it", callback_data="intake_yes")],
        [InlineKeyboardButton("❌ No, skip",          callback_data="intake_no")],
    ]
    await query.edit_message_text(
        f"📅 *{day.strftime('%A, %d %b')}* at *{time_slot}* — noted!\n\n"
        f"Would you like to answer a few quick questions to help optimise your treatment session?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return INTAKE_CONFIRM


async def start_intake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User said YES — begin adaptive intake questionnaire."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    context.user_data["intake_count"] = 0
    initialize_intake(user_id, OPENING_QUESTION)

    await query.edit_message_text(
        "Great! A few quick questions to help your acupuncturist prepare. 🌿"
    )
    await context.bot.send_message(chat_id=update.effective_chat.id, text=OPENING_QUESTION)
    return INTAKE


async def skip_intake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User said NO — save appointment immediately without intake details."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    day = date.fromisoformat(context.user_data["selected_day"])
    time_slot = context.user_data["selected_time"]

    apt_summary = "Patient opted to skip the intake questionnaire."
    gcal_id = book_slot(day, time_slot, user.full_name or user.first_name, apt_summary)
    save_appointment(
        patient_id=user.id,
        patient_name=user.full_name or user.first_name,
        day=day,
        time_slot=time_slot,
        intake_history=[],
        summary=apt_summary,
        gcal_apt_event_id=gcal_id,
    )
    clear_intake(user.id)
    logger.info(f"[{user.id}] appointment saved (no intake)")
    context.user_data.clear()

    await query.edit_message_text(
        f"✅ *Appointment confirmed!*\n"
        f"📅 {day.strftime('%A, %d %b')} at {time_slot}\n\n"
        f"We look forward to seeing you at ZenFlow Clinic 🌿\n\n"
        f"What else can I help you with?",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(),
    )
    return SELECTING


# ── intake answers ────────────────────────────────────────────────────────────

async def handle_intake_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_answer = update.message.text
    intake_count = context.user_data.get("intake_count", 0) + 1
    context.user_data["intake_count"] = intake_count
    logger.info(f"[{user_id}] intake answer {intake_count}/5")

    if intake_count >= 5:
        await update.message.reply_text("Thank you! Saving your details... ⏳")

        summary = await generate_summary(user_id, user_answer)
        history = get_history_dicts(user_id)

        user = update.effective_user
        day = date.fromisoformat(context.user_data["selected_day"])
        time_slot = context.user_data["selected_time"]

        gcal_id = book_slot(day, time_slot, user.full_name or user.first_name, summary)
        save_appointment(
            patient_id=user_id,
            patient_name=user.full_name or user.first_name,
            day=day,
            time_slot=time_slot,
            intake_history=history,
            summary=summary,
            gcal_apt_event_id=gcal_id,
        )
        clear_intake(user_id)
        context.user_data.clear()

        await update.message.reply_text(
            f"✅ *Appointment confirmed!*\n"
            f"📅 {day.strftime('%A, %d %b')} at {time_slot}\n\n"
            f"We look forward to seeing you at ZenFlow Clinic 🌿\n\n"
            f"What else can I help you with?",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    next_q = await get_next_question(user_id, user_answer)
    await update.message.reply_text(next_q)
    return INTAKE
