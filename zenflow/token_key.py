"""zenflow.token_key — Fernet key derivation for stored Google OAuth tokens + key rotation (F7).

Key material → Fernet key: sha256(material) → 32 bytes → urlsafe-base64. This is exactly the
derivation `web/gcal.py` used with SESSION_SECRET before Phase 0.4, so existing rows keep
decrypting until they are rotated to TOKEN_ENCRYPTION_KEY with `rotate()`.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field

from cryptography.fernet import Fernet, InvalidToken

import bot.db as dbmod


def derive_fernet_key(material: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(material.encode("utf-8")).digest())


def fernet_for(material: str) -> Fernet:
    return Fernet(derive_fernet_key(material))


@dataclass
class RotationReport:
    dry_run: bool
    rotated: int = 0
    already_current: int = 0
    failed: int = 0
    failed_ids: list[str] = field(default_factory=list)
    backup_path: str | None = None

    def summary(self) -> str:
        mode = "DRY RUN — nothing written" if self.dry_run else "applied"
        lines = [
            f"google_tokens key rotation ({mode})",
            f"  rotated:         {self.rotated}",
            f"  already current: {self.already_current}",
            f"  failed:          {self.failed}"
            + (f"  {self.failed_ids}" if self.failed_ids else ""),
        ]
        if self.backup_path:
            lines.append(f"  backup:          {self.backup_path}")
        return "\n".join(lines)


def _backup_database() -> str:
    from zenflow.db_backup import backup_database

    return backup_database("bak")


def rotate(
    *, old_material: str, new_material: str, dry_run: bool, backup: bool = True
) -> RotationReport:
    """Re-encrypt every google_tokens row from `old_material` to `new_material`.

    Rows that already decrypt with the new key are left alone (idempotent). Rows that decrypt
    with neither key are reported, never modified. With `dry_run=True` nothing is written and
    no backup is taken.
    """
    if old_material == new_material:
        raise ValueError("old and new key material are identical — nothing to rotate")
    old, new = fernet_for(old_material), fernet_for(new_material)
    report = RotationReport(dry_run=dry_run)
    conn = dbmod.get_db()
    rows = conn.execute("SELECT therapist_id, encrypted_token FROM google_tokens").fetchall()

    plan: list[tuple[str, bytes]] = []
    for row in rows:
        tid, blob = row["therapist_id"], row["encrypted_token"].encode("utf-8")
        try:
            new.decrypt(blob)
            report.already_current += 1
            continue
        except InvalidToken:
            pass
        try:
            plan.append((tid, old.decrypt(blob)))
        except InvalidToken:
            report.failed += 1
            report.failed_ids.append(tid)

    report.rotated = len(plan)
    if dry_run or not plan:
        return report

    if backup:
        report.backup_path = _backup_database()
    for tid, plaintext in plan:
        conn.execute(
            "UPDATE google_tokens SET encrypted_token=?, updated_at=datetime('now') "
            "WHERE therapist_id=?",
            (new.encrypt(plaintext).decode("utf-8"), tid),
        )
    return report
