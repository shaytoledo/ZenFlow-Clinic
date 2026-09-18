"""
zenflow/tracing.py
───────────────────
OpenTelemetry tracing, behind `ZF_TRACING=1` (Phase 8.4).

The flag exists so that Phase 12 can turn tracing on in a container without a code change, and so
that the seam is written and tested now rather than invented under deployment pressure. The
OpenTelemetry packages are **not** installed in this project yet — installing anything is the
owner's call — so asking for tracing today logs one clear warning and changes nothing else:

    status() == "off"          the flag is not set
    status() == "unavailable"  the flag is set, the library is not installed
    status() == "on"           the flag is set and instrumentation is running

An application must start whatever the answer is. Observability that can take the clinic down is
not observability.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: what to install to make ZF_TRACING=1 do something (Phase 12; the owner decides, not the agent)
PACKAGES = ("opentelemetry-sdk", "opentelemetry-instrumentation-fastapi")

_status = "off"


def reset() -> None:
    """Forget what a previous setup decided (tests, and a reload in development)."""
    global _status
    _status = "off"


def status() -> str:
    return _status


def enabled() -> bool:
    return _status == "on"


def wanted() -> bool:
    try:
        from zenflow.settings import get_settings

        return bool(get_settings().flags.tracing)
    except Exception:  # an unreadable environment is not a reason to trace
        return False


def setup(app: Any = None) -> str:
    """Instrument `app` if tracing is asked for and possible. Returns the resulting status."""
    global _status
    if not wanted():
        _status = "off"
        return _status
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        _status = "unavailable"
        logger.warning(
            "ZF_TRACING=1 but OpenTelemetry is not installed — tracing stays off. "
            "Install %s to enable it.",
            " and ".join(PACKAGES),
        )
        return _status
    try:
        if app is not None:
            FastAPIInstrumentor.instrument_app(app)
        _status = "on"
        logger.info("tracing enabled (OpenTelemetry, FastAPI instrumentation)")
    except Exception as exc:  # a broken exporter must not stop the application
        _status = "unavailable"
        logger.warning("tracing could not start: %s", exc)
    return _status
