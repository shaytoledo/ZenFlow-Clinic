"""zenflow.logging — structured logging, secret redaction, request/job context (Phase 0 task 0.5).

Every record carries: ts, level, logger, event, request_id, therapist_id, patient_id,
appointment_id, duration_ms (+ service, exc when present).

- Context (`bind`, `log_context`) lives in a ContextVar, so it follows the request through
  `asyncio.to_thread`, `create_task` and Starlette background tasks automatically.
- The context is injected by a **log-record factory**, so any handler — including pytest's
  caplog or a third-party one — sees the fields, not only our formatters.
- Redaction happens twice: in the record factory (message + string args) and in the formatters
  (final text, including exception tracebacks). `logs/` has leaked bot tokens before.
- Format is flag-driven (`LOG_FORMAT=auto|console|json`): human-readable in dev, JSON otherwise.

Stdlib only — no structlog dependency (plan 0.5 allows either).
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
import time
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REQUIRED_KEYS: tuple[str, ...] = (
    "ts",
    "level",
    "logger",
    "event",
    "request_id",
    "therapist_id",
    "patient_id",
    "appointment_id",
    "duration_ms",
)
CONTEXT_KEYS: tuple[str, ...] = ("request_id", "therapist_id", "patient_id", "appointment_id")

# ── Redaction ────────────────────────────────────────────────────────────────────────────────
_SECRET_KEYS = (
    "access_token|refresh_token|id_token|client_secret|api_key|apikey|password|passwd|"  # noqa: S105
    "session_secret|token_encryption_key|authorization|secret|token"  # key NAMES, not a secret
)
REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Telegram bot token 1234567890:AAH... (35 chars today; match >= 30 to survive format
    # changes). No leading \b: in URLs the token directly follows "bot".
    (re.compile(r"\d{8,10}:[A-Za-z0-9_-]{30,}"), "<redacted:telegram-token>"),
    # Google OAuth client secret / access token / refresh token / API key
    (re.compile(r"GOCSPX-[A-Za-z0-9_-]+"), "<redacted:google-client-secret>"),
    (re.compile(r"ya29\.[A-Za-z0-9._-]+"), "<redacted:google-access-token>"),
    (re.compile(r"1//0[A-Za-z0-9_-]{20,}"), "<redacted:google-refresh-token>"),
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "<redacted:google-api-key>"),
    # Anthropic API keys
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}"), "<redacted:anthropic-key>"),
    # Fernet tokens (encrypted Google credentials at rest)
    (re.compile(r"gAAAA[A-Za-z0-9_=-]{40,}"), "<redacted:fernet>"),
    # Bearer / JWT
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer <redacted>"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        "<redacted:jwt>",
    ),
    # key=value / "key": "value" shaped secrets
    (
        re.compile(rf"(?i)\b({_SECRET_KEYS})(\"?\s*[:=]\s*\"?)([^\s\"',;&}}]+)"),
        r"\1\2<redacted>",
    ),
)


_MAYBE_SECRET = re.compile(
    r"(?i)\d{8,10}:|GOCSPX|ya29\.|1//0|AIza|sk-ant|gAAAA|bearer|eyJ|token|secret|password|"
    r"passwd|api_?key|authorization"
)


def redact(text: str) -> str:
    """Scrub every known secret shape from `text`. Idempotent; safe on any string."""
    if not _MAYBE_SECRET.search(text):  # common case: one scan instead of eleven
        return text
    for pattern, replacement in REDACTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ── Context ──────────────────────────────────────────────────────────────────────────────────
_ctx: ContextVar[dict[str, Any]] = ContextVar("zenflow_log_context", default={})


def get_context() -> dict[str, Any]:
    return dict(_ctx.get())


def bind(**fields: Any) -> None:
    """Add fields to the current context (request / job / task scope)."""
    _ctx.set({**_ctx.get(), **fields})


def unbind(*keys: str) -> None:
    cur = _ctx.get()
    _ctx.set({k: v for k, v in cur.items() if k not in keys})


def clear_context() -> None:
    _ctx.set({})


@contextlib.contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Temporarily add fields; restored on exit (also across exceptions)."""
    token = _ctx.set({**_ctx.get(), **fields})
    try:
        yield
    finally:
        _ctx.reset(token)


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# ── Record factory: context + first-pass redaction ───────────────────────────────────────────
_CONTEXT_ATTRS: frozenset[str] = frozenset((*CONTEXT_KEYS, "duration_ms", "service"))


