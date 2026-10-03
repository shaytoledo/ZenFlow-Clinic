"""Phase 13.3 — an operator can see the queue and replay dead-lettered jobs (docs/RUNBOOK.md)."""

from __future__ import annotations

import pytest

from zenflow import jobs
from zenflow.queue import get_default_queue


def _dead(name: str = "followup.send", error: str = "Telegram unreachable") -> int:
    queue = get_default_queue()
    job_id = queue.enqueue(name, {"appointment_id": 7}, max_attempts=1)
    (claimed,) = queue.claim(worker_id="w")
    assert claimed.id == job_id
    queue.fail(job_id, error=error, worker_id="w")
    return job_id


def test_a_dead_job_is_replayed_with_a_fresh_budget(db) -> None:
    queue = get_default_queue()
    job_id = _dead()
    assert queue.requeue(job_id) is True
    (job,) = queue.pending("followup.send")
    assert job.id == job_id and job.attempts == 0, "a fresh start"
    assert job.last_error == "Telegram unreachable", "the reason it died is kept"
    assert queue.claim(worker_id="w2")[0].id == job_id, "due now"


def test_only_dead_jobs_are_replayed(db) -> None:
    queue = get_default_queue()
    pending = queue.enqueue("followup.send", {"appointment_id": 8})
    assert queue.requeue(pending) is False
    assert queue.requeue(999_999) is False


def test_the_cli_lists_replays_and_cancels(db, capsys) -> None:
    first, second = _dead("followup.send"), _dead(
        "recommendations.dispatch", error="token=abc123secret"
    )
    assert jobs.main(["dead"]) == 0
    listing = capsys.readouterr().out
    assert str(first) in listing and str(second) in listing
    assert "abc123secret" not in listing, "errors are redacted"

    assert jobs.main(["replay", "--all", "--name", "followup.send"]) == 0
    assert f"replayed 1: [{first}]" in capsys.readouterr().out
    assert jobs.main(["replay", str(second), str(first)]) == 1, "the second is no longer dead"
    assert jobs.main(["cancel", str(first)]) == 0
    assert jobs.main(["stats"]) == 0
    assert '"cancelled": 1' in capsys.readouterr().out
    with pytest.raises(SystemExit):
        jobs.main(["replay"])  # neither ids nor --all
