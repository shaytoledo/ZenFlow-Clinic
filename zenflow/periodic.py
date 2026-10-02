"""
zenflow.periodic — work that runs every N seconds, once across all processes (Phase 12.2.4).

Until 12.2.4 the follow-up reconcile sweep was an asyncio loop inside every bot process: two bot
containers, two sweeps. Now a periodic task is a function registered here, and every worker
(`zenflow.worker`, in-process or standalone) calls `run_due()` on each poll. Each task is guarded by
a lease named after it and held for the task's interval: the first worker to take the lease runs
the task, and every other worker, in any process or container, skips it until the interval is over.

On AWS the same task can be fired by EventBridge Scheduler instead, with
`python -m zenflow.periodic <name>`. The lease still prevents a double run.

    python -m zenflow.periodic                    # list the tasks and their intervals
    python -m zenflow.periodic <name>             # run it now if its interval is over
    python -m zenflow.periodic <name> --force     # run it now regardless
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from zenflow import leases

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PeriodicTask:
    name: str
    every_seconds: int
    run: Callable[[], Any]


_TASKS: dict[str, PeriodicTask] = {}


def task(name: str, *, every_seconds: int) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Register `fn` to run every `every_seconds`, somewhere, once."""
    if every_seconds <= 0:
        raise ValueError("every_seconds must be positive")

    def register(fn: Callable[[], Any]) -> Callable[[], Any]:
        _TASKS[name] = PeriodicTask(name, every_seconds, fn)
        return fn

    return register


def tasks() -> list[PeriodicTask]:
    return sorted(_TASKS.values(), key=lambda t: t.name)


def lease_name(name: str) -> str:
    return f"periodic:{name}"


def run(name: str, holder: str, *, force: bool = False) -> bool:
    """Run one task if its interval is over (or `force`). Returns whether it ran.

    The lease is taken BEFORE the work and kept for the whole interval — a task that fails waits
    for its next turn instead of being retried every poll by every worker.
    """
    periodic = _TASKS[name]
    got = leases.acquire(lease_name(name), holder, ttl_seconds=periodic.every_seconds, renew=False)
    if not (got or force):
        return False
    try:
        result = periodic.run()
        if result:
            logger.info("periodic %s: %s", name, result)
    except Exception:
        logger.exception("periodic %s failed; next try in %ss", name, periodic.every_seconds)
    return True


def run_due(holder: str) -> list[str]:
    """Run every task whose interval is over. Called by each worker on each poll."""
    return [t.name for t in tasks() if run(t.name, holder)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Periodic tasks (Phase 12.2.4).")
    parser.add_argument("name", nargs="?", help="the task to run (default: list them)")
    parser.add_argument("--force", action="store_true", help="run even inside its interval")
    args = parser.parse_args(argv)

    import socket

    import bot.db as dbmod
    from zenflow.worker import load_default_handlers

    dbmod.init_db()
    load_default_handlers()  # the modules that register handlers also register periodic tasks
    if not args.name:
        for t in tasks():
            print(f"{t.name:30} every {t.every_seconds}s")  # noqa: T201 - CLI output
        return 0
    if args.name not in _TASKS:
        parser.error(f"unknown task {args.name!r}; known: {', '.join(_TASKS) or 'none'}")
    ran = run(args.name, f"cli-{socket.gethostname()}", force=args.force)
    print("ran" if ran else "skipped: its interval is not over (use --force)")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
