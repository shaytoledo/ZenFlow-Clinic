"""zenflow.worker — runs queued jobs by handler name (Phase 1.2, ADR-20).

Hosting (flag-driven, `ZF_QUEUE_BACKEND=inprocess`):
* inside the bot process — `bot/main.py` starts `start_in_process()` at post_init (local dev,
  single box);
* as its own process — `python -m zenflow.worker` (Phase 12 splits services).

Handlers are `async def handler(payload: dict) -> None`, registered by name::

    from zenflow.worker import default_registry

    @default_registry.handler("followup.send_step1")
    async def send_step1(payload: dict) -> None: ...

Every job runs under a structured-log context (`request_id=job-<id>-…`, `job`, and the
payload's `appointment_id` / `patient_id` when present), with a hard timeout. A failure is
recorded on the job (retry with backoff, then dead-letter); a missing handler dead-letters
immediately.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from zenflow import logging as zlog
from zenflow.queue import Job, TaskQueue

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}

    def handler(self, name: str) -> Callable[[Handler], Handler]:
        def _register(fn: Handler) -> Handler:
            if name in self._handlers and self._handlers[name] is not fn:
                raise ValueError(f"job handler {name!r} registered twice")
            self._handlers[name] = fn
            return fn

        return _register

    def get(self, name: str) -> Handler | None:
        return self._handlers.get(name)

    def names(self) -> list[str]:
        return sorted(self._handlers)


default_registry = HandlerRegistry()


class Worker:
    def __init__(
        self,
        queue: TaskQueue,
        registry: HandlerRegistry | None = None,
        *,
        worker_id: str | None = None,
        poll_interval: float = 5.0,
        batch: int = 5,
        handler_timeout: float = 300.0,
    ) -> None:
        self.queue = queue
        self.registry = registry if registry is not None else default_registry
        self.worker_id = worker_id or f"{socket.gethostname()}-{zlog.new_request_id()[:6]}"
        self.poll_interval = poll_interval
        self.batch = batch
        self.handler_timeout = handler_timeout

    async def run_once(self) -> int:
        """Claim and process one batch. Returns the number of jobs processed."""
        jobs = self.queue.claim(worker_id=self.worker_id, limit=self.batch)
        for job in jobs:
            await self._process(job)
        return len(jobs)

    async def run_forever(self) -> None:
        logger.info(
            "worker %s started (handlers: %s)", self.worker_id, ", ".join(self.registry.names())
        )
        while True:
            try:
                processed = await self.run_once()
            except asyncio.CancelledError:
                logger.info("worker %s cancelled", self.worker_id)
                raise
            except Exception:
                logger.exception("worker %s loop iteration failed", self.worker_id)
                processed = 0
            await asyncio.sleep(0 if processed else self.poll_interval)

    async def _process(self, job: Job) -> None:
        fields = {
            "request_id": f"job-{job.id}-{zlog.new_request_id()[:8]}",
            "job": job.name,
            "appointment_id": job.payload.get("appointment_id"),
            "patient_id": job.payload.get("patient_id"),
        }
        with zlog.log_context(**fields):
            handler = self.registry.get(job.name)
            if handler is None:
                msg = f"no handler registered for job {job.name!r}"
                logger.error(msg)
                self.queue.fail(job.id, error=msg, dead=True)
                return
            try:
                with zlog.timed(logger, "job completed", attempt=job.attempts):
                    await asyncio.wait_for(handler(job.payload), timeout=self.handler_timeout)
            except TimeoutError:
                msg = f"handler timed out after {self.handler_timeout}s"
                logger.error(msg)
                self.queue.fail(job.id, error=msg)
                return
            except Exception as exc:
                logger.exception("job failed (attempt %s/%s)", job.attempts, job.max_attempts)
                self.queue.fail(job.id, error=f"{type(exc).__name__}: {exc}")
                return
            self.queue.complete(job.id)


def start_in_process(
    queue: TaskQueue | None = None, registry: HandlerRegistry | None = None, **kw: Any
) -> asyncio.Task[None]:
    """Run the worker as an asyncio task inside the current process (local dev / single box)."""
    from zenflow.queue import get_default_queue

    worker = Worker(queue or get_default_queue(), registry, **kw)
    return asyncio.create_task(worker.run_forever(), name="zenflow-worker")


def main() -> None:
    """`python -m zenflow.worker` — standalone worker process."""
    from zenflow.queue import get_default_queue

    zlog.configure_logging("worker")
    asyncio.run(Worker(get_default_queue()).run_forever())


if __name__ == "__main__":
    main()
