"""Phase 9.9 — backups and patient exports are encrypted before they leave the host.

`python -m zenflow.db_backup --encrypt` and `python -m zenflow.patient_export --out f --encrypt`
write Fernet files keyed by `BACKUP_ENCRYPTION_KEY` (its own secret); the plaintext never touches
disk, a wrong key or a damaged file is refused, and without a key nothing is written at all.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

import bot.db as dbmod

pytestmark = pytest.mark.integration

KEY = "backup-key-" + "x" * 40
MARKER = "Yael Backupname"


@pytest.fixture
def backup_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import zenflow.settings as settings_mod

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", KEY)
    settings_mod.reset_settings()
    yield
    settings_mod.reset_settings()


@pytest.fixture
def no_backup_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import zenflow.settings as settings_mod

    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "")
    settings_mod.reset_settings()
    yield
    settings_mod.reset_settings()


def _backups() -> list[Path]:
    db = dbmod.db_path()
    return sorted(db.parent.glob(db.name + ".bak-*"))


def test_an_encrypted_backup_holds_the_database_and_no_plaintext(
    backup_key, make_patient, tmp_path
) -> None:
    from zenflow.db_backup import backup_database
    from zenflow.file_crypto import decrypt_file

    make_patient(MARKER)
    path = backup_database(encrypt=True)
    assert path.endswith(".enc")
    assert [p.name for p in _backups()] == [Path(path).name], "no plaintext copy is left behind"
    assert MARKER.encode() not in Path(path).read_bytes()

    restored = decrypt_file(path, tmp_path / "restored.db")
    names = [r[0] for r in sqlite3.connect(restored).execute("SELECT full_name FROM patients")]
    assert MARKER in names


def test_without_a_key_the_encrypted_backup_writes_nothing(no_backup_key) -> None:
    from zenflow.db_backup import backup_database
    from zenflow.file_crypto import EncryptionUnavailable

    with pytest.raises(EncryptionUnavailable):
        backup_database(encrypt=True)
    assert _backups() == []


def test_a_wrong_key_or_a_damaged_file_is_refused(backup_key, tmp_path) -> None:
    from zenflow.db_backup import backup_database
    from zenflow.file_crypto import decrypt_file

    path = Path(backup_database(encrypt=True))
    with pytest.raises(InvalidToken):
        decrypt_file(path, tmp_path / "x.db", material="another-key-" + "y" * 40)
    damaged = bytearray(path.read_bytes())
    damaged[len(damaged) // 2] ^= 0x01
    path.write_bytes(bytes(damaged))
    with pytest.raises(InvalidToken):
        decrypt_file(path, tmp_path / "x.db")
    assert not (tmp_path / "x.db").exists(), "nothing half-decrypted is written"


def test_the_plain_backup_still_works(no_backup_key) -> None:
    from zenflow.db_backup import backup_database

    path = backup_database()
    assert not path.endswith(".enc")
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM patients").fetchone() is not None


def test_an_encrypted_export_never_writes_plaintext(backup_key, make_patient, tmp_path) -> None:
    from zenflow.file_crypto import decrypt_file
    from zenflow.patient_export import main

    patient = make_patient(MARKER)
    out = tmp_path / "export.json"
    assert main([str(patient["patient_id"]), "--out", str(out), "--encrypt"]) == 0
    assert not out.exists()
    enc = Path(str(out) + ".enc")
    assert MARKER.encode() not in enc.read_bytes()
    data = json.loads(Path(decrypt_file(enc, tmp_path / "plain.json")).read_text("utf-8"))
    assert data["patient"]["full_name"] == MARKER


def test_an_encrypted_export_without_a_key_is_refused(no_backup_key, make_patient, tmp_path):
    from zenflow.patient_export import main

    patient = make_patient(MARKER)
    folder = tmp_path / "exports"
    folder.mkdir()
    out = folder / "export.json"
    assert main([str(patient["patient_id"]), "--out", str(out), "--encrypt"]) == 2
    assert list(folder.iterdir()) == []


def test_the_decrypt_cli_round_trips_and_refuses_a_wrong_key(
    backup_key, tmp_path, monkeypatch, capsys
) -> None:
    import zenflow.settings as settings_mod
    from zenflow.db_backup import backup_database
    from zenflow.file_crypto import main

    path = backup_database(encrypt=True)
    assert main(["decrypt", path, "--out", str(tmp_path / "ok.db")]) == 0
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "not-the-key-" + "z" * 40)
    settings_mod.reset_settings()
    assert main(["decrypt", path, "--out", str(tmp_path / "bad.db")]) == 2
    assert "wrong BACKUP_ENCRYPTION_KEY" in capsys.readouterr().err


def test_the_backup_key_is_a_managed_secret() -> None:
    from zenflow.secrets import SECRET_NAMES

    assert "BACKUP_ENCRYPTION_KEY" in SECRET_NAMES
