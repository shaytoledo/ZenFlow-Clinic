"""The 24h follow-up conversation gate (BOT_AUDIT B3) and its buttons (Phase 6.2).

The follow-up is not part of the booking conversation: it arrives on its own schedule and the
patient answers whenever they read it — which may be in the middle of an intake, inside a
therapist chat, or at the main menu. Consuming it only in `start()` meant a "7" typed while in
`INTAKE` was fed to the intake LLM and a "much better" typed in `THERAPIST_RELAY` was forwarded to
the therapist, while the follow-up itself never advanced.

Both handlers are registered in a lower handler group than the ConversationHandler, so they see
every text message and every `fu:` button first. When the update belongs to an open check-in they
answer and raise `ApplicationHandlerStop`, which keeps it away from the conversation *without*
changing the patient's state — they stay exactly where they were. Otherwise they do nothing.
"""

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

logger = logging.getLogger(__name__)


def _markup(buttons: list[list[tuple[str, str]]] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label, callback_data=data) for label, data in row]
            for row in buttons
        ]
    )


async def _send_prompt(message: Any, prompt: Any) -> None:
    await message.reply_text(
        prompt.text, parse_mode="Markdown", reply_markup=_markup(prompt.buttons)
    )


async def handle_followup_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Answer a typed check-in message, or let the update through untouched."""
    message = update.message
    user = update.effective_user
    if message is None or user is None or not message.text:
        return

    from bot.services.followup_scheduler import consume_followup_conversation

    consumed, prompt = await consume_followup_conversation(user.id, message.text)
    if not consumed:
        return
    if prompt is not None:
        await _send_prompt(message, prompt)
    logger.info(f"[{user.id}] follow-up answer consumed before the conversation handler")
    raise ApplicationHandlerStop


async def handle_followup_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """A tapped check-in button (`fu:<appointment>:<step>:<value>`)."""
    query = update.callback_query
    user = update.effective_user
    if query is None or user is None or not (query.data or "").startswith("fu:"):
        return

    from bot.services.followup_scheduler import consume_followup_button

    result = await consume_followup_button(user.id, query.data or "")
    if not result.consumed:
        return
    await query.answer(result.toast or None)
    try:
        # A toggle redraws the buttons; an answered question loses them (no double answers).
        await query.edit_message_reply_markup(reply_markup=_markup(result.keep_buttons))
    except Exception as e:  # an unchanged or too-old message — the answer still counts
        logger.debug(f"follow-up buttons not updated: {e}")
    if result.prompt is not None and query.message is not None:
        await _send_prompt(query.message, result.prompt)
    logger.info(f"[{user.id}] follow-up button consumed")
    raise ApplicationHandlerStop
