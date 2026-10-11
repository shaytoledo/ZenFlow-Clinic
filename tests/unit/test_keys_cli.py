"""Phase 14.2 — the keys the project generates live as files in a git-ignored folder (Q15).

The owner asked for the encryption keys to be kept in the project folder, outside git.
`python -m zenflow.keys` creates them without ever showing a value.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from zenflow import keys

ROOT = Path(__file__).resolve().parents[2]


def _values(folder: Path) -> dict[str, str]:
    return {name: (folder / name).read_text(encoding="utf-8") for name in keys.GENERATED}


def test_init_creates_every_key_and_shows_none(tmp_path: Path, capsys) -> None:
    folder = tmp_path / "keys"
    assert keys.main(["init", "--dir", str(folder)]) == 0
    shown = capsys.readouterr()
    values = _values(folder)
    assert set(values) == set(keys.GENERATED)
    for name, value in values.items():
        assert len(value) >= 48, name
        assert value not in shown.out and value not in shown.err, f"{name} was printed"
        assert f"{name}: created" in shown.out
    assert len(set(values.values())) == len(values), "the app refuses two keys with one value"


def test_the_webhook_secret_uses_only_what_telegram_accepts(tmp_path: Path) -> None:
    keys.init(tmp_path)
    secret = (tmp_path / "TELEGRAM_WEBHOOK_SECRET").read_text(encoding="utf-8")
    assert re.fullmatch(r"[A-Za-z0-9_-]+", secret)


def test_an_existing_key_is_never_replaced(tmp_path: Path, capsys) -> None:
    """Replacing BACKUP_ENCRYPTION_KEY would make every earlier backup unreadable."""
    keys.init(tmp_path)
    before = _values(tmp_path)
    (tmp_path / "SESSION_SECRET").unlink()
    assert keys.main(["init", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    after = _values(tmp_path)
    assert "BACKUP_ENCRYPTION_KEY: kept" in out and "SESSION_SECRET: created" in out
    for name in keys.GENERATED:
        assert (after[name] == before[name]) == (name != "SESSION_SECRET"), name


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_key_files_are_owner_only(tmp_path: Path) -> None:
    keys.init(tmp_path)
    for name in keys.GENERATED:
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600, name


def test_status_names_what_is_missing(tmp_path: Path, capsys) -> None:
    assert keys.main(["status", "--dir", str(tmp_path)]) == 1
    assert "BACKUP_ENCRYPTION_KEY: MISSING" in capsys.readouterr().out
    keys.init(tmp_path)
    assert keys.main(["status", "--dir", str(tmp_path)]) == 0


def test_the_generated_keys_pass_the_apps_own_prod_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of generating them: prod refuses short, default or repeated secrets."""
    from tests.unit.test_infra import _secret_keys, _task_environment
    from zenflow import settings as S

    keys.init(tmp_path)
    secrets = {key: f"{key.lower()}-" + "x" * 40 for key in _secret_keys()}
    secrets.update(_values(tmp_path))
    secrets["REDIS_URL"] = "rediss://:" + "t" * 48 + "@master.zenflow.cache.amazonaws.com:6379/0"
    for key in S.ALL_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {**_task_environment(), **secrets}.items():
        monkeypatch.setenv(key, value)
    S.reset_settings()
    try:
        settings = S.get_settings()  # raises SettingsError on anything prod refuses
        assert settings.env == "prod"
        assert settings.backup_encryption_key == _values(tmp_path)["BACKUP_ENCRYPTION_KEY"]
    finally:
        S.reset_settings()


