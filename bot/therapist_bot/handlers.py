import json
import logging
import re
import threading

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import TELEGRAM_TOKEN, THERAPIST_MAP
from bot.therapist_bot.services.relay import get_current_patient, get_patient_for_msg

_END_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔚 End Chat", callback_data="therapist_end")]])
_REG_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
_reg_lock = threading.Lock()

logger = logging.getLogger(__name__)

# Patient bot instance used to deliver therapist replies
_patient_bot = Bot(token=TELEGRAM_TOKEN)


async def start_therapist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond to /start from a therapist."""
    user_id = update.effective_user.id
    if user_id in THERAPIST_MAP:
        name = THERAPIST_MAP[user_id].get("name", "Therapist")
        await update.message.reply_text(
            f"👋 Hello, {name}! You're registered as a therapist.\n\n"
            "Patient messages will appear here when they connect with you.\n"
            "Reply directly to each forwarded message to respond."
        )
    else:
        await update.message.reply_text(
            "👋 Welcome to ZenFlow Therapist Bot!\n\n"
            "To get started, visit the clinic web portal to register "
            "and get your 8-character activation code, then send it here."
        )


async def handle_therapist_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single handler for all therapist bot text messages.

    Routing:
    - Known therapist (in THERAPIST_MAP) → relay reply to patient
    - Unknown sender + valid 8-char code  → registration activation
    - Unknown sender + anything else      → not-registered message
    """
    msg = update.message
    user_id = update.effective_user.id
    text = (msg.text or "").strip()

    if user_id in THERAPIST_MAP:
        await _handle_relay(msg, THERAPIST_MAP[user_id]["id"])
    elif _REG_CODE_RE.match(text):
        await _handle_registration(msg, user_id, text)
    else:
        await msg.reply_text(
            "👋 You're not registered as a therapist on this bot.\n"
            "Visit the clinic web portal to register and get your activation code."
        )


async def _handle_relay(msg, therapist_id: str) -> None:
    """Route a therapist message back to the correct patient.

    If the therapist replies to a specific forwarded message, use that message's
    relay key. Otherwise fall back to their current active patient so they can
    type freely without having to reply to a particular message each time.
    """
    therapist_name = msg.from_user.full_name or "Therapist"

    if msg.reply_to_message:
        info = get_patient_for_msg(msg.reply_to_message.message_id)
        if info is None:
            # The replied-to message has no relay key — fall back to current patient
            patient_id = get_current_patient(therapist_id)
            if patient_id is None:
                await msg.reply_text(
                    "⚠️ Could not find the patient for this message. "
                    "They may have ended the chat or restarted the bot."
                )
                return
        else:
            # Security check: ensure the replying therapist owns this relay session
            if info.get("therapist_id") and info["therapist_id"] != therapist_id:
                await msg.reply_text("⚠️ This message belongs to another therapist's session.")
                logger.warning(
                    f"Therapist {therapist_id} tried to reply to a message owned by {info['therapist_id']}"
                )
                return
            patient_id = info["patient_id"]
    else:
        # No reply-to — use current active patient for this therapist
        patient_id = get_current_patient(therapist_id)
        if patient_id is None:
            await msg.reply_text(
                "⚠️ No active patient chat. Wait for a patient to message you first, "
                "or reply directly to one of their forwarded messages.",
                parse_mode="Markdown",
            )
            return

    try:
        await _patient_bot.send_message(
            chat_id=patient_id,
            text=f"👨‍⚕️ *{therapist_name}:*\n{msg.text}",
            parse_mode="Markdown",
            reply_markup=_END_KB,
        )
        await msg.reply_text("✅ Delivered to patient.")
        logger.info(f"Therapist reply delivered to patient {patient_id}")
    except Exception as e:
        logger.error(f"Could not deliver therapist reply to patient {patient_id}: {e}")
        await msg.reply_text(f"⚠️ Could not deliver to patient {patient_id}.")


