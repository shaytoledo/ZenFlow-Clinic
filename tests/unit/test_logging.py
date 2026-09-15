"""Phase 0 task 0.5 — structured logging, redaction, request context.

Every record carries ts, level, logger, event, request_id, therapist_id, patient_id,
appointment_id, duration_ms. Secrets never reach a handler. Context is bound per request/job.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any

import pytest

from zenflow import logging as zlog

TG_TOKEN = "8123456789:AAHfakeFAKEfakeFAKEfakeFAKEfakeFAKE01"
SAMPLES = [
    (f"calling https://api.telegram.org/bot{TG_TOKEN}/getMe", TG_TOKEN),
    ("Authorization: Bearer ya29.a0AfH6SMBxyzFAKEtoken_value-1234567890", "ya29.a0AfH6SMB"),
    ("Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcDEF-ghi", "eyJhbGciOiJIUzI1NiJ9"),
    ("client secret GOCSPX-AbCdEfGhIjKlMnOpQrStUvWx", "GOCSPX-AbCdEf"),
    ('{"refresh_token": "1//0gFAKEfakeFAKEfakeFAKEfakeFAKEfake"}', "1//0gFAKE"),
    ("key AIzaSyA-FAKEfakeFAKEfakeFAKEfakeFAKE123456", "AIzaSyA-FAKE"),
    ("anthropic sk-ant-api03-FAKEfakeFAKEfakeFAKEfakeFAKE", "sk-ant-api03-FAKE"),
    ('{"access_token": "abc123SECRET"}', "abc123SECRET"),
    ("password=hunter2&next=/", "hunter2"),
    ("SESSION_SECRET=supersecretvalue123", "supersecretvalue123"),
    ("fernet gAAAAABkFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKEfakeFAKE==", "gAAAAABkFAKE"),
]


@pytest.mark.parametrize(("text", "secret"), SAMPLES)
def test_redact_scrubs_every_known_secret_shape(text: str, secret: str) -> None:
    out = zlog.redact(text)
    assert secret not in out, out
    assert "redacted" in out.lower()


def test_redact_leaves_ordinary_text_alone() -> None:
    text = "patient 918187404 booked 2026-03-02 10:00 with t1; status=active"
    assert zlog.redact(text) == text


def _capture(fmt: logging.Formatter) -> tuple[logging.Logger, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(fmt)
    logger = logging.getLogger("zenflow.test.capture")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    return logger, stream


@pytest.fixture(autouse=True)
def _configured():
    zlog.configure_logging("test", fmt="console", install_file_handler=False)
    zlog.clear_context()
    yield
    zlog.clear_context()


def test_json_formatter_emits_every_required_key() -> None:
    logger, stream = _capture(zlog.JsonFormatter())
    with zlog.log_context(request_id="req-1", therapist_id="t1", patient_id=7, appointment_id=3):
        logger.info("booking saved", extra={"duration_ms": 12.5})
    rec = json.loads(stream.getvalue().strip())
    assert set(zlog.REQUIRED_KEYS) <= set(rec)
    assert rec["event"] == "booking saved"
    assert rec["level"] == "INFO"
    assert rec["logger"] == "zenflow.test.capture"
    assert rec["request_id"] == "req-1"
    assert rec["therapist_id"] == "t1"
    assert rec["patient_id"] == 7
    assert rec["appointment_id"] == 3
    assert rec["duration_ms"] == 12.5
    assert rec["ts"].endswith("Z")


def test_json_formatter_outputs_one_line_per_record_even_for_multiline_messages() -> None:
    logger, stream = _capture(zlog.JsonFormatter())
    logger.warning("line one\nline two")
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "line one\nline two"


def test_secrets_are_scrubbed_from_message_args_and_exceptions() -> None:
    logger, stream = _capture(zlog.JsonFormatter())
    try:
        raise RuntimeError(f"upstream said Bearer {TG_TOKEN}")
    except RuntimeError:
        logger.error("send failed for token %s", TG_TOKEN, exc_info=True)
    out = stream.getvalue()
    assert TG_TOKEN not in out
    assert "redacted" in out.lower()
    assert "RuntimeError" in out


def test_console_formatter_is_human_readable_and_carries_context() -> None:
    logger, stream = _capture(zlog.ConsoleFormatter())
    with zlog.log_context(request_id="req-9", therapist_id="t2"):
        logger.info("hello %s", "world")
    line = stream.getvalue().strip()
    assert "hello world" in line
    assert "req-9" in line
    assert "therapist_id=t2" in line
    assert not line.startswith("{")


def test_bind_and_log_context_scoping() -> None:
    assert zlog.get_context() == {}
    zlog.bind(therapist_id="t1")
    assert zlog.get_context()["therapist_id"] == "t1"
    with zlog.log_context(patient_id=5):
        assert zlog.get_context() == {"therapist_id": "t1", "patient_id": 5}
    assert "patient_id" not in zlog.get_context()
    zlog.clear_context()
    assert zlog.get_context() == {}


def test_context_is_visible_in_records_captured_by_any_handler(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The record factory injects context, so even pytest's caplog handler sees it."""
    with caplog.at_level(logging.INFO), zlog.log_context(request_id="req-cap", appointment_id=42):
        logging.getLogger("zenflow.test.factory").info("captured")
    rec: Any = caplog.records[-1]  # context fields resolve lazily via ZenLogRecord.__getattr__
    assert rec.request_id == "req-cap"
    assert rec.appointment_id == 42
    assert rec.therapist_id is None


def test_timed_adds_duration_ms() -> None:
    logger, stream = _capture(zlog.JsonFormatter())
    with zlog.timed(logger, "ollama call", appointment_id=9):
        pass
    rec = json.loads(stream.getvalue().strip())
    assert rec["event"] == "ollama call"
    assert isinstance(rec["duration_ms"], float) and rec["duration_ms"] >= 0
    assert rec["appointment_id"] == 9


def test_new_request_id_is_unique_and_short() -> None:
    ids = {zlog.new_request_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(8 <= len(i) <= 32 for i in ids)


def test_configure_logging_picks_format_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    from zenflow import settings as S

    def _ours() -> list[logging.Handler]:
        return [h for h in logging.getLogger().handlers if getattr(h, "_zenflow", False)]

    monkeypatch.setenv("LOG_FORMAT", "json")
    S.reset_settings()
    try:
        zlog.configure_logging("test", install_file_handler=False)
        assert _ours() and all(isinstance(h.formatter, zlog.JsonFormatter) for h in _ours())
        monkeypatch.setenv("LOG_FORMAT", "console")
        S.reset_settings()
        zlog.configure_logging("test", install_file_handler=False)
        assert _ours() and all(isinstance(h.formatter, zlog.ConsoleFormatter) for h in _ours())
        monkeypatch.setenv("LOG_FORMAT", "auto")
        S.reset_settings()
        zlog.configure_logging("test", install_file_handler=False)  # ENV=test ⇒ dev ⇒ console
        assert _ours() and all(isinstance(h.formatter, zlog.ConsoleFormatter) for h in _ours())
    finally:
        S.reset_settings()


def test_configure_logging_is_idempotent() -> None:
    zlog.configure_logging("test", fmt="json", install_file_handler=False)
    zlog.configure_logging("test", fmt="json", install_file_handler=False)
    root = logging.getLogger()
    assert len([h for h in root.handlers if getattr(h, "_zenflow", False)]) == 1
