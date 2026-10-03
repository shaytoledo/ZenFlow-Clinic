"""
bot/services/media_relay.py — photos, voice notes and files between patient and therapist.

Owner decision Q6 (2026-10-02): yes, relay them; switched on with ZF_RELAY_MEDIA=1.

Neither bot can forward a message the other bot received, so a file is downloaded through the
bot that received it and sent again through the other one. Nothing about it is kept:
- the bytes stay in memory for the length of the call, never written to disk or the database;
- nothing is logged but the kind and the size (message_log gets the delivery, never the content).

Two checks run BEFORE anything is downloaded: the kind/type must be allowed (pictures, voice and
audio, mp4 video, PDF), and the size must be at most ZF_RELAY_MEDIA_MAX_MB, which Telegram tells
us up front. The size is checked again on the downloaded bytes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bot.interfaces.channel import OutboundMedia, SentMessage

#: Telegram photos are JPEG and carry no mime type; everything else must declare one of these
ALLOWED_TYPES: dict[str, frozenset[str]] = {
    "image": frozenset({"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}),
    "audio": frozenset({"audio/ogg", "audio/mpeg", "audio/mp4", "audio/x-m4a", "audio/aac"}),
    "video": frozenset({"video/mp4"}),
    "document": frozenset({"application/pdf", "image/jpeg", "image/png", "image/heic"}),
}
DEFAULT_NAMES = {
    "image": "photo.jpg",
    "audio": "voice.ogg",
    "video": "video.mp4",
    "document": "file",
}


class MediaRefused(Exception):
    """Not relayed: the reason is safe to show to the sender."""


@dataclass(frozen=True)
class Attachment:
    kind: str  # bot.interfaces MEDIA_KINDS
    file_id: str
    file_size: int | None
    mime_type: str | None
    filename: str


def attachment_of(message: Any) -> Attachment | None:
    """The one file a Telegram message carries (the largest photo size), or None."""
    photos = getattr(message, "photo", None)
    if photos and getattr(photos[-1], "file_id", None):
        best = photos[-1]  # Telegram lists sizes in ascending order
        return Attachment(
            "image", best.file_id, getattr(best, "file_size", None), "image/jpeg", "photo.jpg"
        )
    for attr, kind in (("voice", "audio"), ("audio", "audio"), ("video", "video"),
                       ("video_note", "video"), ("document", "document")):  # fmt: skip
        item = getattr(message, attr, None)
        if item is not None and getattr(item, "file_id", None):
            name = _safe_name(getattr(item, "file_name", None)) or DEFAULT_NAMES[kind]
            mime = getattr(item, "mime_type", None) or ("audio/ogg" if attr == "voice" else None)
            if attr == "video_note":
                mime = mime or "video/mp4"
            return Attachment(kind, item.file_id, getattr(item, "file_size", None), mime, name)
    return None


def _safe_name(name: str | None) -> str | None:
    """A file name without any path, control characters or length beyond 80."""
    if not name:
        return None
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    clean = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    return clean[:80] or None


def max_bytes() -> int:
    from zenflow.settings import get_settings

    return get_settings().flags.relay_media_max_mb * 1024 * 1024


def check(attachment: Attachment) -> None:
    """Raise MediaRefused unless this attachment may be relayed — before downloading it."""
    allowed = ALLOWED_TYPES.get(attachment.kind, frozenset())
    if (attachment.mime_type or "").lower() not in allowed:
        raise MediaRefused(
            "This type of file can't be sent. Photos, voice notes, mp4 videos and PDFs can."
        )
    limit = max_bytes()
    if attachment.file_size and attachment.file_size > limit:
        raise MediaRefused(
            f"This file is too large to send (the limit is {limit // (1024 * 1024)} MB)."
        )


async def relay(
    attachment: Attachment,
    *,
    from_bot: Any,
    channel: Any,
    recipient_id: int | str,
    caption: str,
) -> SentMessage:
    """Download through `from_bot`, send through `channel`; nothing is kept."""
    check(attachment)
    limit = max_bytes()
    file = await from_bot.get_file(attachment.file_id)
    if (getattr(file, "file_size", None) or 0) > limit:
        raise MediaRefused(
            f"This file is too large to send (the limit is {limit // (1024 * 1024)} MB)."
        )
    data = bytes(await file.download_as_bytearray())
    if len(data) > limit:
        raise MediaRefused(
            f"This file is too large to send (the limit is {limit // (1024 * 1024)} MB)."
        )
    return await channel.send_media(  # type: ignore[no-any-return]
        recipient_id,
        OutboundMedia(
            kind=attachment.kind,
            data=data,
            filename=attachment.filename,
            mime_type=attachment.mime_type,
            caption=caption[:1000],
        ),
    )