async def _handle_registration(msg, user_id: int, code: str) -> None:
    """Activate a therapist via their one-time registration code."""
    from bot.redis_client import get_sync_redis
    r = get_sync_redis()
    raw = r.get(f"zenflow:reg:{code}")
    if not raw:
        await msg.reply_text(
            "❌ Code not found or expired. Please request a new code from the clinic portal."
        )
        return

    info = json.loads(raw)
    entry = _register_therapist_to_db(
        name=info["name"],
        telegram_id=user_id,
        email=info.get("email") or "",
        google_id=info.get("google_id") or "",
    )
    r.delete(f"zenflow:reg:{code}")

    await msg.reply_text(
        f"✅ Welcome, {entry['name']}!\n\n"
        "You're now registered as a therapist. When patients connect with you, "
        "their messages will appear here.\n\n"
        "Reply directly to each forwarded message to respond to the patient."
    )
    logger.info(f"New therapist registered: {entry['name']} (id={entry['id']}, tg={user_id})")


def _register_therapist_to_db(
    name: str, telegram_id: int, email: str = "", google_id: str = ""
) -> dict:
    """Register or update a therapist in SQLite and in-memory config maps.

    Thread-safe via _reg_lock.
    Upsert priority:
      1. Match by email (web-registered therapist with telegram_id=0 → set their telegram_id)
      2. Match by telegram_id (already linked, update name/email)
      3. Create new entry

    Mutates THERAPISTS, THERAPIST_MAP, THERAPIST_BY_ID in bot.config for immediate activation
    without requiring a bot restart.
    """
    from bot import config as _cfg
    from bot.db import get_db

    conn = get_db()

    with _reg_lock:
        # Try email match first (web-registered therapist not yet linked to Telegram)
        existing_row = None
        if email:
            existing_row = conn.execute(
                "SELECT * FROM therapists WHERE lower(email)=?",
                (email.lower(),),
            ).fetchone()

        # Fallback: match by telegram_id
        if existing_row is None:
            existing_row = conn.execute(
                "SELECT * FROM therapists WHERE telegram_id=?",
                (telegram_id,),
            ).fetchone()

        if existing_row:
            updates = ["name=?", "telegram_id=?", "active=1"]
            params: list = [name, telegram_id]
            if email:
                updates.append("email=?")
                params.append(email)
            if google_id:
                updates.append("google_id=?")
                params.append(google_id)
            params.append(existing_row["id"])
            conn.execute(f"UPDATE therapists SET {', '.join(updates)} WHERE id=?", params)
            conn.commit()
            entry_row = conn.execute(
                "SELECT * FROM therapists WHERE id=?", (existing_row["id"],)
            ).fetchone()
        else:
            existing_ids = {r[0] for r in conn.execute("SELECT id FROM therapists").fetchall()}
            n = 1
            while f"t{n}" in existing_ids:
                n += 1
            new_id = f"t{n}"
            conn.execute(
                """INSERT INTO therapists
                   (id, name, telegram_id, email, google_id, calendar_name, active)
                   VALUES (?, ?, ?, ?, ?, 'ZenFlow Availability', 1)""",
                (new_id, name, telegram_id, email or None, google_id or None),
            )
            conn.commit()
            entry_row = conn.execute(
                "SELECT * FROM therapists WHERE id=?", (new_id,)
            ).fetchone()

        entry = dict(entry_row)
        entry["active"] = bool(entry.get("active"))

        # Reload all therapists and mutate in-place so other modules see the change immediately.
        all_rows = conn.execute("SELECT * FROM therapists").fetchall()
        all_therapists = []
        for row in all_rows:
            t = dict(row)
            t["active"] = bool(t.get("active"))
            all_therapists.append(t)

        _cfg.THERAPISTS.clear()
        _cfg.THERAPISTS.extend(all_therapists)
        _cfg.THERAPIST_MAP.clear()
        _cfg.THERAPIST_MAP.update({
            t["telegram_id"]: t
            for t in all_therapists
            if t.get("active") and t.get("telegram_id")
        })
        _cfg.THERAPIST_BY_ID.clear()
        _cfg.THERAPIST_BY_ID.update({t["id"]: t for t in all_therapists if t.get("active")})

    return entry