def test_the_aws_bundle_holds_exactly_what_the_task_definitions_inject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from tests.unit.test_infra import _secret_keys
    from zenflow import settings as S

    monkeypatch.setenv("TELEGRAM_TOKEN", "1111:patient-bot-token")
    monkeypatch.setenv("THERAPIST_BOT_TOKEN", "2222:therapist-bot-token")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "google-client-secret-value")
    S.reset_settings()
    assert keys.main(["app-secret", "--dir", str(tmp_path)]) == 1, "no keys yet"
    capsys.readouterr()
    keys.init(tmp_path)
    assert keys.main(["app-secret", "--dir", str(tmp_path)]) == 0
    shown = capsys.readouterr()
    bundle = json.loads((tmp_path / keys.APP_SECRET_FILE).read_text(encoding="utf-8"))
    assert set(bundle) == set(
        _secret_keys()
    ), "a key missing from the secret stops the tasks from starting"
    assert bundle["TELEGRAM_TOKEN"] == "1111:patient-bot-token"
    assert bundle["BACKUP_ENCRYPTION_KEY"] == _values(tmp_path)["BACKUP_ENCRYPTION_KEY"]
    for value in bundle.values():
        assert value and value not in shown.out and value not in shown.err


def test_the_bundle_says_which_issued_secret_is_still_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    S.reset_settings()
    keys.init(tmp_path)
    assert keys.main(["app-secret", "--dir", str(tmp_path)]) == 1
    assert "GOOGLE_CLIENT_SECRET" in capsys.readouterr().err


def test_adopt_adds_the_two_keys_to_the_env_file_once(tmp_path: Path, capsys) -> None:
    """The host moves to its own token and backup keys before the AWS cut-over, without anyone
    reading a key off the screen."""
    folder = tmp_path / "keys"
    keys.init(folder)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SESSION_SECRET=host-session-secret\nBACKUP_ENCRYPTION_KEY=already-here\n", encoding="utf-8"
    )
    assert keys.main(["adopt", "--dir", str(folder), "--env-file", str(env_file)]) == 0
    shown = capsys.readouterr()
    text = env_file.read_text(encoding="utf-8")
    token_key = _values(folder)["TOKEN_ENCRYPTION_KEY"]
    assert f"TOKEN_ENCRYPTION_KEY={token_key}\n" in text
    assert "BACKUP_ENCRYPTION_KEY=already-here" in text, "a key already set is never replaced"
    assert text.count("BACKUP_ENCRYPTION_KEY=") == 1
    assert "SESSION_SECRET=host-session-secret" in text and text.count("SESSION_SECRET=") == 1
    assert token_key not in shown.out and "TOKEN_ENCRYPTION_KEY: added" in shown.out
    assert "BACKUP_ENCRYPTION_KEY: already set" in shown.out
    assert keys.main(["adopt", "--dir", str(folder), "--env-file", str(env_file)]) == 0
    assert env_file.read_text(encoding="utf-8") == text, "a second run changes nothing"


def test_adopt_fills_an_empty_line_and_needs_the_key_files(tmp_path: Path, capsys) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TOKEN_ENCRYPTION_KEY=\nUSE_AI=ollama", encoding="utf-8")
    assert keys.main(["adopt", "--dir", str(tmp_path / "none"), "--env-file", str(env_file)]) == 1
    assert "run init first" in capsys.readouterr().out
    assert env_file.read_text(encoding="utf-8") == "TOKEN_ENCRYPTION_KEY=\nUSE_AI=ollama"

    folder = tmp_path / "keys"
    keys.init(folder)
    assert keys.main(["adopt", "--dir", str(folder), "--env-file", str(env_file)]) == 0
    values = _values(folder)
    assert env_file.read_text(encoding="utf-8") == (
        f"TOKEN_ENCRYPTION_KEY={values['TOKEN_ENCRYPTION_KEY']}\nUSE_AI=ollama\n"
        f"BACKUP_ENCRYPTION_KEY={values['BACKUP_ENCRYPTION_KEY']}\n"
    )


def test_the_key_folder_is_ignored_by_git() -> None:
    for path in ("keys/BACKUP_ENCRYPTION_KEY", "keys/app-secret.json"):
        probe = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)
        assert probe.returncode == 0, f"{path} must be ignored before a key is ever written there"
