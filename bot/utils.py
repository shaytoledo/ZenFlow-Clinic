from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_keyboard(show_change_therapist: bool = True) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("📅 Schedule Appointment", callback_data="schedule")],
        [InlineKeyboardButton("❌ Cancel Appointment",   callback_data="cancel")],
        [InlineKeyboardButton("💬 Connect to Therapist", callback_data="therapist")],
    ]
    if show_change_therapist:
        rows.append([InlineKeyboardButton("👨‍⚕️ Change Therapist", callback_data="change_therapist")])
    return InlineKeyboardMarkup(rows)
