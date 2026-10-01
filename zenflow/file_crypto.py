"""
zenflow.file_crypto
────────────────────
Encrypt a file before it leaves the host — database backups and patient exports (Phase 9.9).

    python -m zenflow.file_crypto decrypt backup.db.enc --out restored.db

Fernet (AES-128-CBC + HMAC-SHA256, authenticated: a tampered or truncated file is refused, never
half-decrypted) with a key derived from `BACKUP_ENCRYPTION_KEY` exactly as `zenflow.token_key`
derives the token key. The material is its own secret, so leaking a backup key never exposes the
session or the stored Google tokens. Keep a copy of it **off** the host: without it an encrypted
backup cannot be restored.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SUFFIX = ".enc"


class EncryptionUnavailable(RuntimeError):
    """`BACKUP_ENCRYPTION_KEY` is not set, so nothing can be encrypted or decrypted."""


def require_fernet(material: str | None = None) -> Fernet:
    """The backup Fernet, or `EncryptionUnavailable` when no key is configured."""
    from zenflow.settings import get_settings
    from zenflow.token_key import fernet_for

    material = material or get_settings().backup_encryption_key
    if not material:
        raise EncryptionUnavailable(
            "BACKUP_ENCRYPTION_KEY is not set — see .env.example and docs/DATA_PROTECTION.md"
        )
    return fernet_for(material)


def encrypt_bytes(data: bytes, *, material: str | None = None) -> bytes:
    return require_fernet(material).encrypt(data)


def encrypt_file(
    path: str | Path, *, remove_plain: bool = True, material: str | None = None
) -> str:
    """Write `<path>.enc` and (by default) delete the plaintext. Returns the encrypted path."""
    src = Path(path)
    token = encrypt_bytes(src.read_bytes(), material=material)
    dest = src.with_name(src.name + SUFFIX)
    dest.write_bytes(token)
    if remove_plain:
        os.remove(src)
    return str(dest)


def decrypt_file(path: str | Path, out: str | Path, *, material: str | None = None) -> str:
    """Decrypt `path` into `out`. Raises `InvalidToken` on a wrong key or a damaged file."""
    data = require_fernet(material).decrypt(Path(path).read_bytes())
    Path(out).write_bytes(data)
    return str(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Decrypt a backup or export (Phase 9.9).")
    sub = parser.add_subparsers(dest="command", required=True)
    dec = sub.add_parser("decrypt", help="decrypt a .enc file")
    dec.add_argument("path")
    dec.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        print(decrypt_file(args.path, args.out))
    except EncryptionUnavailable as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except InvalidToken:
        print("refused: wrong BACKUP_ENCRYPTION_KEY, or the file is damaged", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
