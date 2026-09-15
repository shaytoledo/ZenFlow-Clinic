"""Phase 1.2 — durable TaskQueue (SQLite `jobs` table) + in-process worker.

Gate from the plan: a job scheduled 24h out survives a process restart and fires exactly once;
duplicate idempotency keys are rejected; failures retry with backoff then dead-letter; a job is
never claimed early.
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


def _rows() -> list[dict]:
    return [dict(r) for r in dbmod.get_db().execute("SELECT * FROM jobs ORDER BY id")]


# ── enqueue / claim / complete ───────────────────────────────────────────────────────────────
def test_enqueue_claim_complete_round_trip(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    job_id = queue.enqueue("followup.send_step1", {"appointment_id": 7}, run_at=clock.iso_now())
    assert isinstance(job_id, int)
    claimed = queue.claim(worker_id="w1", limit=10)
    assert [j.id for j in claimed] == [job_id]
    job = claimed[0]
    assert job.name == "followup.send_step1"
    assert job.payload == {"appointment_id": 7}
    assert job.attempts == 1
    assert queue.claim(worker_id="w2", limit=10) == [], "a running job is not claimed twice"
    queue.complete(job_id)
    (row,) = _rows()
    assert row["status"] == "done"
    assert clock.is_canonical(row["completed_at"])
    assert clock.is_canonical(row["created_at"])


def test_idempotency_key_rejects_duplicates(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    first = queue.enqueue(
        "recommendations.dispatch", {"appointment_id": 1}, idempotency_key="rec:1"
    )
    second = queue.enqueue(
        "recommendations.dispatch", {"appointment_id": 1}, idempotency_key="rec:1"
    )
    assert second == first
    assert len(_rows()) == 1
    # a *done* job with the same key is still a duplicate: never re-send
    queue.claim(worker_id="w1", limit=1)
    queue.complete(first)
    assert (
        queue.enqueue("recommendations.dispatch", {"appointment_id": 1}, idempotency_key="rec:1")
        == first
    )
    assert len(_rows()) == 1


def test_job_scheduled_24h_out_is_not_claimed_early(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    queue.enqueue("followup.send_step1", {"appointment_id": 9}, run_at=clock.hours_ahead(24))
    assert queue.claim(worker_id="w1", limit=10) == []
    frozen_clock.tick(timedelta(hours=23, minutes=59))
    assert queue.claim(worker_id="w1", limit=10) == []
    frozen_clock.tick(timedelta(minutes=2))
    assert len(queue.claim(worker_id="w1", limit=10)) == 1


def test_failure_retries_with_backoff_then_dead_letters(
    queue: q.SqliteTaskQueue, frozen_clock
) -> None:
    job_id = queue.enqueue("x", {}, max_attempts=3)
    for attempt in (1, 2):
        (job,) = queue.claim(worker_id="w1", limit=1)
        assert job.attempts == attempt
        queue.fail(job_id, error=f"boom {attempt}")
        (row,) = _rows()
        assert row["status"] == "pending"
        assert row["last_error"] == f"boom {attempt}"
        expected_delay = q.BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
        assert row["run_at"] == clock.to_iso(clock.now_utc() + timedelta(seconds=expected_delay))
        assert queue.claim(worker_id="w1", limit=1) == [], "not before its backoff"
        frozen_clock.tick(timedelta(seconds=expected_delay + 1))
    (job,) = queue.claim(worker_id="w1", limit=1)
    assert job.attempts == 3
    queue.fail(job_id, error="boom 3")
    (row,) = _rows()
    assert row["status"] == "dead"
    assert queue.claim(worker_id="w1", limit=1) == []
    assert [j.id for j in queue.dead_letters()] == [job_id]


def test_worker_death_mid_flight_recovers_after_lock_timeout(
    queue: q.SqliteTaskQueue, frozen_clock
) -> None:
    """Claimed by a worker that never completes it (process killed) → reclaimable once the lock
    expires, and it completes exactly once."""
    job_id = queue.enqueue("x", {"n": 1})
    (job,) = queue.claim(worker_id="crashed", limit=1)
    assert job.id == job_id
    assert queue.claim(worker_id="w2", limit=1) == []
    frozen_clock.tick(timedelta(seconds=q.LOCK_TIMEOUT_SECONDS + 1))
    (again,) = queue.claim(worker_id="w2", limit=1)
    assert again.id == job_id and again.attempts == 2 and again.locked_by == "w2"
    queue.complete(job_id)
    frozen_clock.tick(timedelta(seconds=q.LOCK_TIMEOUT_SECONDS + 1))
    assert queue.claim(worker_id="w3", limit=1) == []
    assert _rows()[0]["status"] == "done"


def test_retry_at_override_and_cancel(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    job_id = queue.enqueue("x", {})
    queue.claim(worker_id="w1", limit=1)
    queue.fail(job_id, error="rate limited", retry_at=clock.hours_ahead(1))
    assert _rows()[0]["run_at"] == clock.hours_ahead(1)
    assert queue.cancel(job_id) is True
    assert _rows()[0]["status"] == "cancelled"
    assert queue.claim(worker_id="w1", limit=1) == []


def test_stats_counts_by_status(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    queue.enqueue("a", {})
    b = queue.enqueue("b", {})
    queue.claim(worker_id="w1", limit=1)
    queue.enqueue("c", {}, run_at=clock.hours_ahead(2))
    stats = queue.stats()
    assert stats["pending"] == 2 and stats["running"] == 1
    queue.fail(b, error="x") if False else None
    assert set(stats) >= {"pending", "running", "done", "dead", "cancelled"}


# ── worker ───────────────────────────────────────────────────────────────────────────────────
async def test_worker_runs_registered_handler_and_completes(
    queue: q.SqliteTaskQueue, frozen_clock
) -> None:
    seen: list[dict] = []
    registry = w.HandlerRegistry()

    @registry.handler("followup.send_step1")
    async def _h(payload: dict) -> None:
        seen.append(payload)

    queue.enqueue("followup.send_step1", {"appointment_id": 3})
    worker = w.Worker(queue, registry, worker_id="test")
    processed = await worker.run_once()
    assert processed == 1
    assert seen == [{"appointment_id": 3}]
    assert _rows()[0]["status"] == "done"
    assert await worker.run_once() == 0


async def test_worker_failure_is_recorded_and_retried(
    queue: q.SqliteTaskQueue, frozen_clock
) -> None:
    registry = w.HandlerRegistry()
    calls = {"n": 0}

    @registry.handler("flaky")
    async def _h(payload: dict) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("telegram 502")

    queue.enqueue("flaky", {}, max_attempts=3)
    worker = w.Worker(queue, registry, worker_id="test")
    assert await worker.run_once() == 1
    row = _rows()[0]
    assert row["status"] == "pending" and "telegram 502" in row["last_error"]
    frozen_clock.tick(timedelta(seconds=q.BACKOFF_BASE_SECONDS + 1))
    assert await worker.run_once() == 1
    assert _rows()[0]["status"] == "done"
    assert calls["n"] == 2


async def test_worker_unknown_handler_dead_letters_immediately(
    queue: q.SqliteTaskQueue, frozen_clock
) -> None:
    queue.enqueue("no.such.handler", {})
    worker = w.Worker(queue, w.HandlerRegistry(), worker_id="test")
    assert await worker.run_once() == 1
    row = _rows()[0]
    assert row["status"] == "dead" and "no handler" in row["last_error"]


async def test_worker_handler_timeout_fails_the_job(queue: q.SqliteTaskQueue, frozen_clock) -> None:
    import asyncio

    registry = w.HandlerRegistry()

    @registry.handler("slow")
    async def _h(payload: dict) -> None:
        await asyncio.sleep(10)

    queue.enqueue("slow", {}, max_attempts=1)
    worker = w.Worker(queue, registry, worker_id="test", handler_timeout=0.01)
    assert await worker.run_once() == 1
    row = _rows()[0]
    assert row["status"] == "dead" and "timed out" in row["last_error"]


async def test_worker_logs_carry_job_context(
    queue: q.SqliteTaskQueue, frozen_clock, caplog
) -> None:
    import logging

    registry = w.HandlerRegistry()

    @registry.handler("ctx")
    async def _h(payload: dict) -> None:
        logging.getLogger("zenflow.test.job").info("inside handler")

    queue.enqueue("ctx", {"appointment_id": 42, "patient_id": 5})
    with caplog.at_level(logging.INFO):
        await w.Worker(queue, registry, worker_id="test").run_once()
    inside = [r for r in caplog.records if r.getMessage() == "inside handler"][0]
    assert str(inside.request_id).startswith("job-")
    assert inside.appointment_id == 42
    assert inside.patient_id == 5


# ── backend selection by flag (both paths) ───────────────────────────────────────────────────
def test_default_queue_is_sqlite_inprocess(db, monkeypatch) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("ZF_QUEUE_BACKEND", "inprocess")
    S.reset_settings()
    assert isinstance(q.get_default_queue(), q.SqliteTaskQueue)


@pytest.mark.parametrize("backend", ["celery", "temporal", "aws"])
def test_other_backends_are_explicitly_not_implemented_yet(db, monkeypatch, backend: str) -> None:
    from zenflow import settings as S

    monkeypatch.setenv("ZF_QUEUE_BACKEND", backend)
    S.reset_settings()
    with pytest.raises(NotImplementedError, match="Phase 12"):
        q.get_default_queue()


def test_jobs_table_is_part_of_init_db(db) -> None:
    cols = {r[1] for r in dbmod.get_db().execute("PRAGMA table_info(jobs)")}
    assert {
        "id", "name", "payload_json", "run_at", "status", "attempts", "max_attempts",
        "last_error", "idempotency_key", "locked_by", "locked_at", "created_at", "updated_at",
        "completed_at",
    } <= cols  # fmt: skip