class ZenLogRecord(logging.LogRecord):
    """LogRecord that resolves context fields lazily.

    The context snapshot is stored under `_zf_ctx` and exposed through `__getattr__`, so the
    fields are visible on every record (`record.request_id`) WITHOUT being real instance
    attributes — `logger.info(..., extra={"duration_ms": 3})` would otherwise fail with
    "Attempt to overwrite 'duration_ms' in LogRecord".
    """

    _zf_ctx: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        if name in _CONTEXT_ATTRS:
            return self.__dict__.get("_zf_ctx", {}).get(name)
        raise AttributeError(name)


def _record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = ZenLogRecord(*args, **kwargs)
    if isinstance(record.msg, str):
        record.msg = redact(record.msg)
    if record.args:
        if isinstance(record.args, dict):
            record.args = {
                k: (redact(v) if isinstance(v, str) else v) for k, v in record.args.items()
            }
        else:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
    record._zf_ctx = dict(_ctx.get())
    return record


def _install_record_factory() -> None:
    if logging.getLogRecordFactory() is not _record_factory:
        logging.setLogRecordFactory(_record_factory)


# ── Formatters ───────────────────────────────────────────────────────────────────────────────
def _ts(record: logging.LogRecord) -> str:
    return (
        datetime.fromtimestamp(record.created, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    )


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Never multi-line, never unredacted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": _ts(record),
            "level": record.levelname,
            "logger": record.name,
            "event": redact(record.getMessage()),
            "request_id": getattr(record, "request_id", None),
            "therapist_id": getattr(record, "therapist_id", None),
            "patient_id": getattr(record, "patient_id", None),
            "appointment_id": getattr(record, "appointment_id", None),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        service = getattr(record, "service", None)
        if service:
            payload["service"] = service
        for key in ("method", "path", "status_code", "job"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        elif record.exc_text:
            payload["exc"] = redact(record.exc_text)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ConsoleFormatter(logging.Formatter):
    """Human-readable single line: time level logger [request_id] message key=value …"""

    def format(self, record: logging.LogRecord) -> str:
        rid = getattr(record, "request_id", None) or "-"
        parts = [
            _ts(record),
            f"{record.levelname:<5}",
            record.name,
            f"[{rid}]",
            redact(record.getMessage()),
        ]
        for key in ("therapist_id", "patient_id", "appointment_id", "duration_ms", "status_code"):
            value = getattr(record, key, None)
            if value is not None:
                parts.append(f"{key}={value}")
        line = " ".join(parts)
        if record.exc_info:
            line += " | " + redact(self.formatException(record.exc_info)).replace("\n", " | ")
        return line.replace("\n", " | ")


# ── Timing helper ────────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def timed(
    logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any
) -> Iterator[None]:
    """Log `event` with duration_ms (and any extra fields) when the block exits."""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = round((time.perf_counter() - start) * 1000, 1)
        logger.log(level, event, extra={"duration_ms": duration, **fields})


# ── Configuration ────────────────────────────────────────────────────────────────────────────
def configure_logging(
    service: str,
    *,
    fmt: str | None = None,
    level: str | None = None,
    file_path: Path | None = None,
    file_mode: str = "a",
    install_file_handler: bool = True,
) -> None:
    """Configure the root logger once per process (idempotent).

    fmt: "console" | "json" | None (→ LOG_FORMAT setting; "auto" = console in dev, json otherwise)
    """
    from zenflow.settings import get_settings

    settings = get_settings()
    chosen = fmt or settings.log_format
    if chosen == "auto":
        chosen = "console" if settings.is_dev else "json"
    formatter: logging.Formatter = JsonFormatter() if chosen == "json" else ConsoleFormatter()

    _install_record_factory()
    root = logging.getLogger()
    root.setLevel((level or settings.log_level).upper())
    root.handlers = [h for h in root.handlers if not getattr(h, "_zenflow", False)]

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console._zenflow = True  # type: ignore[attr-defined]
    root.addHandler(console)

    if install_file_handler and file_path is not None and settings.env != "test":
        file_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(file_path, encoding="utf-8", mode=file_mode)
        fh.setFormatter(formatter)
        fh._zenflow = True  # type: ignore[attr-defined]
        root.addHandler(fh)

    bind(service=service)
