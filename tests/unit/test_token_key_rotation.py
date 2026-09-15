"""Phase 0.4 / F7 — re-encrypt google_tokens from the SESSION_SECRET-derived key to
TOKEN_ENCRYPTION_KEY, with a dry-run mode and a backup step."""

from __future__ import annotations

from pathlib import Path

import pytest

import bot.db as dbmod
from zenflow import settings as S
from zenflow import token_key


def _seed(rows: dict[str, str], material: str) -> None:
    f = token_key.fernet_for(material)
    conn = dbmod.get_db()
    for tid, plaintext in rows.items():
        conn.execute(
            "INSERT INTO google_tokens (therapist_id, encrypted_token, scopes) VALUES (?, ?, 'x')",
            (tid, f.encrypt(plaintext.encode()).decode()),
        )


def _plaintexts(material: str) -> dict[str, str]:
    f = token_key.fernet_for(material)
    return {
        r["therapist_id"]: f.decrypt(r["encrypted_token"].encode()).decode()
        for r in dbmod.get_db().execute("SELECT * FROM google_tokens")
    }


OLD = "old-session-secret-material-0123456789"
NEW = "new-dedicated-token-key-0123456789abcdef"


def test_fernet_key_derivation_matches_legacy_gcal_derivation() -> None:
    from web.gcal import _fernet

    S.reset_settings()
    legacy = _fernet()
    mine = token_key.fernet_for(S.get_settings().token_key_material)
    token = legacy.encrypt(b"abc")
    assert mine.decrypt(token) == b"abc"


def test_dry_run_changes_nothing(db: Path) -> None:
    _seed({"t1": '{"tok": 1}', "t2": '{"tok": 2}'}, OLD)
    report = token_key.rotate(old_material=OLD, new_material=NEW, dry_run=True)
    assert report.rotated == 2 and report.already_current == 0 and report.failed == 0
    assert _plaintexts(OLD) == {"t1": '{"tok": 1}', "t2": '{"tok": 2}'}
    assert report.backup_path is None


def test_rotation_re_encrypts_every_row_and_is_idempotent(db: Path) -> None:
    _seed({"t1": '{"tok": 1}', "t2": '{"tok": 2}'}, OLD)
    report = token_key.rotate(old_material=OLD, new_material=NEW, dry_run=False)
    assert report.rotated == 2 and report.failed == 0
    assert report.backup_path is not None and Path(report.backup_path).exists()
    assert _plaintexts(NEW) == {"t1": '{"tok": 1}', "t2": '{"tok": 2}'}
    # second run: everything already current, nothing rewritten
    again = token_key.rotate(old_material=OLD, new_material=NEW, dry_run=False)
    assert again.rotated == 0 and again.already_current == 2 and again.failed == 0


def test_rotation_reports_rows_it_cannot_decrypt(db: Path) -> None:
    _seed({"t1": '{"tok": 1}'}, OLD)
    _seed({"t9": "garbage"}, "some-other-material-that-nobody-knows-9")
    report = token_key.rotate(old_material=OLD, new_material=NEW, dry_run=False)
    assert report.rotated == 1 and report.failed == 1
    assert "t9" in report.failed_ids


def test_rotation_refuses_identical_materials() -> None:
    with pytest.raises(ValueError, match="identical"):
        token_key.rotate(old_material=OLD, new_material=OLD, dry_run=True)
