"""SQLite-backed bot persistence: in-flight flows survive a restart (plan 2.3).

Why not `PicklePersistence`:
- `bot_data` holds the running follow-up scheduler and job-worker tasks, which cannot be pickled.
- A pickle file is a second, unmanaged copy of patient state next to the database, and loading
  pickle is code execution. JSON rows in the database we already back up avoid both.

What is stored is a whitelist, not a snapshot (plan 2.3: never persist raw clinical free text):
- conversation states, keyed by conversation name and `(chat_id, user_id)`;
- the scheduling keys of `user_data` in `PERSISTED_USER_KEYS`; the cancel list keeps only the
  fields `confirm_cancel()` needs, never summaries or names.
Intake answers stay in Redis under their own TTL. Anything older than `stale_after_seconds` is not
restored: after a long outage the patient starts from the menu (their therapist choice is kept).
"""

from __future__ import annotations

import json
import logging
from collections.abc import MutableMapping
from typing import Any

from telegram.ext import BasePersistence, PersistenceInput

from bot.patient_bot.commands import IN_FLIGHT_KEYS
from zenflow.clock import iso_now, now_utc, parse_iso

logger = logging.getLogger(__name__)

UserData = dict[Any, Any]
ConversationKey = tuple[int | str, ...]

# Keys that belong to one flow and expire with it — the same list `/cancel` clears.
FLOW_KEYS = IN_FLIGHT_KEYS
# Everything that may reach the disk. `selected_therapist` outlives any single flow.
PERSISTED_USER_KEYS = ("selected_therapist", *FLOW_KEYS)
# The fields of a cancel-list entry that `confirm_cancel()` uses — scheduling data only.
CANCEL_ENTRY_FIELDS = ("id", "patient_id", "date", "time", "therapist_id", "gcal_apt_event_id")

_USER = "user"
_CONV = "conv"


def _safe_user_data(data: UserData) -> UserData:
    """The subset of `user_data` that may be written down."""
    out: UserData = {}
    for key in PERSISTED_USER_KEYS:
        if key not in data:
            continue
        value = data[key]
        if key == "apts_to_cancel":
            value = [
                {f: entry.get(f) for f in CANCEL_ENTRY_FIELDS}
                for entry in (value or [])
                if isinstance(entry, dict)
            ]
        out[key] = value
    return out


class SqlitePersistence(BasePersistence[UserData, UserData, UserData]):
    """Conversation states and whitelisted `user_data` in the `bot_persistence` table."""

    def __init__(self, stale_after_seconds: int | None = None, update_interval: float = 60) -> None:
        super().__init__(
            store_data=PersistenceInput(
                bot_data=False, chat_data=False, user_data=True, callback_data=False
            ),
            update_interval=update_interval,
        )
        self.stale_after_seconds = stale_after_seconds

    # ── storage ──
    @staticmethod
    def _db() -> Any:
        from bot.db import get_db

        return get_db()

    def _write(self, kind: str, name: str, key: str, value: object) -> None:
        self._db().execute(
            """INSERT INTO bot_persistence (kind, name, key, value_json, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(kind, name, key)
               DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (kind, name, key, json.dumps(value, ensure_ascii=False), iso_now()),
        )

    def _delete(self, kind: str, name: str, key: str) -> None:
        self._db().execute(
            "DELETE FROM bot_persistence WHERE kind=? AND name=? AND key=?", (kind, name, key)
        )

    def _rows(self, kind: str, name: str) -> list[Any]:
        return list(
            self._db().execute(
                "SELECT key, value_json, updated_at FROM bot_persistence WHERE kind=? AND name=?",
                (kind, name),
            )
        )

    def _is_stale(self, updated_at: str) -> bool:
        if not self.stale_after_seconds:
            return False
        try:
            age = (now_utc() - parse_iso(updated_at)).total_seconds()
        except (TypeError, ValueError):
            return True
        return age > self.stale_after_seconds

    # ── user data ──
    async def get_user_data(self) -> dict[int, UserData]:
        out: dict[int, UserData] = {}
        for row in self._rows(_USER, ""):
            try:
                data = json.loads(row["value_json"])
                user_id = int(row["key"])
            except (TypeError, ValueError):
                logger.warning(f"skipping unreadable persisted user_data row {row['key']!r}")
                continue
            if self._is_stale(row["updated_at"]):
                data = {k: v for k, v in data.items() if k not in FLOW_KEYS}
            out[user_id] = data
        return out

    async def update_user_data(self, user_id: int, data: UserData) -> None:
        self._write(_USER, "", str(user_id), _safe_user_data(data))

    async def refresh_user_data(self, user_id: int, user_data: UserData) -> None:
        return None

    async def drop_user_data(self, user_id: int) -> None:
        self._delete(_USER, "", str(user_id))

    # ── conversations ──
    async def get_conversations(self, name: str) -> MutableMapping[ConversationKey, object]:
        out: dict[ConversationKey, object] = {}
        for row in self._rows(_CONV, name):
            if self._is_stale(row["updated_at"]):
                continue
            try:
                key = tuple(json.loads(row["key"]))
                state = json.loads(row["value_json"])
            except (TypeError, ValueError):
                logger.warning(f"skipping unreadable persisted conversation {row['key']!r}")
                continue
            out[key] = state
        return out

    async def update_conversation(
        self, name: str, key: ConversationKey, new_state: object | None
    ) -> None:
        encoded = json.dumps(list(key))
        if new_state is None:
            self._delete(_CONV, name, encoded)
        else:
            self._write(_CONV, name, encoded, new_state)

    # ── not stored (see PersistenceInput above) ──
    async def get_chat_data(self) -> dict[int, UserData]:
        return {}

    async def update_chat_data(self, chat_id: int, data: UserData) -> None:
        return None

    async def refresh_chat_data(self, chat_id: int, chat_data: UserData) -> None:
        return None

    async def drop_chat_data(self, chat_id: int) -> None:
        return None

    async def get_bot_data(self) -> UserData:
        return {}

    async def update_bot_data(self, data: UserData) -> None:
        return None

    async def refresh_bot_data(self, bot_data: UserData) -> None:
        return None

    async def get_callback_data(self) -> None:
        return None

    async def update_callback_data(self, data: Any) -> None:
        return None

    async def flush(self) -> None:
        return None  # every write is already committed (autocommit connection)
