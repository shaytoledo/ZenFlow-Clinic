"""Phase 0.4 — zenflow.settings: the ONE place env vars are read.

Covers: defaults, fail-fast on a default/short SESSION_SECRET outside dev, TOKEN_ENCRYPTION_KEY
separation, the HTTPS-only rule (ADR-14), and both values of every feature flag.
"""

from __future__ import annotations

import pytest

from zenflow import settings as S

GOOD_SECRET = "s" * 48
GOOD_KEY = "k" * 48

PROD_BASE = {
    "ENV": "prod",
    "SESSION_SECRET": GOOD_SECRET,
    "TOKEN_ENCRYPTION_KEY": GOOD_KEY,
    "OLLAMA_HOST": "https://ollama.internal.example",
    "REDIS_URL": "rediss://:redis-secret@redis.internal.example:6380/0",
    "GOOGLE_REDIRECT_URI": "https://clinic.example/auth/callback",
    "GOOGLE_REG_REDIRECT_URI": "https://clinic.example/register/google/callback",
    "GOOGLE_GMAIL_REDIRECT_URI": "https://clinic.example/auth/gmail/callback",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch):
    """Apply a dict of env vars, drop every ZF_* var, and rebuild settings from scratch."""

    def _apply(values: dict[str, str]) -> S.Settings:
        for key in list(S.ALL_ENV_VARS):
            monkeypatch.delenv(key, raising=False)
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        S.reset_settings()
        return S.get_settings()

    yield _apply
    S.reset_settings()


# ── defaults ─────────────────────────────────────────────────────────────────────────────────
def test_defaults_are_dev_and_local(env) -> None:
    s = env({})
    assert s.env == "dev"
    assert s.is_dev is True
    assert s.ollama_host == "http://localhost:11434"
    assert s.redis_url == "redis://localhost:6379/0"
    assert s.google_redirect_uri.startswith("http://localhost:8080/")  # dev server port
    assert s.session_secret == S.DEFAULT_SESSION_SECRET
    assert s.token_encryption_key is None
    assert s.flags.queue_backend == "inprocess"
    assert s.flags.ai_provider == "ollama"


def test_every_documented_variable_is_a_known_setting() -> None:
    documented = {
        "ENV", "ZENFLOW_DB_PATH", "TELEGRAM_TOKEN", "THERAPIST_BOT_TOKEN", "MESSAGING_CHANNEL",
        "USE_AI", "OLLAMA_HOST", "OLLAMA_MODEL", "ANTHROPIC_API_KEY", "REDIS_URL",
        "SESSION_SECRET", "TOKEN_ENCRYPTION_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI", "GOOGLE_REG_REDIRECT_URI", "GOOGLE_GMAIL_REDIRECT_URI",
    }  # fmt: skip
    assert documented <= set(S.ALL_ENV_VARS)
    assert {f"ZF_{name}" for name in S.FLAG_NAMES} <= set(S.ALL_ENV_VARS)


# ── fail fast (F7) ────────────────────────────────────────────────────────────────────────────
def test_prod_with_default_session_secret_refuses_to_boot(env) -> None:
    with pytest.raises(S.SettingsError, match="SESSION_SECRET"):
        env({**PROD_BASE, "SESSION_SECRET": S.DEFAULT_SESSION_SECRET})


def test_prod_with_short_session_secret_refuses_to_boot(env) -> None:
    with pytest.raises(S.SettingsError, match="SESSION_SECRET"):
        env({**PROD_BASE, "SESSION_SECRET": "short"})


def test_prod_requires_a_separate_token_encryption_key(env) -> None:
    with pytest.raises(S.SettingsError, match="TOKEN_ENCRYPTION_KEY"):
        env({k: v for k, v in PROD_BASE.items() if k != "TOKEN_ENCRYPTION_KEY"})
    with pytest.raises(S.SettingsError, match="TOKEN_ENCRYPTION_KEY"):
        env({**PROD_BASE, "TOKEN_ENCRYPTION_KEY": GOOD_SECRET})  # same as SESSION_SECRET


def test_dev_tolerates_default_secret_and_missing_key(env) -> None:
    s = env({"ENV": "dev"})
    assert s.session_secret == S.DEFAULT_SESSION_SECRET
    assert s.token_key_material == S.DEFAULT_SESSION_SECRET  # legacy derivation


def test_token_key_material_prefers_the_dedicated_key(env) -> None:
    s = env({"ENV": "dev", "SESSION_SECRET": GOOD_SECRET, "TOKEN_ENCRYPTION_KEY": GOOD_KEY})
    assert s.token_key_material == GOOD_KEY


def test_prod_happy_path_boots(env) -> None:
    s = env(PROD_BASE)
    assert s.env == "prod"
    assert s.is_dev is False


