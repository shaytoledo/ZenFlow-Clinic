# Secrets & Crypto (Phase 9.6)

How the clinic's secrets are stored, where they come from, and how the one encrypted-at-rest secret
(Google OAuth tokens) is rotated. ADRs: 0.5 (key separation + redaction), **41** (the provider seam).

## The secrets

| Secret | Purpose | Required |
|---|---|---|
| `SESSION_SECRET` | signs the `zf_session` cookie | always (default refused outside dev) |
| `TOKEN_ENCRYPTION_KEY` | Fernet material for stored Google tokens | outside dev; **must differ** from `SESSION_SECRET` |
| `TELEGRAM_TOKEN` / `THERAPIST_BOT_TOKEN` | the two bot tokens | to run the bots |
| `GOOGLE_CLIENT_SECRET` | Google OAuth | for Calendar/Gmail |
| `WHATSAPP_TOKEN` / `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` | WhatsApp Cloud API | with `ZF_CHANNEL_WHATSAPP=1` |
| `TELEGRAM_WEBHOOK_SECRET` | webhook authentication | with `ZF_WEBHOOK_MODE=1` |
| `ANTHROPIC_API_KEY` | Claude API | with `USE_AI=anthropic` |

All are read in exactly one place — `zenflow.settings` — and **never logged**: `zenflow/logging.py`
redacts secret-shaped values, and a `SecretsProvider` never renders its values (`__repr__` shows the
class only). Never put a secret in a URL, query string or log line.

## Where a secret's value comes from — `SecretsProvider`

`zenflow/secrets.py` decides the *source* of each secret, behind an ABC:

- **`EnvSecrets` (default)** — the process environment (and the `.env` file pydantic loads). This is
  how the clinic runs today; nothing about local or single-server operation changes.
- **`AwsSecretsManagerSecrets` (Phase 12)** — one JSON entry in AWS Secrets Manager, keyed by
  env-var name. boto3 is imported lazily and the bundle is fetched once and cached.

The provider is wired into settings as the **lowest-precedence source** (ADR-41): an explicit
environment variable or `.env` entry always wins, so the default provider is a no-op, and a cloud
provider only fills the secrets the environment does not carry. Because it runs inside pydantic's
source chain, provider-supplied secrets are present *before* the fail-fast validators run.

**Selecting the cloud provider (Phase 12):** set `ZF_CLOUD=1` and `AWS_SECRETS_ID=<name/arn>` (region
from `S3_REGION` / `AWS_REGION`), populate that Secrets Manager entry with a JSON object
(`{"SESSION_SECRET": "...", "TOKEN_ENCRYPTION_KEY": "...", ...}`), and drop those secrets from the
environment. No code changes.

## Rotating the token-encryption key

Stored Google OAuth tokens are the one secret encrypted at rest (Fernet, key derived from
`TOKEN_ENCRYPTION_KEY`; `zenflow/token_key.py`). To rotate the key, re-encrypt every row:

```bash
# 1. Preview — reports how many rows would rotate; writes nothing
python -m zenflow.rotate_token_key --dry-run

# 2. Apply — backs up the DB first, then re-encrypts every google_tokens row
python -m zenflow.rotate_token_key

# Rotating away from a *previous* TOKEN_ENCRYPTION_KEY (not the SESSION_SECRET default):
python -m zenflow.rotate_token_key --old-material '<previous key material>'
```

- The rotation is **idempotent**: rows already encrypted with the new key are left alone.
- A row that decrypts with **neither** key is reported and never modified; the command exits `1` so a
  failed rotation is visible.
- The pre-Phase-0.4 default derived the key from `SESSION_SECRET`; that is the default `--old-material`,
  so a first rotation to a dedicated `TOKEN_ENCRYPTION_KEY` needs no extra flag.

**To change `SESSION_SECRET`** (cookie signing): there is no re-encryption to do — rotating it simply
signs new cookies and invalidates existing sessions (everyone signs in again). It must stay distinct
from `TOKEN_ENCRYPTION_KEY` (settings refuses otherwise outside dev).
