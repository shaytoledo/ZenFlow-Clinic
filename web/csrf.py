"""
web/csrf.py
────────────
Cross-site request forgery protection (Phase 9.3), by the double-submit-cookie pattern.

The dashboard authenticates with an ambient cookie, so a page on another origin can make a logged-in
therapist's browser send a state-changing request. Nothing stopped that before this.

**The token lives in two places that a cross-site attacker cannot both control.** A random value is
set as a *readable* cookie (`zf_csrf`, not HttpOnly — same-origin script must read it). Every unsafe
request has to echo that value back, in the `X-CSRF-Token` header (fetch/XHR) or a `csrf_token` form
field (a native `<form>` post). An attacker's page can cause the cookie to ride along, but cannot
read it to copy it into the header or field, and the same-origin policy keeps it from reading our
pages. A request whose two copies do not match is refused.

**What is exempt, and why it is safe:**

- **Safe methods** (GET/HEAD/OPTIONS) change nothing.
- **A request carrying `Authorization`** — the booking API's key. A browser never attaches that
  header on its own, so such a request was not forged by an ambient cookie. (The booking API also
  accepts a *session*; a session-authenticated call to it is not exempt and needs the token, which
  is why the check keys on the header, not on the path.)
- **The WhatsApp webhook** — no cookie at all; it proves itself with Meta's signature.

The token is compared with `secrets.compare_digest`, and `protect` never trusts a path or a claim
from the request body to decide whether to check.
"""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Request

COOKIE = "zf_csrf"
HEADER = "x-csrf-token"
FIELD = "csrf_token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
#: signature-authenticated routes that have no cookie and cannot carry a token
EXEMPT_PREFIXES = ("/api/webhooks/",)
_FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")
TOKEN_BYTES = 32


def issue() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def current(request: Request) -> str | None:
    return request.cookies.get(COOKIE)


def ensure_cookie(request: Request, response: object) -> None:
    """Give a visitor a CSRF cookie if they have none, so a form or script can read it.

    Set only when absent: the value must stay stable, or a token minted for a page would not match
    the cookie by the time the page submits.
    """
    if request.cookies.get(COOKIE):
        return
    from zenflow.settings import get_settings

    set_cookie = getattr(response, "set_cookie", None)
    if set_cookie is None:
        return
    set_cookie(
        COOKIE,
        issue(),
        max_age=_max_age(),
        httponly=False,  # same-origin JS must read it to echo it back
        samesite="lax",
        secure=not get_settings().is_dev,
        path="/",
    )


async def protect(request: Request) -> None:
    """Refuse an unsafe, cookie-authenticated request whose CSRF token is missing or wrong."""
    if request.method in SAFE_METHODS:
        return
    if request.url.path.startswith(EXEMPT_PREFIXES):
        return
    if request.headers.get("authorization"):  # API key — not an ambient-cookie request
        return

    cookie = request.cookies.get(COOKIE)
    submitted = await _submitted(request)
    if not cookie or not submitted or not secrets.compare_digest(submitted, cookie):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


async def _submitted(request: Request) -> str | None:
    header = request.headers.get(HEADER)
    if header:
        return header
    content_type = request.headers.get("content-type", "")
    if any(content_type.startswith(kind) for kind in _FORM_TYPES):
        form = await request.form()  # cached on this request; the endpoint re-reads the same object
        value = form.get(FIELD)
        return value if isinstance(value, str) else None
    return None


def _max_age() -> int:
    from web.session_policy import max_hours

    return max_hours() * 3600
