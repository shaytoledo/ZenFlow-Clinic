"""Phase 11.6 — Locust load scenario: 50 therapists on the dashboard, 200 patients booking.

Run against a LOCAL or STAGING server only (never production — see docs/THREAT_MODEL.md rules of
engagement). The weights model the plan's 50:200 therapist-to-booking ratio (1:4).

    # 0. install the tool (kept out of the dev lock — it pulls a heavy gevent/flask tree):
    pip install locust==2.46.6
    # 1. start the app (see startup/START.md) and seed a therapist + a booking-API key:
    python -m zenflow.api_keys create load-test          # prints the key once
    # 2. run headless (or drop --headless for the web UI):
    ZF_LOAD_THERAPIST_EMAIL=you@clinic.test ZF_LOAD_THERAPIST_PASSWORD=... \\
    ZF_LOAD_THERAPIST_ID=t1 ZF_LOAD_API_KEY=<key> \\
    locust -f tests/load/locustfile.py --host http://localhost:8080 \\
           --headless -u 250 -r 25 -t 2m

`-u 250 -r 25` ramps to 250 concurrent users at 25/s; with the 1:4 weights that is ~50 therapists +
~200 booking clients. Bookings under contention will legitimately return 409 (slot taken) / 422 —
that is real load behaviour, not a script bug. Requires availability seeded for the therapist.
"""

from __future__ import annotations

# mypy: ignore-errors
# (locust is install-on-demand, not a mypy dependency, so HttpUser resolves to Any in CI; this is a
# runnable load script, not typed app code.)
# ruff: noqa: S311 - random here only varies load-test data (names, slots); it is not cryptographic.
import os
import random
from datetime import UTC, datetime, timedelta

from locust import HttpUser, between, task

_EMAIL = os.environ.get("ZF_LOAD_THERAPIST_EMAIL", "loadtest@example.com")
_PASSWORD = os.environ.get("ZF_LOAD_THERAPIST_PASSWORD", "pw-Load-123")
_THERAPIST_ID = os.environ.get("ZF_LOAD_THERAPIST_ID", "t1")
_API_KEY = os.environ.get("ZF_LOAD_API_KEY", "")


class TherapistDashboardUser(HttpUser):
    """A therapist working the dashboard: sign in once, then poll the read endpoints the UI hits."""

    weight = 1  # ~50 of 250
    wait_time = between(1, 5)

    def on_start(self) -> None:
        # Arm the double-submit CSRF cookie, then sign in through the real form (9.3).
        self.client.get("/healthz", name="/healthz")
        csrf = self.client.cookies.get("zf_csrf", "")
        self.client.post(
            "/register/signin",
            data={"email": _EMAIL, "password": _PASSWORD, "csrf_token": csrf},
            name="/register/signin",
        )

    @task(3)
    def dashboard(self) -> None:
        self.client.get("/", name="/ (dashboard)")

    @task(3)
    def today(self) -> None:
        self.client.get("/api/appointments/today", name="/api/appointments/today")

    @task(2)
    def unread(self) -> None:
        self.client.get("/api/notifications/unread-count", name="/api/notifications/unread-count")

    @task(1)
    def messages(self) -> None:
        self.client.get("/api/messages/active", name="/api/messages/active")


class BookingApiUser(HttpUser):
    """A machine client (WhatsApp bridge, etc.) reading and creating bookings via /api/v1."""

    weight = 4  # ~200 of 250
    wait_time = between(1, 3)

    def _headers(self, idempotency: str | None = None) -> dict[str, str]:
        h = {"X-API-Key": _API_KEY}
        if idempotency:
            h["Idempotency-Key"] = idempotency
        return h

    @task(3)
    def list_appointments(self) -> None:
        if not _API_KEY:
            return
        self.client.get(
            "/api/v1/appointments", headers=self._headers(), name="/api/v1/appointments [GET]"
        )

    @task(1)
    def book(self) -> None:
        if not _API_KEY:
            return
        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(
            days=random.randint(1, 14), hours=random.randint(0, 8)
        )
        key = f"load-{start.timestamp():.0f}-{random.randint(0, 10**9)}"
        self.client.post(
            "/api/v1/appointments",
            headers=self._headers(key),
            json={
                "therapist_id": _THERAPIST_ID,
                "start_at": start.isoformat().replace("+00:00", "Z"),
                "patient": {"name": f"Load Patient {random.randint(1, 10_000)}"},
                "source": "api",
                "send_confirmation": False,
            },
            name="/api/v1/appointments [POST]",
        )
