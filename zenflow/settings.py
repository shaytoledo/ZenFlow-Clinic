"""zenflow.settings — the ONE place environment variables are read (Phase 0.4, ADR-16).

Usage::

    from zenflow.settings import get_settings
    s = get_settings()
    s.telegram_token, s.flags.sse_updates, s.ai_provider, ...

Rules enforced at construction (so the process refuses to boot on a bad config):
- ENV != dev/test  ⇒ SESSION_SECRET must be set, non-default and ≥ 32 chars (F7)
- ENV != dev/test  ⇒ TOKEN_ENCRYPTION_KEY must be set, ≥ 32 chars and ≠ SESSION_SECRET (F7)
- ENV != dev/test  ⇒ every non-localhost URL must be https:// (rediss:// for Redis) (ADR-14)
- ZF_* feature flags are typed; unknown values are rejected

`bot/db.py` reads ZENFLOW_DB_PATH directly (it must work before this module can be imported
by the test harness) and `startup/launch.py` parses .env by hand (it runs before dependencies
are installed). Those are the only sanctioned exceptions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

DEFAULT_SESSION_SECRET = "changeme-set-in-dotenv"  # noqa: S105 — sentinel, refused outside dev
MIN_SECRET_LEN = 32
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

Env = Literal["dev", "test", "staging", "prod"]
QueueBackend = Literal["inprocess", "celery", "temporal", "aws"]
AIProvider = Literal["ollama", "anthropic"]

FLAG_NAMES: tuple[str, ...] = (
    "CLOUD",
    "STORAGE_S3",
    "QUEUE_BACKEND",
    "CHANNEL_WHATSAPP",
    "AI_PROVIDER",
    "WEBHOOK_MODE",
    "SSE_UPDATES",
    "POINT_IMAGES",
    "CONV_TIMEOUT_MINUTES",
    "AUTO_FOLLOWUP",
    "API_RATE_PER_MINUTE",
    "AI_DEBUG_PROMPTS",
    "METRICS_PROMETHEUS",
    "TRACING",
)


class SettingsError(RuntimeError):
    """Raised when the environment is invalid. The message names every offending variable."""


class FeatureFlags(BaseSettings):
    """Typed feature flags. Every flag must have BOTH paths exercised in tests (plan 0.4)."""

    model_config = SettingsConfigDict(
        env_prefix="ZF_", env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    cloud: bool = False  # ZF_CLOUD — running on AWS (Phase 12)
    storage_s3: bool = False  # ZF_STORAGE_S3 — Storage ABC → S3 (Phase 4.3 / 12)
    queue_backend: QueueBackend = "inprocess"  # ZF_QUEUE_BACKEND — TaskQueue impl (Phase 1.2)
    channel_whatsapp: bool = False  # ZF_CHANNEL_WHATSAPP — WhatsApp adapter (Phase 7.4)
    ai_provider: AIProvider | None = None  # ZF_AI_PROVIDER — None ⇒ legacy USE_AI
    webhook_mode: bool = False  # ZF_WEBHOOK_MODE — bots via webhooks, not polling (12.2.5)
    sse_updates: bool = False  # ZF_SSE_UPDATES — server-sent events instead of polling (3.4)
    point_images: bool = False  # ZF_POINT_IMAGES — acupoint image store (4.3)
    # ZF_CONV_TIMEOUT_MINUTES — how long a patient flow may sit idle; 0 = never expire (2.2d)
    conv_timeout_minutes: int = 30
    # ZF_AUTO_FOLLOWUP — also check in on sessions never marked complete (owner decision Q7, 6.1)
    auto_followup: bool = False
    # ZF_API_RATE_PER_MINUTE — booking API requests per minute per caller; 0 = no limit (7.3)
    api_rate_per_minute: int = 60
    # ZF_AI_DEBUG_PROMPTS — keep the clinical prompt in `ai_calls` in the clear; dev only (8.2)
    ai_debug_prompts: bool = False
    # ZF_METRICS_PROMETHEUS — serve /api/admin/metrics in Prometheus' text format too (8.4)
    metrics_prometheus: bool = False
    # ZF_TRACING — OpenTelemetry tracing; needs the packages installed (8.4, Phase 12)
    tracing: bool = False

    @field_validator("ai_provider", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # `.env` templates ship `ZF_AI_PROVIDER=` (empty) meaning "use USE_AI".
        return None if isinstance(value, str) and not value.strip() else value

    def snapshot(self) -> dict[str, bool | str | int]:
        """Flag state for /api/admin/flags — never contains secrets."""
        return {name: getattr(self, name.lower()) for name in FLAG_NAMES}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # ── runtime ──
    env: Env = "dev"
    zenflow_db_path: str | None = None  # mirrored for documentation; bot/db.py reads it itself
    log_format: Literal["auto", "console", "json"] = "auto"  # auto = console in dev, json otherwise
    log_level: str = "INFO"
    clinic_tz: str = "Asia/Jerusalem"  # IANA zone; the clinic's wall clock for "today" (ADR-19)

    # ── telegram ──
    telegram_token: str = ""
    therapist_bot_token: str = ""
    messaging_channel: str = "telegram"

    # ── whatsapp (Phase 7.4, only with ZF_CHANNEL_WHATSAPP=1) ──
    whatsapp_phone_number_id: str = ""  # WHATSAPP_PHONE_NUMBER_ID — the clinic's sender
    whatsapp_token: str = ""  # WHATSAPP_TOKEN — system-user access token
    whatsapp_app_secret: str = ""  # WHATSAPP_APP_SECRET — signs the webhook; empty ⇒ all refused
    whatsapp_verify_token: str = ""  # WHATSAPP_VERIFY_TOKEN — Meta's subscription handshake
    whatsapp_api_version: str = "v23.0"  # WHATSAPP_API_VERSION — Graph API version
    # Approved template names (Meta console). Empty ⇒ nothing is sent outside the 24 h window.
    whatsapp_template_followup: str = ""  # WHATSAPP_TEMPLATE_FOLLOWUP — the 24h check-in
    whatsapp_template_confirmation: str = ""  # WHATSAPP_TEMPLATE_CONFIRMATION — a booking
    # TELEGRAM_WEBHOOK_SECRET — the secret_token Telegram echoes on webhook calls (7.1); empty ⇒
    # every webhook is refused. Polling (today's mode) does not use it.
    telegram_webhook_secret: str = ""

    # ── ai ──
    use_ai: AIProvider = "ollama"  # legacy name; ZF_AI_PROVIDER wins when set
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "gemma3:latest"
    anthropic_api_key: str = ""

    # ── redis ──
    redis_url: str = "redis://localhost:6379/0"

    # ── media (Phase 4.3b/c) ──
    media_root: str = ""  # MEDIA_ROOT — LocalStorage directory; empty ⇒ data/media
    # S3 store, used when ZF_STORAGE_S3=1. Credentials come from the standard AWS chain
    # (environment, profile or instance role) — boto3 reads them itself.
    s3_bucket: str = ""  # S3_BUCKET — required with ZF_STORAGE_S3
    s3_prefix: str = "media/"  # S3_PREFIX — prepended to every key in the bucket
    s3_region: str = ""  # S3_REGION — empty ⇒ the AWS default region
    s3_kms_key_id: str = ""  # S3_KMS_KEY_ID — empty ⇒ the bucket's AWS-managed key (still SSE-KMS)
    s3_endpoint_url: str = ""  # S3_ENDPOINT_URL — an S3-compatible endpoint (MinIO); empty ⇒ AWS

    # ── web / secrets ──
    session_secret: str = DEFAULT_SESSION_SECRET
    token_encryption_key: str | None = None

    # ── google oauth ──
    google_client_id: str = ""
    google_client_secret: str = ""
    # Defaults match the dev server port (startup/run_web.py listens on 8080).
    google_redirect_uri: str = "http://localhost:8080/auth/callback"
    google_reg_redirect_uri: str = "http://localhost:8080/register/google/callback"
    google_gmail_redirect_uri: str = "http://localhost:8080/auth/gmail/callback"

    # ── feature flags (ZF_*) ──
    flags: FeatureFlags = Field(default_factory=FeatureFlags)

    # ── derived ──
    @property
    def is_dev(self) -> bool:
        return self.env in ("dev", "test")

    @property
    def ai_provider(self) -> AIProvider:
        return self.flags.ai_provider or self.use_ai

    @property
    def token_key_material(self) -> str:
        """Material for the Fernet key protecting google_tokens.

        Prefers TOKEN_ENCRYPTION_KEY; falls back to SESSION_SECRET for backwards compatibility
        with rows encrypted before Phase 0.4 (rotate with `python -m zenflow.rotate_token_key`).
        """
        return self.token_encryption_key or self.session_secret

    # ── validation ──
    @field_validator("clinic_tz")
    @classmethod
    def _valid_zone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"CLINIC_TZ={value!r} is not a known IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.flags.ai_provider is None:
            self.flags.ai_provider = self.use_ai
        if self.flags.storage_s3 and not self.s3_bucket.strip():
            raise ValueError("S3_BUCKET is required when ZF_STORAGE_S3=1")
        if self.is_dev:
            return self
        problems: list[str] = []
        if not self.session_secret or self.session_secret == DEFAULT_SESSION_SECRET:
            problems.append("SESSION_SECRET is missing or still the default value")
        elif len(self.session_secret) < MIN_SECRET_LEN:
            problems.append(f"SESSION_SECRET must be at least {MIN_SECRET_LEN} characters")
        if not self.token_encryption_key:
            problems.append("TOKEN_ENCRYPTION_KEY is required outside dev (F7)")
        elif len(self.token_encryption_key) < MIN_SECRET_LEN:
            problems.append(f"TOKEN_ENCRYPTION_KEY must be at least {MIN_SECRET_LEN} characters")
        elif self.token_encryption_key == self.session_secret:
            problems.append("TOKEN_ENCRYPTION_KEY must differ from SESSION_SECRET (F7)")
        problems.extend(self._url_problems())
        if problems:
            raise ValueError(f"invalid configuration for ENV={self.env}: " + "; ".join(problems))
        return self

    def _url_problems(self) -> list[str]:
        checks: list[tuple[str, str, frozenset[str]]] = [
            ("OLLAMA_HOST", self.ollama_host, frozenset({"https"})),
            ("REDIS_URL", self.redis_url, frozenset({"rediss"})),
            ("GOOGLE_REDIRECT_URI", self.google_redirect_uri, frozenset({"https"})),
            ("GOOGLE_REG_REDIRECT_URI", self.google_reg_redirect_uri, frozenset({"https"})),
            ("GOOGLE_GMAIL_REDIRECT_URI", self.google_gmail_redirect_uri, frozenset({"https"})),
        ]
        if self.s3_endpoint_url:
            checks.append(("S3_ENDPOINT_URL", self.s3_endpoint_url, frozenset({"https"})))
        out: list[str] = []
        for name, value, schemes in checks:
            parts = urlsplit(value)
            if (parts.hostname or "") in LOCAL_HOSTS:
                continue
            if parts.scheme not in schemes:
                out.append(
                    f"{name}={value!r} must use {'/'.join(sorted(schemes))}:// "
                    "for non-localhost hosts (ADR-14)"
                )
        return out


ALL_ENV_VARS: tuple[str, ...] = tuple(
    [name.upper() for name in Settings.model_fields if name != "flags"]
    + [f"ZF_{name}" for name in FLAG_NAMES]
)

_settings: Settings | None = None


def _env_file() -> Path | None:
    """Which .env to read: ZENFLOW_DOTENV=0 disables it (tests), a path overrides ROOT/.env."""
    import os

    raw = os.environ.get("ZENFLOW_DOTENV")
    if raw is None:
        return ENV_FILE
    if raw.strip().lower() in ("", "0", "false", "no", "off"):
        return None
    return Path(raw)


def get_settings() -> Settings:
    """Process-wide settings, built once. Raises SettingsError on an invalid environment."""
    global _settings
    if _settings is None:
        try:
            env_file = _env_file()
            # pydantic-settings accepts _env_file at init; its stubs do not declare it.
            flags = FeatureFlags(_env_file=env_file)  # type: ignore[call-arg]
            _settings = Settings(_env_file=env_file, flags=flags)  # type: ignore[call-arg]
        except ValidationError as exc:
            # Name the offending ENV VAR, not the pydantic field: ZF_QUEUE_BACKEND, not queue_backend.
            prefix = "ZF_" if exc.title == "FeatureFlags" else ""
            parts = []
            for err in exc.errors():
                loc = ".".join(str(x) for x in err.get("loc", ()))
                msg = str(err.get("msg", err))
                parts.append(f"{prefix}{loc.upper()}: {msg}" if loc else msg)
            raise SettingsError("; ".join(parts)) from exc
    return _settings


def reset_settings() -> None:
    """Forget the cached settings (tests; config reload)."""
    global _settings
    _settings = None
