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
from contextvars import ContextVar
from typing import Any

from zenflow import logging as zlog
from zenflow.queue import LOCK_TIMEOUT_SECONDS, Job, TaskQueue

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], Awaitable[None]]
DeadHook = Callable[[dict[str, Any], str], Awaitable[None]]


class HandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {}
        self._dead_hooks: dict[str, DeadHook] = {}

    def handler(self, name: str) -> Callable[[Handler], Handler]:
        def _register(fn: Handler) -> Handler:
            if name in self._handlers and self._handlers[name] is not fn:
                raise ValueError(f"job handler {name!r} registered twice")
            self._handlers[name] = fn
            return fn

        return _register

    def on_dead(self, name: str) -> Callable[[DeadHook], DeadHook]:
        """Register `async def hook(payload, error)` called when a job of `name` exhausts its
        attempts on a handler error or timeout (not when a crashed worker's lock expires)."""

        def _register(fn: DeadHook) -> DeadHook:
            self._dead_hooks[name] = fn
            return fn

        return _register

    def get(self, name: str) -> Handler | None:
        return self._handlers.get(name)

    def dead_hook(self, name: str) -> DeadHook | None:
        return self._dead_hooks.get(name)

    def names(self) -> list[str]:
        return sorted(self._handlers)


default_registry = HandlerRegistry()

#: The job currently being handled (None outside a handler). Lets a handler decide, for example,
#: to alert a human only on its final attempt instead of on every retry.
_current_job: ContextVar[Job | None] = ContextVar("zenflow_current_job", default=None)

#: Modules whose import registers handlers on `default_registry`.
DEFAULT_HANDLER_MODULES: tuple[str, ...] = (
    "bot.services.followup_jobs",
    "bot.services.pipeline_jobs",
)


def current_job() -> Job | None:
    return _current_job.get()


def is_last_attempt() -> bool:
    job = _current_job.get()
    return job is not None and job.attempts >= job.max_attempts


def load_default_handlers() -> None:
    import importlib

    for module in DEFAULT_HANDLER_MODULES:
        importlib.import_module(module)


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
        if handler_timeout >= LOCK_TIMEOUT_SECONDS:
            raise ValueError(
                f"handler_timeout ({handler_timeout}s) must be shorter than "
                f"queue.LOCK_TIMEOUT_SECONDS ({LOCK_TIMEOUT_SECONDS}s), or a still-running job "
                "would be reclaimed and executed twice"
            )
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

    async def _maybe_dead_hook(self, job: Job, error: str) -> None:
        """The attempt that just failed was the last one → the job is dead: tell the owner."""
        if job.attempts < job.max_attempts:
            return
        hook = self.registry.dead_hook(job.name)
        if hook is None:
            return
        try:
            await hook(job.payload, error)
        except Exception:
            logger.exception("dead-letter hook for %s failed", job.name)

    async def _process(self, job: Job) -> None:
        fields = {
            "request_id": f"job-{job.id}-{zlog.new_request_id()[:8]}",
            "job": job.name,
            "appointment_id": job.payload.get("appointment_id"),
            "patient_id": job.payload.get("patient_id"),
        }
        token = _current_job.set(job)
        try:
            await self._run_handler(job, fields)
        finally:
            _current_job.reset(token)

    async def _run_handler(self, job: Job, fields: dict[str, Any]) -> None:
        with zlog.log_context(**fields):
            handler = self.registry.get(job.name)
            if handler is None:
                msg = f"no handler registered for job {job.name!r}"
                logger.error(msg)
                self.queue.fail(job.id, error=msg, dead=True, worker_id=self.worker_id)
                return
            try:
                with zlog.timed(logger, "job completed", attempt=job.attempts):
                    await asyncio.wait_for(handler(job.payload), timeout=self.handler_timeout)
            except asyncio.CancelledError:
                # Worker shutting down mid-flight: hand the job back immediately (not charged),
                # instead of leaving it locked until LOCK_TIMEOUT expires.
                self.queue.release(job.id, worker_id=self.worker_id)
                logger.info("job released on worker cancellation")
                raise
            except TimeoutError:
                msg = f"handler timed out after {self.handler_timeout}s"
                logger.error(msg)
                if self.queue.fail(job.id, error=msg, worker_id=self.worker_id):
                    await self._maybe_dead_hook(job, msg)
                return
            except Exception as exc:
                logger.exception("job failed (attempt %s/%s)", job.attempts, job.max_attempts)
                msg = f"{type(exc).__name__}: {exc}"
                if self.queue.fail(job.id, error=msg, worker_id=self.worker_id):
                    await self._maybe_dead_hook(job, msg)
                return
            if not self.queue.complete(job.id, worker_id=self.worker_id):
                logger.warning("job finished but was no longer ours (cancelled or reclaimed)")


def start_in_process(
    queue: TaskQueue | None = None, registry: HandlerRegistry | None = None, **kw: Any
) -> asyncio.Task[None]:
    """Run the worker as an asyncio task inside the current process (local dev / single box)."""
    from zenflow.queue import get_default_queue

    if registry is None:
        load_default_handlers()
    worker = Worker(queue or get_default_queue(), registry, **kw)
    return asyncio.create_task(worker.run_forever(), name="zenflow-worker")


def main() -> None:
    """`python -m zenflow.worker` — standalone worker process."""
    import bot.db as dbmod
    from zenflow.queue import get_default_queue

    zlog.configure_logging("worker")
    dbmod.init_db()  # this process does not import bot.config, which normally creates the schema
    load_default_handlers()
    asyncio.run(Worker(get_default_queue()).run_forever())


if __name__ == "__main__":
    main()
