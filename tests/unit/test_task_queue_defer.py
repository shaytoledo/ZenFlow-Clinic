"""Phase 5.4 — a job can wait for something outside it without failing.

`defer()` parks a running job until a time without charging the attempt, `pending()` lists what
waits, `run_now()` ends the wait early; a handler raises `JobDeferred` to use it.
"""

from __future__ import annotations

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


def test_defer_parks_the_job_without_charging_the_attempt(
    queue: q.SqliteTaskQueue, frozen_clock
) -> None:
    job_id = queue.enqueue("mail", {}, max_attempts=1)
    queue.claim(worker_id="w1")
    assert queue.defer(job_id, run_at=clock.hours_ahead(6), reason="waiting", worker_id="w1")
    row = _row(job_id)
    assert row["status"] == "pending" and row["attempts"] == 0
    assert row["run_at"] == clock.hours_ahead(6) and row["last_error"] == "waiting"
    assert row["locked_by"] is None
    assert queue.claim(worker_id="w1") == [], "not before its time"
    frozen_clock.tick(timedelta(hours=6))
    assert [j.id for j in queue.claim(worker_id="w1")] == [job_id], "one attempt is still left"


def test_only_the_owner_can_defer_a_running_job(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    job_id = queue.enqueue("mail", {})
    assert not queue.defer(job_id, run_at=clock.hours_ahead(1), reason="x"), "not running"
    queue.claim(worker_id="w1")
    assert not queue.defer(job_id, run_at=clock.hours_ahead(1), reason="x", worker_id="w2")
    assert _row(job_id)["status"] == "running"


def test_pending_lists_waiting_jobs_of_one_name(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    later = queue.enqueue("mail", {"n": 2}, run_at=clock.hours_ahead(2))
    sooner = queue.enqueue("mail", {"n": 1}, run_at=clock.hours_ahead(1))
    queue.enqueue("other", {}, run_at=clock.hours_ahead(3))
    done = queue.enqueue("mail", {"n": 0})
    [claimed] = queue.claim(worker_id="w1")
    assert claimed.id == done
    queue.complete(done)
    assert [j.id for j in queue.pending("mail")] == [sooner, later]


def test_run_now_ends_the_wait(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    job_id = queue.enqueue("mail", {}, run_at=clock.hours_ahead(6))
    assert queue.run_now(job_id) is True
    assert _row(job_id)["run_at"] == clock.iso_now()
    assert queue.run_now(job_id) is False, "already due"
    [job] = queue.claim(worker_id="w1")
    assert queue.run_now(job.id) is False, "running jobs are left alone"
    queue.complete(job.id)
    assert queue.run_now(job.id) is False, "so are finished ones"


async def test_a_handler_can_defer_its_job(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    registry = w.HandlerRegistry()
    dead: list[str] = []
    calls = {"n": 0}

    @registry.handler("mail")
    async def _h(payload: dict) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise w.JobDeferred(clock.hours_ahead(6), "waiting for Google")

    @registry.on_dead("mail")
    async def _dead(payload: dict, error: str) -> None:
        dead.append(error)

    job_id = queue.enqueue("mail", {}, max_attempts=1)
    worker = w.Worker(queue, registry, worker_id="test")
    for _ in range(2):
        assert await worker.run_once() == 1
        row = _row(job_id)
        assert row["status"] == "pending" and row["attempts"] == 0
        assert row["last_error"] == "waiting for Google"
        frozen_clock.tick(timedelta(hours=6))
    assert await worker.run_once() == 1
    assert _row(job_id)["status"] == "done"
    assert calls["n"] == 3 and dead == [], "waiting is never a failure"
