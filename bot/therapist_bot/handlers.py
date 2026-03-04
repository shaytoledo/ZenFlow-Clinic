import json
import logging
import re
import threading
from pathlib import Path

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.config import TELEGRAM_TOKEN, THERAPIST_MAP
from bot.therapist_bot.services.relay import get_patient_for_msg

_END_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🔚 End Chat", callback_data="therapist_end")]])
_REG_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")
_reg_lock = threading.Lock()

logger = logging.getLogger(__name__)

# Patient bot instance used to deliver therapist replies
_patient_bot = Bot(token=TELEGRAM_TOKEN)


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
    """Route a therapist reply back to the correct patient."""
    therapist_name = msg.from_user.full_name or "Therapist"

    if not msg.reply_to_message:
        await msg.reply_text(
            "⚠️ Please *reply directly* to the patient's message so I know who to send it to.",
            parse_mode="Markdown",
        )
        return

    info = get_patient_for_msg(msg.reply_to_message.message_id)
    if info is None:
        await msg.reply_text(
            "⚠️ Could not find the patient for this message. They may have restarted the bot."
        )
        return

    # Security check: ensure the replying therapist owns this relay session
    if info.get("therapist_id") and info["therapist_id"] != therapist_id:
        await msg.reply_text("⚠️ This message belongs to another therapist's session.")
        logger.warning(
            f"Therapist {therapist_id} tried to reply to a message owned by {info['therapist_id']}"
        )
        return

    patient_id = info["patient_id"]
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
    entry = _register_therapist_to_file(
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


def _register_therapist_to_file(
    name: str, telegram_id: int, email: str = "", google_id: str = ""
) -> dict:
    """Register or update a therapist in data/therapists.json and in-memory config maps.

    Thread-safe via _reg_lock.
    Upsert priority:
      1. Match by email (web-registered therapist with telegram_id=0 → set their telegram_id)
      2. Match by telegram_id (already linked, update name/email)
      3. Create new entry

    Mutates THERAPISTS, THERAPIST_MAP, THERAPIST_BY_ID in bot.config for immediate activation
    without requiring a bot restart.
    """
    from bot import config as _cfg

    path = Path(_cfg.DATA_DIR).parent / "therapists.json"

    with _reg_lock:
        therapists = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

        # Try email match first (web-registered therapist not yet linked to Telegram)
        existing = None
        if email:
            existing = next(
                (t for t in therapists if (t.get("email") or "").lower() == email.lower()),
                None,
            )
        # Fallback: match by telegram_id
        if existing is None:
            existing = next((t for t in therapists if t.get("telegram_id") == telegram_id), None)

        if existing:
            existing["name"] = name
            existing["telegram_id"] = telegram_id
            existing["active"] = True
            if email:
                existing["email"] = email
            if google_id:
                existing["google_id"] = google_id
            entry = existing
        else:
            existing_ids = {t["id"] for t in therapists}
            n = 1
            while f"t{n}" in existing_ids:
                n += 1
            entry = {
                "id": f"t{n}",
                "name": name,
                "telegram_id": telegram_id,
                "calendar_name": "ZenFlow Availability",
                "active": True,
            }
            if email:
                entry["email"] = email
            if google_id:
                entry["google_id"] = google_id
            therapists.append(entry)

        path.write_text(json.dumps(therapists, indent=2, ensure_ascii=False), encoding="utf-8")

        # Mutate in-place so modules that did `from bot.config import THERAPISTS`
        # (e.g. schedule.py) see the change immediately without a restart.
        _cfg.THERAPISTS.clear()
        _cfg.THERAPISTS.extend(therapists)
        _cfg.THERAPIST_MAP.clear()
        _cfg.THERAPIST_MAP.update({t["telegram_id"]: t for t in therapists if t.get("active")})
        _cfg.THERAPIST_BY_ID.clear()
        _cfg.THERAPIST_BY_ID.update({t["id"]: t for t in therapists if t.get("active")})

    return entry
