"""Regressions for the PR #5 (Phase 1.2) review findings."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

import bot.db as dbmod
from zenflow import clock
from zenflow import queue as q
from zenflow import worker as w


@pytest.fixture
def queue(db) -> q.SqliteTaskQueue:
    return q.SqliteTaskQueue()


def _row(job_id: int) -> dict:
    return dict(dbmod.get_db().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())


def test_complete_cannot_resurrect_a_cancelled_job(queue, frozen_clock) -> None:
    job_id = queue.enqueue("x", {})
    (job,) = queue.claim(worker_id="w1", limit=1)
    assert queue.cancel(job_id) is True
    assert queue.complete(job_id, worker_id="w1") is False
    assert _row(job_id)["status"] == "cancelled"


def test_late_finisher_after_reclaim_does_not_touch_the_new_owner(queue, frozen_clock) -> None:
    job_id = queue.enqueue("x", {})
    queue.claim(worker_id="A", limit=1)
    frozen_clock.tick(timedelta(seconds=q.LOCK_TIMEOUT_SECONDS + 1))
    (again,) = queue.claim(worker_id="B", limit=1)
    assert again.locked_by == "B"
    assert queue.complete(job_id, worker_id="A") is False  # A finished late: ignored
    assert queue.fail(job_id, error="A failed late", worker_id="A") is False
    assert _row(job_id)["status"] == "running" and _row(job_id)["locked_by"] == "B"
    assert queue.complete(job_id, worker_id="B") is True


def test_retry_at_does_not_bypass_the_attempt_budget(queue, frozen_clock) -> None:
    job_id = queue.enqueue("x", {}, max_attempts=2)
    queue.claim(worker_id="w", limit=1)
    queue.fail(job_id, error="429", retry_at=clock.hours_ahead(1))
    assert _row(job_id)["status"] == "pending"
    frozen_clock.tick(timedelta(hours=1, seconds=1))
    queue.claim(worker_id="w", limit=1)
    queue.fail(job_id, error="429 again", retry_at=clock.hours_ahead(1))
    assert _row(job_id)["status"] == "dead", "explicit retry_at only overrides the delay"


def test_repeatedly_crashing_worker_dead_letters_after_the_budget(queue, frozen_clock) -> None:
    job_id = queue.enqueue("x", {}, max_attempts=3)
    for _ in range(3):  # claimed, never completed (process killed), lock expires
        assert [j.id for j in queue.claim(worker_id="crash", limit=1)] == [job_id]
        frozen_clock.tick(timedelta(seconds=q.LOCK_TIMEOUT_SECONDS + 1))
    assert queue.claim(worker_id="w", limit=1) == []
    row = _row(job_id)
    assert row["status"] == "dead"
    assert "lock expired" in (row["last_error"] or "")
    assert [j.id for j in queue.dead_letters()] == [job_id]


def test_empty_idempotency_key_is_treated_as_none(queue, frozen_clock) -> None:
    a = queue.enqueue("x", {"n": 1}, idempotency_key="")
    b = queue.enqueue("x", {"n": 2}, idempotency_key="")
    assert a != b and _row(a)["idempotency_key"] is None and _row(b)["idempotency_key"] is None


def test_worker_refuses_a_timeout_longer_than_the_lock(queue) -> None:
    with pytest.raises(ValueError, match="LOCK_TIMEOUT"):
        w.Worker(
            queue, w.HandlerRegistry(), worker_id="t", handler_timeout=q.LOCK_TIMEOUT_SECONDS + 1
        )


async def test_cancelled_worker_releases_its_job_immediately(queue, frozen_clock) -> None:
    registry = w.HandlerRegistry()
    started = asyncio.Event()

    @registry.handler("long")
    async def _h(payload: dict) -> None:
        started.set()
        await asyncio.sleep(60)

    job_id = queue.enqueue("long", {})
    worker = w.Worker(queue, registry, worker_id="w")
    task = asyncio.create_task(worker.run_once())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    row = _row(job_id)
    assert row["status"] == "pending" and row["locked_by"] is None
    assert row["attempts"] == 0, "an attempt that never ran is not charged"
    assert [j.id for j in queue.claim(worker_id="w2", limit=1)] == [job_id]  # claimable now


def test_stats_counts_every_status(queue, frozen_clock) -> None:
    a = queue.enqueue("a", {})
    b = queue.enqueue("b", {}, max_attempts=1)
    c = queue.enqueue("c", {})
    queue.enqueue("d", {}, run_at=clock.hours_ahead(2))
    queue.claim(worker_id="w", limit=3)
    queue.complete(a, worker_id="w")
    queue.fail(b, error="x", worker_id="w")
    queue.cancel(c)
    assert queue.stats() == {"pending": 1, "running": 0, "done": 1, "dead": 1, "cancelled": 1}


def test_standalone_worker_main_initialises_the_schema(db, monkeypatch) -> None:
    """`python -m zenflow.worker` must create the jobs table itself (bot.config is not imported)."""
    dbmod.get_db().execute("DROP TABLE jobs")

    async def _run_once_then_stop(self) -> None:  # replace run_forever so main() returns
        await self.run_once()

    monkeypatch.setattr(w.Worker, "run_forever", _run_once_then_stop)
    w.main()
    assert {r[1] for r in dbmod.get_db().execute("PRAGMA table_info(jobs)")} >= {"id", "status"}
