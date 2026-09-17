"""Manage booking-API keys (Phase 7.3).

    python -m zenflow.api_keys list
    python -m zenflow.api_keys create <name>     # prints the key ONCE
    python -m zenflow.api_keys revoke <name>

A key lets its holder book for any therapist in the clinic, so give one to a server you run,
never to a browser or a person. Only its hash is stored: a lost key is revoked and replaced,
never recovered.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m zenflow.api_keys", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show the clients and when their key was last used")
    create = commands.add_parser("create", help="add a client and print its key once")
    create.add_argument("name", help="who the key is for, e.g. whatsapp-bridge")
    revoke = commands.add_parser("revoke", help="stop a key working")
    revoke.add_argument("name")
    args = parser.parse_args(argv)

    import bot.db as dbmod
    from web.repositories import api_client_repo

    dbmod.init_db()

    if args.command == "list":
        clients = api_client_repo.list_clients()
        if not clients:
            print("no API clients")
            return 0
        for client in clients:
            state = "revoked" if client["revoked_at"] else "active"
            used = client["last_used_at"] or "never used"
            print(f"{client['id']:>3}  {client['name']:<24} {state:<8} {used}")
        return 0

    if args.command == "create":
        _client_id, key = api_client_repo.create(args.name)
        print(f"client: {args.name}")
        print(f"key:    {key}")
        print("\nStore it now — it is not shown again and only its hash is kept.")
        return 0

    if api_client_repo.revoke(args.name):
        print(f"revoked: {args.name}")
        return 0
    print(f"no active client named {args.name!r}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
