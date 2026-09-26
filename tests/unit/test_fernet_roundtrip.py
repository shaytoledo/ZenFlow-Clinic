"""Phase 11.1 (UNIT) — Fernet key derivation and round-trip.

Stored Google tokens are encrypted with a Fernet key derived from key material (`sha256 → 32 bytes →
urlsafe-base64`). The rotation tests cover re-encryption; these pin the primitive: derivation is
deterministic, a value round-trips, and a token encrypted under one material cannot be read with
another. (`test_token_key_rotation` covers the higher-level rotate().)
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from zenflow.token_key import derive_fernet_key, fernet_for


def test_derivation_is_deterministic_and_a_valid_fernet_key() -> None:
    a = derive_fernet_key("some-key-material-123")
    b = derive_fernet_key("some-key-material-123")
    assert a == b, "the same material always derives the same key"
    assert len(a) == 44, "sha256 (32 bytes) as urlsafe-base64 is 44 chars"
    Fernet(a)  # constructing a Fernet from it must not raise


def test_different_material_derives_a_different_key() -> None:
    assert derive_fernet_key("material-A") != derive_fernet_key("material-B")


def test_a_value_round_trips_through_encrypt_decrypt() -> None:
    f = fernet_for("clinic-token-key")
    token = f.encrypt(b'{"access_token": "secret"}')
    assert f.decrypt(token) == b'{"access_token": "secret"}'


def test_a_token_cannot_be_read_with_the_wrong_material() -> None:
    token = fernet_for("the-right-material").encrypt(b"google-oauth-token")
    try:
        fernet_for("the-wrong-material").decrypt(token)
    except InvalidToken:
        pass
    else:  # pragma: no cover - the assert makes the failure explicit
        raise AssertionError("a token must not decrypt under different key material")
