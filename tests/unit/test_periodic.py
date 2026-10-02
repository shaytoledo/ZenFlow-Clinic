"""Phase 12.2.4 — periodic work runs once per interval across every worker (`zenflow.periodic`).

Before: the follow-up reconcile sweep was an asyncio loop in each bot process, so N bot containers
meant N sweeps. Now it is a registered task guarded by a lease held for its interval.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest

from zenflow import leases, periodic


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """A throw-away periodic task (every 60 s) that records each run."""
    runs: list[str] = []
    monkeypatch.setattr(periodic, "_TASKS", {})
    periodic.task("test.count", every_seconds=60)(lambda: runs.append("ran"))
    yield runs


def test_a_due_task_runs_once_per_interval_across_workers(db, counted, frozen_clock) -> None:
    assert periodic.run_due("worker-a") == ["test.count"]
    assert periodic.run_due("worker-a") == [], "the same worker does not run it again"
    assert periodic.run_due("worker-b") == [], "nor does any other worker"
    frozen_clock.tick(timedelta(seconds=61))
    assert periodic.run_due("worker-b") == ["test.count"]
    assert counted == ["ran", "ran"]


def test_a_failing_task_waits_for_its_next_turn(db, monkeypatch, frozen_clock, caplog) -> None:
    calls: list[int] = []

    def boom() -> None:
        calls.append(1)
        raise RuntimeError("database down")

    monkeypatch.setattr(periodic, "_TASKS", {})
    periodic.task("test.boom", every_seconds=60)(boom)
    assert periodic.run_due("w") == ["test.boom"]  # ran (and failed), the error is logged
    assert "test.boom failed" in caplog.text
    assert periodic.run_due("w") == [] and calls == [1], "not retried every poll"


def test_force_runs_inside_the_interval(db, counted) -> None:
    periodic.run("test.count", "w")
    assert periodic.run("test.count", "cli", force=True) is True
    assert counted == ["ran", "ran"]


def test_the_reconcile_sweep_is_a_registered_periodic_task() -> None:
    from bot.services import followup_scheduler
    from zenflow.worker import DEFAULT_HANDLER_MODULES

    assert "bot.services.followup_scheduler" in DEFAULT_HANDLER_MODULES
    reconcile = periodic._TASKS["followup.reconcile"]
    assert reconcile.every_seconds == followup_scheduler.RECONCILE_INTERVAL_SECONDS
    assert reconcile.run is followup_scheduler.reconcile_periodically


def test_the_bot_no_longer_runs_its_own_reconcile_loop() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    main = (root / "bot" / "main.py").read_text(encoding="utf-8")
    scheduler = (root / "bot" / "services" / "followup_scheduler.py").read_text(encoding="utf-8")
    assert "start_followup_scheduler" not in main
    assert "_scheduler_loop" not in scheduler


async def test_the_worker_runs_due_tasks_at_most_once_per_poll(db, counted) -> None:
    from zenflow.queue import SqliteTaskQueue
    from zenflow.worker import HandlerRegistry, Worker

    worker = Worker(SqliteTaskQueue(), HandlerRegistry(), worker_id="w1", poll_interval=3600)
    assert await worker.run_periodic() == ["test.count"]
    assert await worker.run_periodic() == [], "checked again only after a poll interval"
    leases.release(periodic.lease_name("test.count"), "w1")
    assert await worker.run_periodic() == [], "still inside this worker's poll interval"


def test_the_cli_lists_and_runs_tasks(db, counted, capsys, monkeypatch) -> None:
    monkeypatch.setattr("zenflow.worker.load_default_handlers", lambda: None)
    assert periodic.main([]) == 0
    assert "test.count" in capsys.readouterr().out
    assert periodic.main(["test.count"]) == 0 and counted == ["ran"]
    assert periodic.main(["test.count"]) == 0 and "skipped" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        periodic.main(["nope"])
