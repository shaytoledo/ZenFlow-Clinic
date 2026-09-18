"""
web/services/ai_calls.py
─────────────────────────
The AI meter (Phase 8.2): what every model call cost, how long it took, and what it did when it
failed.

    from web.services import ai_calls
    resp = await ai_calls.ask(
        _LLM, messages, stage="intake.question", timeout_seconds=OLLAMA_TIMEOUT
    )

`ask()` is the **only** place in the system that calls a model: it applies the timeout, returns
what the model returned (or raises exactly what it raised, so every caller keeps its fallback),
and leaves one row in `ai_calls` either way. A test walks the source tree and fails if a second
`ainvoke` appears anywhere else — a call nobody can cost, time or explain is the thing this table
exists to prevent.

**The prompt is a hash.** `prompt_sha256` / `response_sha256` are enough to see that two calls were
the same call, that a diagnosis came from the summary the therapist is looking at, or that a
pattern of failures shares one input. The text itself is clinical data and is not stored — except
on a developer's own machine, where `ZF_AI_DEBUG_PROMPTS=1` **and** a dev or test environment
together keep a copy in `prompt_debug` / `response_debug`. A staging or production database never
holds one, whatever the flag says. `forget_prompts()` drops those copies again; the hashes and the
metrics outlive them.

**Best effort.** A failed insert is logged and swallowed. Losing a metric must never lose the
patient's answer.

**Reading it.** `summary(hours=24)` is the cost/latency/failure view Phase 8.4 exposes at
`/api/admin/metrics`; `history(appointment_id)` is every call made for one appointment.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from math import ceil
from typing import Any

from zenflow import clock
from zenflow.logging import redact

logger = logging.getLogger(__name__)

STATUSES = ("ok", "error", "timeout")
#: a debug copy is a developer's convenience, not a store — long prompts are cut
MAX_DEBUG_LEN = 8000
MAX_ERROR_LEN = 500
#: how the classes we know name themselves
_PROVIDERS = {"ChatOllama": "ollama", "ChatAnthropic": "anthropic", "ChatOpenAI": "openai"}

CREATE_AI_CALLS = """CREATE TABLE IF NOT EXISTS ai_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    appointment_id INTEGER,
    stage TEXT NOT NULL,
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('ok','error','timeout')),
    error TEXT,
    prompt_sha256 TEXT NOT NULL DEFAULT '',
    response_sha256 TEXT,
    prompt_debug TEXT,
    response_debug TEXT
)"""
CREATE_AI_CALL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_ai_calls_appointment ON ai_calls(appointment_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_ai_calls_ts ON ai_calls(ts)",
    "CREATE INDEX IF NOT EXISTS idx_ai_calls_stage ON ai_calls(stage, ts)",
)


def _conn() -> sqlite3.Connection:
    from bot.db import get_db

    return get_db()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(CREATE_AI_CALLS)
    for statement in CREATE_AI_CALL_INDEXES:
        conn.execute(statement)


# ── which appointment a call belongs to ──
_appointment: ContextVar[int | None] = ContextVar("zenflow_ai_appointment", default=None)


@contextmanager
def for_appointment(appointment_id: int | None) -> Iterator[None]:
    """Every model call inside this block is recorded against that appointment."""
    token = _appointment.set(int(appointment_id) if appointment_id is not None else None)
    try:
        yield
    finally:
        _appointment.reset(token)


def current_appointment() -> int | None:
    return _appointment.get()


# ── the one place that calls a model ──
async def ask(
    model: Any,
    messages: Any,
    *,
    stage: str,
    timeout_seconds: float,
    appointment_id: int | None = None,
) -> Any:
    """Call the model with a timeout and meter it. Raises whatever the model raised."""
    provider, name = identify(model)
    prompt = _text(messages)
    started = time.perf_counter()
    try:
        response = await asyncio.wait_for(model.ainvoke(messages), timeout=timeout_seconds)
    except TimeoutError:
        record(
            stage,
            provider=provider,
            model=name,
            duration_ms=_ms(started),
            status="timeout",
            prompt=prompt,
            error=f"timed out after {timeout_seconds}s",
            appointment_id=appointment_id,
        )
        raise
    except Exception as exc:
        record(
            stage,
            provider=provider,
            model=name,
            duration_ms=_ms(started),
            status="error",
            prompt=prompt,
            error=f"{type(exc).__name__}: {exc}",
            appointment_id=appointment_id,
        )
        raise
    prompt_tokens, completion_tokens = _tokens(response)
    record(
        stage,
        provider=provider,
        model=name,
        duration_ms=_ms(started),
        status="ok",
        prompt=prompt,
        response=_content(response),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        appointment_id=appointment_id,
    )
    return response


# ── writing ──
def record(
    stage: str,
    *,
    provider: str = "",
    model: str = "",
    duration_ms: int = 0,
    status: str = "ok",
    prompt: str | None = None,
    response: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    error: str | None = None,
    appointment_id: int | None = None,
) -> None:
    """Append one row. Never raises: the call it describes has already happened."""
    try:
        keep = keeps_prompts()
        _insert(
            (
                clock.iso_now(),
                appointment_id if appointment_id is not None else current_appointment(),
                stage,
                provider,
                model,
                prompt_tokens,
                completion_tokens,
                max(int(duration_ms), 0),
                status if status in STATUSES else "error",
                _trim(redact(error) if error else None, MAX_ERROR_LEN),
                _sha256(prompt) or "",
                _sha256(response),
                _trim(prompt, MAX_DEBUG_LEN) if keep else None,
                _trim(response, MAX_DEBUG_LEN) if keep else None,
            )
        )
    except Exception:
        logger.exception("ai_calls row not written: %s (%s)", stage, status)


def _insert(values: tuple[Any, ...]) -> None:
    _conn().execute(
        """INSERT INTO ai_calls (ts, appointment_id, stage, provider, model, prompt_tokens,
                                 completion_tokens, duration_ms, status, error, prompt_sha256,
                                 response_sha256, prompt_debug, response_debug)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values,
    )


