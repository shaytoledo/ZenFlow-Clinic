from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Schedule Appointment", callback_data="schedule")],
        [InlineKeyboardButton("❌ Cancel Appointment",   callback_data="cancel")],
        [InlineKeyboardButton("💬 Connect to Therapist", callback_data="therapist")],
    ])
