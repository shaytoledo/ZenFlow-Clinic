from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_keyboard(lang: str = "en", show_change_therapist: bool = True) -> InlineKeyboardMarkup:
    from bot.locales import t
    rows = [
        [InlineKeyboardButton("📅 " + t("bot_schedule",        lang), callback_data="schedule")],
        [InlineKeyboardButton("❌ " + t("bot_cancel",           lang), callback_data="cancel")],
        [InlineKeyboardButton("💬 " + t("bot_therapist",        lang), callback_data="therapist")],
    ]
    if show_change_therapist:
        rows.append([InlineKeyboardButton("👨‍⚕️ " + t("bot_change_therapist", lang), callback_data="change_therapist")])
    return InlineKeyboardMarkup(rows)