def keeps_prompts() -> bool:
    """A clinical prompt is kept in the clear only where the database is a developer's own —
    `ENV=dev` or a test run — and only when that developer asks for it."""
    try:
        from zenflow.settings import get_settings

        settings = get_settings()
        return bool(settings.is_dev and settings.flags.ai_debug_prompts)
    except Exception:  # a misconfigured environment must not decide to keep more
        return False


def forget_prompts(older_than_days: float = 7) -> int:
    """Drop the debug copies older than the retention window; the hashes stay. Returns the rows."""
    cutoff = clock.hours_ago(24 * float(older_than_days))
    cursor = _conn().execute(
        """UPDATE ai_calls SET prompt_debug=NULL, response_debug=NULL
           WHERE ts <= ? AND (prompt_debug IS NOT NULL OR response_debug IS NOT NULL)""",
        (cutoff,),
    )
    return int(cursor.rowcount or 0)


# ── reading ──
def history(appointment_id: int, limit: int = 100) -> list[dict[str, Any]]:
    """Every model call made for one appointment, newest first (8.5 shows this per session)."""
    rows = _conn().execute(
        "SELECT * FROM ai_calls WHERE appointment_id=? ORDER BY id DESC LIMIT ?",
        (int(appointment_id), int(limit)),
    )
    return [dict(r) for r in rows]


def summary(hours: float = 24) -> dict[str, Any]:
    """Cost, latency and failure rate over a window — the view 8.4 serves at /api/admin/metrics."""
    rows = (
        _conn()
        .execute(
            """SELECT stage, status, duration_ms, prompt_tokens, completion_tokens
           FROM ai_calls WHERE ts >= ?""",
            (clock.hours_ago(float(hours)),),
        )
        .fetchall()
    )

    per_stage: dict[str, list[Any]] = {}
    durations: list[int] = []
    failures = prompt_tokens = completion_tokens = 0
    for row in rows:
        durations.append(int(row["duration_ms"] or 0))
        failed = row["status"] != "ok"
        failures += 1 if failed else 0
        prompt_tokens += int(row["prompt_tokens"] or 0)
        completion_tokens += int(row["completion_tokens"] or 0)
        per_stage.setdefault(row["stage"], []).append((int(row["duration_ms"] or 0), failed))

    return {
        "window_hours": float(hours),
        "calls": len(durations),
        "failures": failures,
        "failure_rate": round(failures / len(durations), 4) if durations else 0.0,
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "stages": {
            stage: {
                "calls": len(calls),
                "failures": sum(1 for _ms_, failed in calls if failed),
                "p50_ms": percentile([ms for ms, _f in calls], 50),
                "p95_ms": percentile([ms for ms, _f in calls], 95),
            }
            for stage, calls in sorted(per_stage.items())
        },
    }


def percentile(values: list[int], p: float) -> int:
    """Nearest-rank: the smallest value at or above p% of the sample. 0 for an empty one."""
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, ceil(p / 100 * len(ordered)) - 1))
    return ordered[index]


# ── what a model is, and what it said ──
def identify(model: Any) -> tuple[str, str]:
    """(provider, model name) for whatever chat model was handed to `ask`."""
    class_name = type(model).__name__
    provider = _PROVIDERS.get(class_name, "")
    name = ""
    for attribute in ("model", "model_name", "model_id"):
        value = getattr(model, attribute, None)
        if isinstance(value, str) and value:
            name = value
            break
    if provider and name:
        return provider, name
    try:
        from zenflow.settings import get_settings

        settings = get_settings()
        provider = provider or str(settings.flags.ai_provider or settings.use_ai or "")
        name = name or (settings.ollama_model if provider == "ollama" else "")
    except Exception:  # an unreadable environment costs a label, never the metric
        logger.debug("ai_calls: settings unavailable while naming the model", exc_info=True)
    return provider or class_name.lower(), name or class_name


def _tokens(response: Any) -> tuple[int | None, int | None]:
    """What the model said it spent, in whichever shape its client reports it."""
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        return _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))
    meta = getattr(response, "response_metadata", None)
    if not isinstance(meta, dict):
        return None, None
    for source in (meta.get("usage"), meta.get("token_usage"), meta):
        if not isinstance(source, dict):
            continue
        prompt = _first(source, "input_tokens", "prompt_tokens", "prompt_eval_count")
        completion = _first(source, "output_tokens", "completion_tokens", "eval_count")
        if prompt is not None or completion is not None:
            return prompt, completion
    return None, None


def _first(source: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = _int(source.get(name))
        if value is not None:
            return value
    return None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _content(response: Any) -> str:
    return _text(getattr(response, "content", response))


def _text(messages: Any) -> str:
    """One string for the prompt, whatever shape the caller built it in."""
    if isinstance(messages, str):
        return messages
    if isinstance(messages, list | tuple):
        return "\n".join(_text(m) for m in messages)
    content = getattr(messages, "content", None)
    return _text(content) if content is not None else str(messages)


def _sha256(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _trim(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"
