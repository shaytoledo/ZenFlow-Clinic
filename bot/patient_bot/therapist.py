import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import THERAPIST_BY_ID, THERAPISTS
from bot.interfaces import TelegramChannel
from bot.patient_bot.services.relay import append_history, end_relay, save_relay_mapping
from bot.states import SELECTING, THERAPIST_INPUT, THERAPIST_RELAY, THERAPIST_SELECT
from bot.utils import get_main_keyboard

logger = logging.getLogger(__name__)

_END_KB = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🔚 End Chat", callback_data="therapist_end")]]
)

# Forwards to therapists: a channel over the therapist application's own client, set by
# `bot.main.wire_bots()` at startup. Never a module-level `Bot(token=...)`: an unmanaged client
# is never initialised or shut down (BOT_AUDIT B14, plan 7.1).
_therapist_channel: TelegramChannel | None = None


def _get_therapist(context) -> dict | None:
    """The therapist this patient chose, or None.

    BOT_AUDIT B12: never silently fall back to a different therapist — a patient's message would
    reach someone they did not choose. Only when the patient has chosen nobody and the clinic has
    exactly one active therapist is the choice unambiguous.
    """
    tid = context.user_data.get("selected_therapist")
    if tid:
        chosen = THERAPIST_BY_ID.get(tid)
        return chosen if chosen and chosen.get("active") else None
    active = [t for t in THERAPISTS if t.get("active")]
    return active[0] if len(active) == 1 else None


def _record_relay(
    patient_id: int, message_id: int, therapist_id: str, patient_name: str, text: str
) -> None:
    """Store the routing mapping and the history entry for a message already delivered.

    Best effort on purpose (BOT_AUDIT B9): the therapist has the message either way. Losing the
    mapping only means their reply must go to a newer message, which the therapist bot says
    clearly, so it is logged rather than surfaced to the patient as a failed send.
    """
    try:
        save_relay_mapping(message_id, patient_id, therapist_id, patient_name)
    except Exception as e:
        logger.error(f"[{patient_id}] relay delivered but the mapping was not saved: {e}")
    try:
        append_history(patient_id, "patient", text, therapist_id)
    except Exception as e:
        logger.error(f"[{patient_id}] relay delivered but the history was not saved: {e}")
    _log_relay(patient_id, therapist_id, "sent", provider_message_id=message_id)


def _log_relay(
    telegram_id: int,
    therapist_id: str,
    status: str,
    *,
    provider_message_id: int | None = None,
    error: str | None = None,
) -> None:
    """The patient wrote to their therapist (plan 8.3) — who, when and whether it arrived, never
    the message itself."""
    from web.repositories import message_log_repo

    message_log_repo.record_relay(
        direction="in",
        channel="telegram",
        external_id=telegram_id,
        therapist_id=therapist_id,
        status=status,
        provider_message_id=provider_message_id,
        error=error,
    )