# ── HTTPS-only rule (ADR-14) ──────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("var", "bad"),
    [
        ("OLLAMA_HOST", "http://ollama.internal.example:11434"),
        ("REDIS_URL", "redis://redis.internal.example:6379/0"),
        ("GOOGLE_REDIRECT_URI", "http://clinic.example/auth/callback"),
        ("GOOGLE_REG_REDIRECT_URI", "http://clinic.example/register/google/callback"),
        ("GOOGLE_GMAIL_REDIRECT_URI", "http://clinic.example/auth/gmail/callback"),
    ],
)
def test_prod_rejects_plain_http_to_non_local_hosts(env, var: str, bad: str) -> None:
    with pytest.raises(S.SettingsError, match=var):
        env({**PROD_BASE, var: bad})


@pytest.mark.parametrize(
    ("var", "ok"),
    [
        ("OLLAMA_HOST", "http://localhost:11434"),
        ("OLLAMA_HOST", "http://127.0.0.1:11434"),
        ("REDIS_URL", "redis://localhost:6379/0"),
        ("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/callback"),
    ],
)
def test_prod_allows_plain_http_only_for_localhost(env, var: str, ok: str) -> None:
    env({**PROD_BASE, var: ok})


def test_dev_allows_plain_http_anywhere(env) -> None:
    env({"ENV": "dev", "OLLAMA_HOST": "http://ollama.lan:11434"})


# ── feature flags: BOTH paths of EVERY flag ──────────────────────────────────────────────────
BOOL_FLAGS = [
    "CLOUD",
    "STORAGE_S3",
    "CHANNEL_WHATSAPP",
    "WEBHOOK_MODE",
    "SSE_UPDATES",
    "POINT_IMAGES",
    "CSP_ENFORCE",
]


@pytest.mark.parametrize("flag", BOOL_FLAGS)
@pytest.mark.parametrize("value", ["1", "0"])
def test_boolean_flags_parse_both_values(env, flag: str, value: str) -> None:
    extra = {"S3_BUCKET": "media-bucket"} if flag == "STORAGE_S3" else {}
    s = env({f"ZF_{flag}": value, **extra})
    assert getattr(s.flags, flag.lower()) is (value == "1")


def test_s3_storage_needs_a_bucket(env) -> None:
    """Phase 4.3c: a store with nowhere to write must stop the boot, in every environment."""
    with pytest.raises(S.SettingsError, match="S3_BUCKET"):
        env({"ZF_STORAGE_S3": "1"})
    assert env({"ZF_STORAGE_S3": "0"}).s3_bucket == ""


@pytest.mark.parametrize("value", ["inprocess", "celery", "temporal", "aws"])
def test_queue_backend_accepts_every_documented_backend(env, value: str) -> None:
    assert env({"ZF_QUEUE_BACKEND": value}).flags.queue_backend == value


def test_queue_backend_rejects_unknown_values(env) -> None:
    with pytest.raises(S.SettingsError, match="ZF_QUEUE_BACKEND"):
        env({"ZF_QUEUE_BACKEND": "rabbit"})


@pytest.mark.parametrize("value", ["ollama", "anthropic"])
def test_ai_provider_flag_both_values(env, value: str) -> None:
    assert env({"ZF_AI_PROVIDER": value}).ai_provider == value


def test_ai_provider_falls_back_to_legacy_use_ai(env) -> None:
    assert env({"USE_AI": "anthropic"}).ai_provider == "anthropic"
    assert env({"USE_AI": "anthropic", "ZF_AI_PROVIDER": "ollama"}).ai_provider == "ollama"


def test_login_max_attempts_parses_both_off_and_on(env) -> None:
    assert env({"ZF_LOGIN_MAX_ATTEMPTS": "0"}).flags.login_max_attempts == 0
    assert env({"ZF_LOGIN_MAX_ATTEMPTS": "7"}).flags.login_max_attempts == 7


def test_ai_and_signup_rate_flags_parse(env) -> None:
    assert env({"ZF_AI_RATE_PER_MINUTE": "0"}).flags.ai_rate_per_minute == 0
    assert env({"ZF_AI_RATE_PER_MINUTE": "30"}).flags.ai_rate_per_minute == 30
    assert env({"ZF_SIGNUP_PER_MINUTE": "0"}).flags.signup_per_minute == 0
    assert env({"ZF_SIGNUP_PER_MINUTE": "4"}).flags.signup_per_minute == 4


def test_bot_flood_flag_parses(env) -> None:
    assert env({"ZF_BOT_FLOOD_PER_MINUTE": "0"}).flags.bot_flood_per_minute == 0
    assert env({"ZF_BOT_FLOOD_PER_MINUTE": "15"}).flags.bot_flood_per_minute == 15


def test_flags_snapshot_has_exactly_the_documented_flags(env) -> None:
    snap = env({}).flags.snapshot()
    assert set(snap) == set(S.FLAG_NAMES)
    assert not any(
        "secret" in k.lower() or "token" in k.lower() for k in snap
    ), "flag snapshot must never carry secrets"


def test_get_settings_is_cached_until_reset(env) -> None:
    a = env({})
    assert S.get_settings() is a
    S.reset_settings()
    assert S.get_settings() is not a
