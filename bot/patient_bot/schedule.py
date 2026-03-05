import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPISTS
from bot.patient_bot.services.ai_intake import (
    clear_intake,
    generate_summary,
    get_history_dicts,
    get_next_question,
    initialize_intake,
)
from bot.patient_bot.services.appointments import save_appointment
from bot.patient_bot.services.availability import book_slot, get_available_days, get_available_hours
from bot.states import (
    INTAKE, INTAKE_CONFIRM, SCHEDULE_DAY, SCHEDULE_HOUR, SCHEDULE_WEEK,
    SELECTING, THERAPIST_INPUT, THERAPIST_SELECT,
)
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)

OPENING_QUESTION = "What's the main issue or discomfort bringing you in today?"


# ── therapist selection ───────────────────────────────────────────────────────

async def show_therapist_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 0 — patient picks a therapist before seeing availability.

    Skipped automatically when:
    - Only one active therapist (auto-selected)
    - Patient already chose a therapist this session
    """
    query = update.callback_query
    await query.answer()
    context.user_data["therapist_flow"] = "schedule"

    active = [t for t in THERAPISTS if t.get("active")]
    if not active:
        await query.edit_message_text(
            "No therapists are available at the moment. Please contact the clinic.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="back_main")]]),
        )
        return SELECTING

    # Auto-select: only one therapist, or patient already picked one
    existing = context.user_data.get("selected_therapist")
    if len(active) == 1:
        context.user_data["selected_therapist"] = active[0]["id"]
        return await show_week_choice(update, context)
    if existing and any(t["id"] == existing for t in active):
        return await show_week_choice(update, context)

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


async def select_therapist_and_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Patient chose a therapist — route to welcome / schedule / contact flow."""
    query = update.callback_query
    therapist_id = query.data.replace("sel_t_", "")
    context.user_data["selected_therapist"] = therapist_id
    flow = context.user_data.pop("therapist_flow", "schedule")

    if flow == "contact":
        await query.answer()
        await query.edit_message_text("What would you like to say to the therapist?\n\nType your message below:")
        return THERAPIST_INPUT

    if flow == "welcome":
        therapist = next((t for t in THERAPISTS if t["id"] == therapist_id), None)
        t_name = therapist["name"] if therapist else "your therapist"
        await query.answer()
        await query.edit_message_text(
            f"Great! You'll be working with *{t_name}*. 🌿\n\nWhat would you like to do?",
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    # Schedule flow: show_week_choice handles query.answer()
    return await show_week_choice(update, context)


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

    therapist_id = context.user_data.get("selected_therapist")
    days = await get_available_days(week_offset=week_offset, therapist_id=therapist_id)
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

    therapist_id = context.user_data.get("selected_therapist")
    hours = await get_available_hours(selected_day, therapist_id=therapist_id)
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
    selected_therapist = context.user_data.get("selected_therapist")
    gcal_id = await book_slot(day, time_slot, user.full_name or user.first_name, apt_summary,
                               therapist_id=selected_therapist)
    save_appointment(
        patient_id=user.id,
        patient_name=user.full_name or user.first_name,
        day=day,
        time_slot=time_slot,
        intake_history=[],
        summary=apt_summary,
        gcal_apt_event_id=gcal_id,
        therapist_id=selected_therapist or "",
    )
    clear_intake(user.id)
    logger.info(f"[{user.id}] appointment saved (no intake)")
    context.user_data.clear()
    if selected_therapist:
        context.user_data["selected_therapist"] = selected_therapist

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

        selected_therapist = context.user_data.get("selected_therapist")
        gcal_id = await book_slot(day, time_slot, user.full_name or user.first_name, summary,
                                   therapist_id=selected_therapist)
        save_appointment(
            patient_id=user_id,
            patient_name=user.full_name or user.first_name,
            day=day,
            time_slot=time_slot,
            intake_history=history,
            summary=summary,
            gcal_apt_event_id=gcal_id,
            therapist_id=selected_therapist or "",
        )
        clear_intake(user_id)
        context.user_data.clear()
        if selected_therapist:
            context.user_data["selected_therapist"] = selected_therapist

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