async def show_therapist_for_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask patient which therapist they want to contact, then prompt for message."""
    query = update.callback_query
    await query.answer()

    active = [t for t in THERAPISTS if t.get("active")]
    if not active:
        await query.edit_message_text(
            "No therapists are available right now. Please try again later.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    if len(active) == 1:
        context.user_data["selected_therapist"] = active[0]["id"]
        await query.edit_message_text(
            "What would you like to say to the therapist?\n\nType your message below:"
        )
        return THERAPIST_INPUT

    # Already chose a therapist this session — skip re-selection
    existing = context.user_data.get("selected_therapist")
    if existing and any(t["id"] == existing for t in active):
        await query.edit_message_text(
            "What would you like to say to the therapist?\n\nType your message below:"
        )
        return THERAPIST_INPUT

    context.user_data["therapist_flow"] = "contact"
    keyboard = [[InlineKeyboardButton(t["name"], callback_data=f"sel_t_{t['id']}")] for t in active]
    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_main")])
    await query.edit_message_text(
        "Choose your therapist:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return THERAPIST_SELECT


async def ask_therapist_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Prompt the patient to type their first message."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "What would you like to say to the therapist?\n\nType your message below:"
    )
    return THERAPIST_INPUT


async def start_relay(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """First patient message — open two-way relay with the therapist."""
    user = update.effective_user
    therapist = _get_therapist(context)

    if not therapist:
        # Their therapist is inactive or was never chosen and the clinic has several.
        context.user_data.pop("selected_therapist", None)
        await update.message.reply_text(
            "Your therapist is not available right now. Please choose a therapist again.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING
    if not _therapist_channel:
        logger.error("Therapist bot not configured")
        await update.message.reply_text(
            "Sorry, the therapist connection is not configured yet.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    patient_name = user.full_name or user.first_name or ""
    try:
        # Plain text: a patient name or message containing _ * ` [ made Telegram reject the
        # whole message when it was parsed as Markdown (BOT_AUDIT B2).
        sent = await _therapist_channel.send_text(
            therapist["telegram_id"],
            f"💬 New message from {patient_name} (ID: {user.id})\n\n{update.message.text}",
        )
    except Exception as e:
        logger.error(f"[{user.id}] failed to forward to therapist bot: {e}")
        _log_relay(user.id, therapist["id"], "failed", error=str(e))
        await update.message.reply_text(
            "Could not reach the therapist right now. Please try again later.",
            reply_markup=get_main_keyboard(),
        )
        return SELECTING

    # The therapist has the message. A Redis failure past this point costs the reply routing, not
    # the delivery, so the patient must not be told it failed (BOT_AUDIT B9).
    _record_relay(
        user.id, int(sent.message_id or 0), therapist["id"], patient_name, update.message.text
    )
    logger.info(
        f"[{user.id}] relay opened via therapist bot, msg_id={sent.message_id}, therapist={therapist['id']}"
    )

    await update.message.reply_text(
        "✅ *Message sent to the therapist!*\n\n"
        "Keep typing here — your messages will be forwarded.\n"
        "Press *End Chat* when you're done.",
        parse_mode="Markdown",
        reply_markup=_END_KB,
    )
    return THERAPIST_RELAY


async def relay_to_therapist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Relay subsequent patient messages to the therapist."""
    user = update.effective_user
    therapist = _get_therapist(context)

    if not _therapist_channel or not therapist:
        await update.message.reply_text("⚠️ Therapist not available.", reply_markup=_END_KB)
        return THERAPIST_RELAY

    patient_name = user.full_name or user.first_name or ""
    try:
        sent = await _therapist_channel.send_text(
            therapist["telegram_id"], f"💬 {patient_name}:\n{update.message.text}"
        )
    except Exception as e:
        logger.error(f"[{user.id}] relay failed: {e}")
        _log_relay(user.id, therapist["id"], "failed", error=str(e))
        await update.message.reply_text(
            "⚠️ Could not forward your message. Please try again.", reply_markup=_END_KB
        )
        return THERAPIST_RELAY

    _record_relay(
        user.id, int(sent.message_id or 0), therapist["id"], patient_name, update.message.text
    )
    logger.info(f"[{user.id}] relayed via therapist bot, msg_id={sent.message_id}")
    await update.message.reply_text("✅ Sent.", reply_markup=_END_KB)
    return THERAPIST_RELAY


async def relay_unsupported_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Patient sent a photo/voice/file during a therapist chat (BOT_AUDIT B7).

    The chat stays open and the patient is told it was not delivered, instead of the message
    silently ending the relay. Whether media should be forwarded (and stored — it may be PHI)
    is open question Q6.
    """
    await update.message.reply_text(
        "📎 I can't send photos, voice notes or files to your therapist yet — "
        "please describe it in text, or bring it to your session.",
        reply_markup=_END_KB,
    )
    return THERAPIST_RELAY


async def end_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Patient ends the relay session."""
    query = update.callback_query
    await query.answer()
    end_relay(update.effective_user.id)
    logger.info(f"[{update.effective_user.id}] ended therapist chat")
    await query.edit_message_text(
        "Chat ended. If the therapist replies you'll still receive it here.\n\n"
        "What else can I help you with?",
        reply_markup=get_main_keyboard(),
    )
    return SELECTING
