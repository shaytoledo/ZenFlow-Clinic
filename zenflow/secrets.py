"""
zenflow.secrets
────────────────
Where secret values come from (Phase 9.6).

A `SecretsProvider` is the seam under `zenflow.settings`: it decides where a secret's value is read
from. `EnvSecrets` (the default) reads the process environment — exactly what pydantic-settings
already does — and `AwsSecretsManagerSecrets` (Phase 12) reads a single JSON bundle from AWS Secrets
Manager, keyed by env-var name.

The provider is wired into `Settings` as the **lowest-precedence** source, so an explicit environment
variable (or `.env` entry) always wins. That makes the default a no-op — nothing changes today — while
in the cloud the provider fills the secrets the environment does not carry, *before* settings'
fail-fast validation runs (which is why the seam lives inside the source chain, not as a post-load
overlay). See ADR-41 and `docs/SECRETS.md`.

A provider never renders its secret values: `__repr__` shows only the class and non-secret config, so
a stray log line or traceback cannot leak them (Phase 0.5 redaction covers the values themselves).

Selection reads `ZF_CLOUD` / `AWS_SECRETS_ID` straight from the environment rather than through
`get_settings()`, because the provider is consulted *while* settings are being built — going back
through settings would recurse.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

#: The `Settings` env-var names that hold secrets. The provider may supply any of these; the
#: environment (and `.env`) override it. Field names are the lower-cased form.
SECRET_NAMES: tuple[str, ...] = (
    "SESSION_SECRET",
    "TOKEN_ENCRYPTION_KEY",
    "TELEGRAM_TOKEN",
    "THERAPIST_BOT_TOKEN",
    "WHATSAPP_TOKEN",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "ANTHROPIC_API_KEY",
    "GOOGLE_CLIENT_SECRET",
)

_TRUE = {"1", "true", "yes", "on"}


class SecretsProvider(ABC):
    """A source of secret values, looked up by their environment-variable name."""

    @abstractmethod
    def get(self, name: str) -> str | None:
        """The secret's value, or None when this provider does not carry it."""

    def __repr__(self) -> str:  # never render the values
        return f"{type(self).__name__}()"


class EnvSecrets(SecretsProvider):
    """The default: secrets come from the process environment (and thus the `.env` pydantic loads)."""

    def get(self, name: str) -> str | None:
        value = os.environ.get(name)
        return value or None


class AwsSecretsManagerSecrets(SecretsProvider):
    """Phase 12: secrets from one AWS Secrets Manager entry — a JSON object keyed by env-var name.

    boto3 is imported lazily and the bundle is fetched once and cached, so importing this module
    costs nothing and needs no AWS access until a secret is actually requested.
    """

    def __init__(self, secret_id: str, region: str | None = None) -> None:
        self._secret_id = secret_id
        self._region = region or None
        self._bundle: dict[str, str] | None = None

    def _load(self) -> dict[str, str]:
        if self._bundle is None:
            try:
                import boto3
            except ImportError as exc:  # pragma: no cover - boto3 is a dependency today
                raise RuntimeError("AwsSecretsManagerSecrets requires boto3") from exc
            client = boto3.client("secretsmanager", region_name=self._region)
            raw = client.get_secret_value(SecretId=self._secret_id)["SecretString"]
            parsed = json.loads(raw)
            self._bundle = {str(k): str(v) for k, v in parsed.items()}
        return self._bundle

    def get(self, name: str) -> str | None:
        value = self._load().get(name)
        return value or None

    def __repr__(self) -> str:  # the id is config, not a secret; the values never appear
        return f"AwsSecretsManagerSecrets(secret_id={self._secret_id!r})"


_provider: SecretsProvider | None = None


def build_provider() -> SecretsProvider:
    """Choose a provider from the environment: `EnvSecrets`, unless `ZF_CLOUD` is on *and*
    `AWS_SECRETS_ID` names a Secrets Manager entry (Phase 12)."""
    cloud = os.environ.get("ZF_CLOUD", "").strip().lower() in _TRUE
    secret_id = os.environ.get("AWS_SECRETS_ID", "").strip()
    if cloud and secret_id:
        region = os.environ.get("S3_REGION") or os.environ.get("AWS_REGION") or None
        logger.info("secrets: using AWS Secrets Manager (%s)", secret_id)
        return AwsSecretsManagerSecrets(secret_id, region)
    return EnvSecrets()


def get_secrets_provider() -> SecretsProvider:
    """The process-wide provider, built once on first use."""
    global _provider
    if _provider is None:
        _provider = build_provider()
    return _provider


def set_secrets_provider(provider: SecretsProvider | None) -> None:
    """Override the cached provider (tests), or reset it with None so it is rebuilt on next use."""
    global _provider
    _provider = provider


def reset_secrets_provider() -> None:
    set_secrets_provider(None)
