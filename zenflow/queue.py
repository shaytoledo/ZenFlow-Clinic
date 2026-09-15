"""zenflow.queue — durable task queue behind a `TaskQueue` interface (Phase 1.2, ADR-20).

The only implementation today is `SqliteTaskQueue`: a `jobs` table in the existing database
(no new infrastructure). It gives durability across restarts, idempotency, retry with
exponential backoff, a dead-letter state and stale-lock recovery. Celery / Temporal / AWS become
alternative `TaskQueue` implementations selected by `ZF_QUEUE_BACKEND` in Phase 12 without
touching a single caller.

Timestamps are canonical UTC strings (ADR-19), written from Python via `zenflow.clock` so the
frozen clock in tests governs scheduling exactly.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import bot.db as dbmod
from zenflow import clock

#: first retry after 60 s, then 120 s, 240 s, … (attempt n waits BASE * 2**(n-1))
BACKOFF_BASE_SECONDS = 60
#: a job left `running` this long is assumed orphaned (worker died) and becomes claimable
LOCK_TIMEOUT_SECONDS = 600
DEFAULT_MAX_ATTEMPTS = 5
MAX_ERROR_LEN = 2000

STATUSES: tuple[str, ...] = ("pending", "running", "done", "dead", "cancelled")


@dataclass(frozen=True)
class Job:
    id: int
    name: str
    payload: dict[str, Any]
    run_at: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    idempotency_key: str | None
    locked_by: str | None
    locked_at: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> Job:
        d = dict(row)
        try:
            payload = json.loads(d.get("payload_json") or "{}")
        except json.JSONDecodeError:
            payload = {}
        return cls(
            id=int(d["id"]),
            name=str(d["name"]),
            payload=payload if isinstance(payload, dict) else {},
            run_at=str(d["run_at"]),
            status=str(d["status"]),
            attempts=int(d["attempts"] or 0),
            max_attempts=int(d["max_attempts"] or DEFAULT_MAX_ATTEMPTS),
            last_error=d.get("last_error"),
            idempotency_key=d.get("idempotency_key"),
            locked_by=d.get("locked_by"),
            locked_at=d.get("locked_at"),
            created_at=str(d["created_at"]),
        )


class TaskQueue(ABC):
    """Contract every backend must satisfy (conformance suite: tests/unit/test_task_queue.py)."""

    @abstractmethod
    def enqueue(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        run_at: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> int:
        """Schedule `name` with `payload` at `run_at` (canonical UTC; default now).

        With an `idempotency_key`, a second enqueue returns the existing job id and creates
        nothing — even after that job completed. Returns the job id.
        """

    @abstractmethod
    def claim(self, *, worker_id: str, limit: int = 1) -> list[Job]:
        """Atomically take up to `limit` due jobs (pending, or running with an expired lock)."""

    @abstractmethod
    def complete(self, job_id: int) -> None: ...

    @abstractmethod
    def fail(
        self, job_id: int, *, error: str, retry_at: str | None = None, dead: bool = False
    ) -> None:
        """Record a failure. Retries with exponential backoff until max_attempts, then dead-letters.
        `retry_at` overrides the backoff; `dead=True` dead-letters immediately."""

    @abstractmethod
    def cancel(self, job_id: int) -> bool: ...

    @abstractmethod
    def dead_letters(self) -> list[Job]: ...

    @abstractmethod
    def stats(self) -> dict[str, int]: ...


class SqliteTaskQueue(TaskQueue):
    """`jobs` table in the ZenFlow SQLite database (schema in bot/db.py)."""

    def _conn(self) -> Any:
        return dbmod.get_db()

    # ── enqueue ──
    def enqueue(
        self,
        name: str,
        payload: dict[str, Any],
        *,
        run_at: str | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> int:
        conn = self._conn()
        if idempotency_key:
            row = conn.execute(
                "SELECT id FROM jobs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if row:
                return int(row["id"])
        now = clock.iso_now()
        when = clock.normalize(run_at) if run_at else now
        cur = conn.execute(
            """INSERT OR IGNORE INTO jobs
               (name, payload_json, run_at, status, attempts, max_attempts, idempotency_key,
                created_at, updated_at)
               VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)""",
            (
                name,
                json.dumps(payload, ensure_ascii=False, default=str),
                when,
                int(max_attempts),
                idempotency_key,
                now,
                now,
            ),
        )
        if cur.rowcount == 0 and idempotency_key:  # lost a race on the UNIQUE key
            row = conn.execute(
                "SELECT id FROM jobs WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            return int(row["id"])
        return int(cur.lastrowid or 0)

    # ── claim ──
    def claim(self, *, worker_id: str, limit: int = 1) -> list[Job]:
        now_dt = clock.now_utc()
        now = clock.to_iso(now_dt)
        stale_before = clock.to_iso(now_dt - timedelta(seconds=LOCK_TIMEOUT_SECONDS))
        rows = (
            self._conn()
            .execute(
                """UPDATE jobs
               SET status='running', locked_by=?, locked_at=?, attempts=attempts+1, updated_at=?
               WHERE id IN (
                   SELECT id FROM jobs
                   WHERE (status='pending' AND run_at <= ?)
                      OR (status='running' AND locked_at < ?)
                   ORDER BY run_at, id
                   LIMIT ?
               )
               RETURNING *""",
                (worker_id, now, now, now, stale_before, int(limit)),
            )
            .fetchall()
        )
        return [Job.from_row(r) for r in rows]

    # ── outcomes ──
    def complete(self, job_id: int) -> None:
        now = clock.iso_now()
        self._conn().execute(
            """UPDATE jobs SET status='done', completed_at=?, updated_at=?,
                               locked_by=NULL, locked_at=NULL
               WHERE id=?""",
            (now, now, job_id),
        )

    def fail(
        self, job_id: int, *, error: str, retry_at: str | None = None, dead: bool = False
    ) -> None:
        conn = self._conn()
        row = conn.execute(
            "SELECT attempts, max_attempts FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not row:
            return
        attempts, max_attempts = int(row["attempts"]), int(row["max_attempts"])
        now_dt = clock.now_utc()
        now = clock.to_iso(now_dt)
        err = (error or "")[:MAX_ERROR_LEN]
        if dead or (retry_at is None and attempts >= max_attempts):
            conn.execute(
                """UPDATE jobs SET status='dead', last_error=?, updated_at=?,
                                   locked_by=NULL, locked_at=NULL
                   WHERE id=?""",
                (err, now, job_id),
            )
            return
        if retry_at is None:
            delay = BACKOFF_BASE_SECONDS * (2 ** max(attempts - 1, 0))
            retry_at = clock.to_iso(now_dt + timedelta(seconds=delay))
        conn.execute(
            """UPDATE jobs SET status='pending', run_at=?, last_error=?, updated_at=?,
                               locked_by=NULL, locked_at=NULL
               WHERE id=?""",
            (clock.normalize(retry_at), err, now, job_id),
        )

    def cancel(self, job_id: int) -> bool:
        cur = self._conn().execute(
            """UPDATE jobs SET status='cancelled', updated_at=?, locked_by=NULL, locked_at=NULL
               WHERE id=? AND status IN ('pending', 'running')""",
            (clock.iso_now(), job_id),
        )
        return int(cur.rowcount or 0) > 0

    # ── introspection ──
    def dead_letters(self) -> list[Job]:
        rows = (
            self._conn()
            .execute("SELECT * FROM jobs WHERE status='dead' ORDER BY updated_at, id")
            .fetchall()
        )
        return [Job.from_row(r) for r in rows]

    def stats(self) -> dict[str, int]:
        out = dict.fromkeys(STATUSES, 0)
        for row in self._conn().execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"):
            out[str(row["status"])] = int(row["n"])
        return out


def get_default_queue() -> TaskQueue:
    """The backend selected by ZF_QUEUE_BACKEND (only `inprocess` exists until Phase 12)."""
    from zenflow.settings import get_settings

    backend = get_settings().flags.queue_backend
    if backend == "inprocess":
        return SqliteTaskQueue()
    raise NotImplementedError(
        f"ZF_QUEUE_BACKEND={backend!r}: the {backend} TaskQueue implementation arrives in Phase 12 "
        "(ADR-20); use 'inprocess' until then"
    )
