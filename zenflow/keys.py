"""
zenflow.keys — the secrets this project generates itself, kept as files (owner decision Q15).

    python -m zenflow.keys init          # create the missing keys in ./keys — never printed
    python -m zenflow.keys status        # which keys exist
    python -m zenflow.keys adopt         # add the token and backup keys to this host's .env
    python -m zenflow.keys app-secret    # assemble keys/app-secret.json for AWS Secrets Manager

`keys/` is git-ignored. Each file is named after its environment variable and holds one value,
written owner-only. A value is never printed: a key is something you copy as a file, not read off
a screen. Losing `BACKUP_ENCRYPTION_KEY` means no encrypted backup can be restored, and losing
`TOKEN_ENCRYPTION_KEY` means every therapist reconnects Google. Keep a second copy of the folder
off this computer (docs/SECRETS.md).

`init` changes nothing about how the host runs: it keeps reading `.env`. `adopt` is the step that
moves the host to these keys, before the AWS cut-over (docs/AWS_MOVE_CHECKLIST.md).
"""

from __future__ import annotations

import argparse
import json
import re
import secrets
import sys
from pathlib import Path

#: random values we own (the others — bot tokens, the Google client secret — come from a provider)
GENERATED: tuple[str, ...] = (
    "SESSION_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "BACKUP_ENCRYPTION_KEY",
    "TELEGRAM_WEBHOOK_SECRET",
)
#: what the host takes over before the move. Not SESSION_SECRET (the host has its own) and not the
#: webhook secret (the host polls).
ADOPTED: tuple[str, ...] = ("TOKEN_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY")
#: issued by Telegram and Google; read from the current configuration when the bundle is assembled
ISSUED: dict[str, str] = {
    "TELEGRAM_TOKEN": "telegram_token",
    "THERAPIST_BOT_TOKEN": "therapist_bot_token",
    "GOOGLE_CLIENT_SECRET": "google_client_secret",
}
BUNDLE_FILE = "app-secret.json"
DEFAULT_DIR = "keys"


def _new_key() -> str:
    """64 URL-safe characters (letters, digits, `-`, `_`): valid for every key, webhook included."""
    return secrets.token_urlsafe(48)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def init(folder: Path) -> dict[str, str]:
    """Create each missing key; an existing key is never replaced. Returns name → what happened."""
    from bot.db import write_owner_only

    folder.mkdir(parents=True, exist_ok=True)
    outcome: dict[str, str] = {}
    taken = {_read(folder / name) for name in GENERATED} - {""}
    for name in GENERATED:
        path = folder / name
        if _read(path):
            outcome[name] = "kept"
            continue
        value = _new_key()
        while value in taken:  # the app refuses two keys with the same value
            value = _new_key()
        taken.add(value)
        write_owner_only(path, value.encode("ascii"))
        outcome[name] = "created"
    return outcome


def status(folder: Path) -> dict[str, bool]:
    return {name: bool(_read(folder / name)) for name in GENERATED}


def adopt(folder: Path, env_file: Path) -> dict[str, str]:
    """Add the token and backup keys to `env_file` unless it already sets them.

    A value already there is never replaced: replacing TOKEN_ENCRYPTION_KEY without a rotation
    would make every stored Google token unreadable. Run `python -m zenflow.rotate_token_key` next.
    """
    from bot.db import write_owner_only

    text = env_file.read_text(encoding="utf-8") if env_file.is_file() else ""
    outcome: dict[str, str] = {}
    for name in ADOPTED:
        value = _read(folder / name)
        if not value:
            outcome[name] = "no key file — run init first"
            continue
        line = re.search(rf"^{name}=(.*)$", text, re.MULTILINE)
        if line and line.group(1).strip():
            outcome[name] = "already set"
        elif line:  # an empty assignment: fill that line in
            text = text[: line.start()] + f"{name}={value}" + text[line.end() :]
            outcome[name] = "added"
        else:
            text = text + ("" if text.endswith("\n") or not text else "\n") + f"{name}={value}\n"
            outcome[name] = "added"
    if "added" in outcome.values():
        write_owner_only(env_file, text.encode("utf-8"))
    return outcome


def app_secret(folder: Path) -> tuple[Path, list[str]]:
    """Write the JSON bundle the AWS app secret expects (docs/INFRA.md). Returns the file and the
    names still empty. The issued secrets come from the current settings (the host's `.env`)."""
    from bot.db import write_owner_only
    from zenflow.settings import get_settings

    settings = get_settings()
    bundle = {name: _read(folder / name) for name in GENERATED}
    bundle.update({name: str(getattr(settings, field) or "") for name, field in ISSUED.items()})
    path = folder / BUNDLE_FILE
    write_owner_only(path, json.dumps(bundle, indent=2).encode("utf-8"))
    return path, sorted(name for name, value in bundle.items() if not value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zenflow.keys", description=(__doc__ or "").split("\n\n")[0]
    )
    parser.add_argument("command", choices=["init", "status", "adopt", "app-secret"])
    parser.add_argument("--dir", default=DEFAULT_DIR, help="the key folder (default: ./keys)")
    parser.add_argument("--env-file", default=".env", help="adopt: the file to add the keys to")
    args = parser.parse_args(argv)
    folder = Path(args.dir)

    # CLI output below prints names and outcomes only, never a value.
    if args.command == "init":
        for name, what in init(folder).items():
            print(f"{name}: {what}")  # noqa: T201
        print(
            f"keys are in {folder.resolve()} — keep a second copy off this computer"
        )  # noqa: T201
        return 0
    if args.command == "status":
        present = status(folder)
        for name, there in present.items():
            print(f"{name}: {'present' if there else 'MISSING'}")  # noqa: T201
        return 0 if all(present.values()) else 1
    if args.command == "adopt":
        outcome = adopt(folder, Path(args.env_file))
        for name, what in outcome.items():
            print(f"{name}: {what}")  # noqa: T201
        if "added" in outcome.values():
            print(
                "next: python -m zenflow.rotate_token_key --dry-run, then without it"
            )  # noqa: T201
        return 1 if any(what.startswith("no key file") for what in outcome.values()) else 0
    missing_keys = [name for name, there in status(folder).items() if not there]
    if missing_keys:
        print(
            f"run `python -m zenflow.keys init` first: {missing_keys}", file=sys.stderr
        )  # noqa: T201
        return 1
    path, empty = app_secret(folder)
    print(f"wrote {path} ({len(GENERATED) + len(ISSUED)} keys, values not shown)")  # noqa: T201
    if empty:
        print(f"still empty — fill before uploading: {empty}", file=sys.stderr)  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
