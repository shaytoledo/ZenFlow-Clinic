import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPISTS
from bot.locales import get_lang, t
from bot.patient_bot.services.ai_intake import (
    clear_intake,
    get_history_dicts,
    get_next_question,
    initialize_intake,
)
from bot.patient_bot.services.appointments import (
    SlotTaken,
    save_appointment,
    save_treatment_notes,
    set_gcal_event_id,
)
from bot.patient_bot.services.availability import book_slot, get_available_days, get_available_hours
from bot.states import (
    INTAKE,
    INTAKE_CONFIRM,
    SCHEDULE_DAY,
    SCHEDULE_HOUR,
    SCHEDULE_WEEK,
    SELECTING,
    THERAPIST_INPUT,
    THERAPIST_SELECT,
)
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return get_lang(context.user_data.get("selected_therapist"))


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

    lang = _lang(context)
    active = [th for th in THERAPISTS if th.get("active")]
    if not active:
        await query.edit_message_text(
            t("bot_no_therapists_contact", lang),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")]]
            ),
        )
        return SELECTING

    existing = context.user_data.get("selected_therapist")
    if len(active) == 1:
        context.user_data["selected_therapist"] = active[0]["id"]
        return await show_week_choice(update, context)
    if existing and any(th["id"] == existing for th in active):
        return await show_week_choice(update, context)

    keyboard = [
        [InlineKeyboardButton(th["name"], callback_data=f"sel_t_{th['id']}")] for th in active
    ]
    keyboard.append([InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")])
    await query.edit_message_text(
        t("bot_choose_therapist", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return THERAPIST_SELECT


async def select_therapist_and_continue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Patient chose a therapist — route to welcome / schedule / contact flow."""
    query = update.callback_query
    therapist_id = query.data.replace("sel_t_", "")
    context.user_data["selected_therapist"] = therapist_id
    flow = context.user_data.pop("therapist_flow", "schedule")

    # Language is now known
    lang = get_lang(therapist_id)

    if flow == "contact":
        await query.answer()
        await query.edit_message_text(t("bot_message_therapist_prompt", lang))
        return THERAPIST_INPUT

    if flow == "welcome":
        therapist = next((th for th in THERAPISTS if th["id"] == therapist_id), None)
        t_name = therapist["name"] if therapist else "your therapist"
        await query.answer()
        await query.edit_message_text(
            t("bot_working_with", lang, name=t_name),
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(lang),
        )
        return SELECTING

    return await show_week_choice(update, context)


# ── week / day / hour selection ───────────────────────────────────────────────


async def show_week_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 1 — ask whether the patient wants this week or next week."""
    query = update.callback_query
    await query.answer()
    logger.info(f"[{update.effective_user.id}] show_week_choice")

    lang = _lang(context)
    keyboard = [
        [InlineKeyboardButton(t("bot_this_week", lang), callback_data="week_0")],
        [InlineKeyboardButton(t("bot_next_week", lang), callback_data="week_1")],
        [InlineKeyboardButton(t("bot_back", lang), callback_data="back_main")],
    ]
    await query.edit_message_text(
        t("bot_which_week", lang),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SCHEDULE_WEEK


async def show_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 2 — show days that have available slots in the chosen week."""
    query = update.callback_query
    await query.answer()

    if query.data.startswith("week_"):
        week_offset = int(query.data.replace("week_", ""))
        context.user_data["selected_week"] = week_offset
    else:
        week_offset = context.user_data.get("selected_week", 0)

    lang = _lang(context)
    week_label = (
        t("bot_label_this_week", lang) if week_offset == 0 else t("bot_label_next_week", lang)
    )
    logger.info(f"[{update.effective_user.id}] show_days week_offset={week_offset}")

    therapist_id = context.user_data.get("selected_therapist")
    days = await get_available_days(week_offset=week_offset, therapist_id=therapist_id)
    if not days:
        await query.edit_message_text(
            t("bot_no_slots_week", lang, week=week_label.lower()),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t("bot_back", lang), callback_data="back_week")]]
            ),
        )
        return SCHEDULE_WEEK

    keyboard = [
        [InlineKeyboardButton(d.strftime("%A, %d %b"), callback_data=f"day_{d.isoformat()}")]
        for d in days
    ]
    keyboard.append([InlineKeyboardButton(t("bot_back", lang), callback_data="back_week")])
    await query.edit_message_text(
        t("bot_choose_day", lang, week=week_label),
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

    lang = _lang(context)
    therapist_id = context.user_data.get("selected_therapist")
    hours = await get_available_hours(selected_day, therapist_id=therapist_id)
    if not hours:
        await query.edit_message_text(
            t("bot_no_hours", lang),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(t("bot_back", lang), callback_data="back_days")]]
            ),
        )
        return SCHEDULE_DAY

    keyboard = [[InlineKeyboardButton(h, callback_data=f"hour_{h}")] for h in hours]
    keyboard.append([InlineKeyboardButton(t("bot_back", lang), callback_data="back_days")])
    await query.edit_message_text(
        t("bot_choose_hour", lang, day=selected_day.strftime("%A, %d %b")),
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
    lang = _lang(context)
    logger.info(f"[{update.effective_user.id}] slot chosen {day} {time_slot}")

    keyboard = [
        [InlineKeyboardButton(t("bot_yes_intake", lang), callback_data="intake_yes")],
        [InlineKeyboardButton(t("bot_skip_intake", lang), callback_data="intake_no")],
    ]
    await query.edit_message_text(
        t("bot_intake_prompt", lang, day=day.strftime("%A, %d %b"), time=time_slot),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return INTAKE_CONFIRM


async def start_intake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User said YES — begin adaptive intake questionnaire."""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    lang = _lang(context)
    opening_q = t("bot_intake_q1", lang)

    context.user_data["intake_count"] = 0
    initialize_intake(user_id, opening_q)

    await query.edit_message_text(t("bot_intake_start", lang))
    await context.bot.send_message(chat_id=update.effective_chat.id, text=opening_q)
    return INTAKE


async def skip_intake(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User said NO — save appointment immediately without intake details."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    day = date.fromisoformat(context.user_data["selected_day"])
    time_slot = context.user_data["selected_time"]
    lang = _lang(context)
    selected_therapist = context.user_data.get("selected_therapist")

    # Claim the slot in the database first: if someone else took this hour while the patient was
    # deciding, nothing has been removed from the availability calendar yet (BOT_AUDIT B4).
    try:
        appointment_id = save_appointment(
            patient_id=user.id,
            patient_name=user.full_name or user.first_name,
            day=day,
            time_slot=time_slot,
            intake_history=[],
            summary="",
            therapist_id=selected_therapist or "",
        )
    except SlotTaken:
        return await _slot_taken(query, context, lang, day, time_slot)

    await _release_hour(
        day,
        time_slot,
        user.full_name or user.first_name,
        "Patient opted to skip the intake questionnaire.",
        selected_therapist,
        appointment_id,
    )
    save_treatment_notes(appointment_id, user.id, {})
    _forget_intake(user.id)
    logger.info(f"[{user.id}] appointment saved (no intake)")
    context.user_data.clear()
    if selected_therapist:
        context.user_data["selected_therapist"] = selected_therapist

    await query.edit_message_text(
        t("bot_booked", lang, day=day.strftime("%A, %d %B %Y"), time=time_slot),
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(lang),
    )
    return SELECTING


def _forget_intake(user_id: int) -> None:
    """Drop the cached intake history, tolerating a Redis outage (BOT_AUDIT B9).

    The appointment is already saved at this point; failing to clear a cache must never cost the
    patient their confirmation message.
    """
    try:
        clear_intake(user_id)
    except Exception as e:
        logger.error(f"[{user_id}] intake history not cleared: {e}")


async def _release_hour(
    day: date,
    time_slot: str,
    patient_name: str,
    summary: str,
    therapist_id: str | None,
    appointment_id: int,
) -> str | None:
    """Remove the booked hour from availability and store the calendar event id.

    Runs after the appointment row exists. A calendar failure must not undo a confirmed booking,
    so it is logged and the patient still gets their confirmation.
    """
    try:
        gcal_id = await book_slot(day, time_slot, patient_name, summary, therapist_id=therapist_id)
    except Exception as e:
        logger.error(f"appointment {appointment_id} saved but the calendar update failed: {e}")
        return None
    set_gcal_event_id(appointment_id, gcal_id)
    return gcal_id


async def _slot_taken(query, context, lang: str, day: date, time_slot: str) -> int:
    """Tell the patient their hour is gone and send them back to the menu (BOT_AUDIT B4)."""
    selected_therapist = context.user_data.get("selected_therapist")
    context.user_data.clear()
    if selected_therapist:
        context.user_data["selected_therapist"] = selected_therapist
    await query.edit_message_text(
        t("bot_slot_taken", lang, day=day.strftime("%A, %d %B %Y"), time=time_slot),
        parse_mode="Markdown",
        reply_markup=get_main_keyboard(lang),
    )
    return SELECTING


# ── generation hand-off (Phase 3.1) ───────────────────────────────────────────


def _intake_snapshot(user_id: int, final_answer: str) -> list[dict]:
    """The whole intake conversation, final answer included, as plain dicts."""
    try:
        history = get_history_dicts(user_id)
    except Exception as e:  # Redis unavailable: keep at least the answer we are holding
        logger.error(f"[{user_id}] intake history unreadable, saving the final answer only: {e}")
        history = []
    return [*history, {"role": "user", "content": final_answer}]


def _start_generation(appointment_id: int, user_id: int) -> None:
    """Queue the summary → diagnosis → points jobs. The booking stands even if this fails."""
    from bot.services.pipeline_jobs import FAILED, start_intake_pipeline

    try:
        start_intake_pipeline(appointment_id, user_id)
    except Exception:
        logger.exception(f"[{user_id}] could not queue generation for {appointment_id}")
        try:
            from web.repositories.treatment_repo import set_points_status

            set_points_status(appointment_id, FAILED)
        except Exception:
            logger.exception("could not mark the session FAILED either")


# ── background helpers ────────────────────────────────────────────────────────


# ── intake answers ────────────────────────────────────────────────────────────


async def handle_intake_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user_answer = update.message.text
    intake_count = context.user_data.get("intake_count", 0) + 1
    context.user_data["intake_count"] = intake_count
    logger.info(f"[{user_id}] intake answer {intake_count}/5")

    if intake_count >= 5:
        user = update.effective_user
        day = date.fromisoformat(context.user_data["selected_day"])
        time_slot = context.user_data["selected_time"]
        selected_therapist = context.user_data.get("selected_therapist")
        lang = get_lang(selected_therapist)
        patient_name = user.full_name or user.first_name

        # The conversation goes into the database with the booking: the generation jobs read
        # it from there, because the Redis intake history expires and does not survive a
        # restart (Phase 3.1).
        history = _intake_snapshot(user_id, user_answer)
        try:
            appointment_id = save_appointment(
                patient_id=user_id,
                patient_name=patient_name,
                day=day,
                time_slot=time_slot,
                intake_history=history,
                summary="",
                therapist_id=selected_therapist or "",
            )
        except SlotTaken:
            _forget_intake(user_id)
            context.user_data.clear()
            if selected_therapist:
                context.user_data["selected_therapist"] = selected_therapist
            await update.message.reply_text(
                t("bot_slot_taken", lang, day=day.strftime("%A, %d %b"), time=time_slot),
                parse_mode="Markdown",
                reply_markup=get_main_keyboard(lang),
            )
            return SELECTING

        await _release_hour(
            day,
            time_slot,
            patient_name,
            "Intake in progress — AI summary pending.",
            selected_therapist,
            appointment_id,
        )
        save_treatment_notes(appointment_id, user_id, {})
        _start_generation(appointment_id, user_id)
        _forget_intake(user_id)

        context.user_data.clear()
        if selected_therapist:
            context.user_data["selected_therapist"] = selected_therapist

        await update.message.reply_text(
            t("bot_booked", lang, day=day.strftime("%A, %d %b"), time=time_slot),
            parse_mode="Markdown",
            reply_markup=get_main_keyboard(lang),
        )
        return SELECTING

    therapist_id = context.user_data.get("selected_therapist")
    lang = get_lang(therapist_id)
    next_q = await get_next_question(user_id, user_answer, lang=lang)
    await update.message.reply_text(next_q)
    return INTAKE
