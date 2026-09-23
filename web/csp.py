"""
web/csp.py
──────────
Content-Security-Policy and the per-request script nonce (Phase 9.4).

The dashboard renders HTML that holds medical records. A Content-Security-Policy tells the browser
which sources of script, style, image and font it may trust, so an injected `<script>` (or a stray
`javascript:` URL) does not run even if it reaches the page. This complements — it does not replace
— server-side output escaping.

**Script is nonce-based.** Every response mints one random nonce (`new_nonce`), publishes it to the
template layer, and pins it into `script-src 'self' 'nonce-…'`. An inline `<script>` the templates
emit carries `nonce="…"`, so it runs; an injected inline script cannot guess the value, so it does
not. External scripts are `'self'` (our own `/static`) plus the one CDN tag the schedule page loads,
which carries the nonce too. A nonce and `'unsafe-inline'` are mutually exclusive by design — the
presence of a nonce makes the browser ignore `'unsafe-inline'` for script, which is exactly the
protection we want.

**Style keeps `'unsafe-inline'` for now.** A nonce cannot cover an inline `style="…"` *attribute*
(only a `<style>` element), and the templates still use many. Styles cannot execute script, so this
is a far smaller exposure than an inline-script allowance; it is tracked as tightening work
(SF-016) and is why the policy ships report-only first.

**Report-only first, then enforce.** `ZF_CSP_ENFORCE=0` (the default) sends the policy as
`Content-Security-Policy-Report-Only`: the browser reports what *would* be blocked but blocks
nothing, so a page with a leftover inline handler keeps working while we watch. Once the markup is
clean, `ZF_CSP_ENFORCE=1` sends it as the enforcing `Content-Security-Policy`.
"""

from __future__ import annotations

import secrets
from contextvars import ContextVar

#: The current request's script nonce, so the Jinja global can read it without threading it through
#: every render call. Set on the way in by the app's outermost middleware; empty when unset.
_NONCE: ContextVar[str] = ContextVar("csp_nonce", default="")

NONCE_BYTES = 16

#: Static headers that never depend on the page and cannot break existing markup, so they enforce
#: everywhere. (HSTS is added separately — it is real only where HTTPS is.)
STATIC_HEADERS: dict[str, str] = {
    # Never let the browser second-guess a declared Content-Type (stops a JSON/text reply being
    # sniffed and run as HTML or script).
    "X-Content-Type-Options": "nosniff",
    # No framing at all — the dashboard is never embedded, and this stops clickjacking. `frame-
    # ancestors 'none'` in the CSP says the same to modern browsers; both are cheap.
    "X-Frame-Options": "DENY",
    # A patient id can sit in a URL path; send only the origin to another site, never the path.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Switch off powerful features the app never uses, so a compromised page cannot reach for them.
    "Permissions-Policy": (
        "geolocation=(), microphone=(), camera=(), payment=(), usb=(), "
        "magnetometer=(), gyroscope=(), accelerometer=(), interest-cohort=()"
    ),
}

#: External origins the pages legitimately load, kept in one place so the policy and the docs agree.
#: Self-hosting these (fonts and FullCalendar under /static) would let us drop them — tracked in
#: SF-016.
_FONT_CSS = "https://fonts.googleapis.com"  # Google Fonts stylesheet (a <link rel=stylesheet>)
_FONT_FILES = "https://fonts.gstatic.com"  # the font files that stylesheet pulls in
_CDN = "https://cdn.jsdelivr.net"  # FullCalendar CSS (the JS tag carries the nonce instead)


def new_nonce() -> str:
    """Mint a fresh nonce for this request and publish it to the template layer. Returns the value
    so the caller can pin the very same nonce into the response's CSP header."""
    value = secrets.token_urlsafe(NONCE_BYTES)
    _NONCE.set(value)
    return value


def current_nonce() -> str:
    """The nonce for the request in flight — what the Jinja `csp_nonce()` global returns."""
    return _NONCE.get()


def reset() -> None:
    """Clear the nonce once the request is done, so it never leaks into the next one."""
    _NONCE.set("")


def policy(nonce: str) -> str:
    """The Content-Security-Policy string, with `nonce` pinned into `script-src`."""
    directives = [
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}'",
        # 'unsafe-inline' covers the inline style attributes and <style> blocks the templates still
        # emit; the two font/CDN hosts serve the external stylesheets (SF-016 tracks removing both).
        f"style-src 'self' 'unsafe-inline' {_FONT_CSS} {_CDN}",
        "img-src 'self' data:",
        f"font-src 'self' {_FONT_FILES}",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
    return "; ".join(directives)


def header_name(*, enforce: bool) -> str:
    """`Content-Security-Policy` when enforcing, its `-Report-Only` twin while baking in."""
    return "Content-Security-Policy" if enforce else "Content-Security-Policy-Report-Only"
