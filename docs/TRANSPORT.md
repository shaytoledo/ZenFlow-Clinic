# Data in Transit (Phase 9.11, ADR-14)

Everything that crosses the Internet or a machine boundary is TLS, and nothing is allowed to turn
verification off. This complements encryption/handling at rest (`docs/SECRETS.md`, `docs/DATA_LAYER.md`).

## What is enforced in code

| Boundary | Rule | Enforced by |
|---|---|---|
| Dashboard, OAuth callbacks, webhooks (non-local) | `https://` only | `zenflow.settings` URL validator (ADR-14) — a plain `http://` to a non-local host refuses to boot outside dev |
| Redis (non-local) | `rediss://` (TLS) only | same validator — `redis://` to a non-local host refuses to boot; Redis holds relay + LLM history (clinical data) |
| Ollama when remote | `https://` only | same validator |
| Google Calendar/Gmail, Telegram Bot API, Anthropic | `https://` endpoints, **certificate verification ON** | the channel adapters / SDKs use https; a test forbids any `verify=False` / `CERT_NONE` / `check_hostname=False` in the source |
| HSTS (non-dev) | `max-age=31536000; includeSubDomains` | `web/csp.py` (Phase 9.4) |
| Local dev | plain `http://localhost` allowed | the validator exempts `localhost` / `127.0.0.1` / `::1` so local work is not forced onto TLS |

**Never** place message content, tokens or identifiers in URLs, query strings or logs. Message
metadata is logged (`message_log`) but never the message text; secrets are redacted
(`zenflow/logging.py`). Guarded by `tests/security/test_transit.py` and the settings tests.

## Telegram Bot API — not end-to-end encrypted (owner decision Q5)

Patient⇄therapist relay messages travel over **Telegram's TLS to Telegram's servers**, where
Telegram can read them — the Bot API is **not** end-to-end encrypted. For a clinic handling health
information this is a consent/records matter: patients should be told their chat with the therapist
goes through Telegram. The exact consent wording and whether a more private channel is required is
**owner decision Q5** (alongside the retention/erasure posture).

## Phase 12 (cloud infrastructure — not code in this repo)

- **HTTP→HTTPS redirect** and **TLS 1.2+ termination** at the load balancer / reverse proxy.
- **Certificates** from ACM (AWS) or Let's Encrypt with auto-renewal.
- **Private network** for bot⇄web⇄worker traffic (the shared SQLite file / future RDS, Redis, the
  internal booking API), so the machine-boundary hops are not exposed even before TLS.
- **RDS encryption at rest** + **encrypted backups** before they leave the host (see 9.9 / `docs/SECRETS.md`).

## Tests

- `tests/security/test_transit.py` — no source disables TLS verification; no plain `http://` to a
  non-local host; prod refuses `redis://` (non-local) and boots on all-TLS.
- `tests/unit/test_settings.py` — the ADR-14 URL validator (https/rediss for non-local, localhost
  exempt).
- `tests/security/test_csp.py` / `test_session_policy.py` — the HSTS header (non-dev only).
