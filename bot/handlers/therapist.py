import json
import os
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot.config import THERAPIST_DIR
from bot.states import SELECTING, THERAPIST_INPUT
from bot.utils import get_main_keyboard


async def ask_therapist_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt the patient to type their message."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "What would you like to say to the therapist?\n\nType your message below:"
    )
    return THERAPIST_INPUT


async def forward_to_therapist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Save the patient's message and confirm delivery."""
    user = update.effective_user
    os.makedirs(THERAPIST_DIR, exist_ok=True)

    data = {
        "patient_id": user.id,
        "patient_name": user.full_name or user.first_name,
        "message": update.message.text,
        "sent_at": datetime.now().isoformat(),
    }
    filename = f"{user.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(os.path.join(THERAPIST_DIR, filename), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    await update.message.reply_text(
        "Thank you! The therapist will connect with you or answer soon.\n\n"
        "Bye for now! 👋"
    )
    await update.message.reply_text(
        "If you need anything else, just send a message.",
        reply_markup=get_main_keyboard(),
    )
    return SELECTING
