"""
zenflow.jobs — see and fix the background-job queue from the command line (Phase 13.3).

    python -m zenflow.jobs stats                     # how many jobs in each state
    python -m zenflow.jobs dead                      # the dead-lettered jobs, oldest first
    python -m zenflow.jobs replay <id> [<id> …]      # give dead jobs a fresh start
    python -m zenflow.jobs replay --all [--name N]   # … every dead job (of one kind)
    python -m zenflow.jobs cancel <id>               # stop a pending job

A job is dead when it failed `max_attempts` times. The owner was alerted through its dead-letter
hook (Phase 8). Fix the cause first: Google reconnected, Telegram reachable again, a bug deployed.
Then replay. Handlers check the database before acting, so a replay never sends anything twice
(docs/RUNBOOK.md). The listing shows ids and the redacted error, never message content.
"""

from __future__ import annotations

import argparse
import json
import sys

from zenflow.queue import Job, get_default_queue


def _line(job: Job) -> str:
    from zenflow.logging import redact

    error = redact((job.last_error or "").replace("\n", " "))[:120]
    payload = json.dumps(job.payload, sort_keys=True)[:80]
    return f"{job.id:>7}  {job.name:28} {job.attempts}/{job.max_attempts}  {payload}  {error}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m zenflow.jobs", description=__doc__.split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("stats", help="jobs per state")
    commands.add_parser("dead", help="list the dead-lettered jobs")
    replay = commands.add_parser("replay", help="give dead jobs a fresh start")
    replay.add_argument("ids", nargs="*", type=int)
    replay.add_argument("--all", action="store_true", help="every dead job")
    replay.add_argument("--name", help="with --all: only jobs of this kind")
    cancel = commands.add_parser("cancel", help="stop a pending job")
    cancel.add_argument("id", type=int)
    args = parser.parse_args(argv)

    import bot.db as dbmod

    dbmod.init_db()
    queue = get_default_queue()
    if args.command == "stats":
        print(json.dumps(queue.stats()))  # noqa: T201 - CLI output
        return 0
    if args.command == "dead":
        dead = queue.dead_letters()
        for job in dead:
            print(_line(job))  # noqa: T201
        print(f"{len(dead)} dead job(s)", file=sys.stderr)  # noqa: T201
        return 0
    if args.command == "cancel":
        done = queue.cancel(args.id)
        print("cancelled" if done else "not cancelled: not pending or running")  # noqa: T201
        return 0 if done else 1
    if args.all == bool(args.ids):
        parser.error("replay takes job ids, or --all — not both, not neither")
    ids = args.ids or [j.id for j in queue.dead_letters() if not args.name or j.name == args.name]
    replayed = [job_id for job_id in ids if queue.requeue(job_id)]
    skipped = sorted(set(ids) - set(replayed))
    print(f"replayed {len(replayed)}: {replayed}")  # noqa: T201
    if skipped:
        print(f"not dead, left alone: {skipped}", file=sys.stderr)  # noqa: T201
    return 0 if not skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())
