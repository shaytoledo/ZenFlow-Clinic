"""One-time migration: re-encrypt google_tokens with TOKEN_ENCRYPTION_KEY (F7).

    python -m zenflow.rotate_token_key --dry-run     # report only
    python -m zenflow.rotate_token_key               # backup DB, then rotate
    python -m zenflow.rotate_token_key --old-material '<previous SESSION_SECRET>'

Reads TOKEN_ENCRYPTION_KEY (new) and SESSION_SECRET (old default) from the environment / .env.
Exit code 1 if any row could not be decrypted with either key.
"""

from __future__ import annotations

import argparse
import sys

from zenflow.settings import get_settings
from zenflow.token_key import rotate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would change; write nothing"
    )
    parser.add_argument("--old-material", help="previous key material (default: SESSION_SECRET)")
    parser.add_argument(
        "--no-backup", action="store_true", help="skip the DB backup before writing"
    )
    args = parser.parse_args(argv)

    s = get_settings()
    if not s.token_encryption_key:
        print("TOKEN_ENCRYPTION_KEY is not set — nothing to rotate to.", file=sys.stderr)
        return 2
    old = args.old_material or s.session_secret
    try:
        report = rotate(
            old_material=old,
            new_material=s.token_encryption_key,
            dry_run=args.dry_run,
            backup=not args.no_backup,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(report.summary())
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
