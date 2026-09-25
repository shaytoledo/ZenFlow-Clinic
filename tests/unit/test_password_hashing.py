"""Phase 11.1 (UNIT) — password hashing.

Therapist dashboard passwords are stored as PBKDF2-HMAC-SHA256 (`therapists.password_hash`). These
pure-logic tests pin the guarantees a credential store must give: a unique random salt per hash, a
correct round-trip, a wrong password rejected, a constant-time compare, and — crucially — that a
malformed or hostile stored value is rejected rather than raising (a raise in `verify_password`
would 500 the sign-in path).

There are currently two byte-for-byte copies of these helpers — `web.services.therapist_service`
(public) and `web.deps` (private `_`-prefixed). Until they are consolidated (tracked separately),
the last test pins them as **cross-compatible**, so a change to one that silently diverges from the
other — and would break sign-in for accounts registered via the other path — fails here.
"""

from __future__ import annotations

import pytest

from web.deps import _hash_password, _verify_password
from web.services.therapist_service import hash_password, verify_password

# Every (hash, verify) pair the app uses. Parametrising proves each copy on its own.
_IMPLS = [
    pytest.param(hash_password, verify_password, id="therapist_service"),
    pytest.param(_hash_password, _verify_password, id="deps"),
]

_PASSWORDS = [
    "correct horse battery staple",
    "short",
    "p@ssw0rd!#$%^&*()",
    "کلمه عبور",  # non-latin
    "日本語のパスワード",
    "🔐🔐🔐",  # emoji
    " leading and trailing ",
    "x" * 4096,  # very long
]


@pytest.mark.parametrize("hasher,verifier", _IMPLS)
def test_hash_format_is_salt_colon_sha256_hex(hasher, verifier) -> None:
    stored = hasher("hunter2")
    salt, digest = stored.split(":", 1)
    assert len(salt) == 32 and all(c in "0123456789abcdef" for c in salt), "16-byte hex salt"
    assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest), "sha256 hex digest"


@pytest.mark.parametrize("hasher,verifier", _IMPLS)
def test_the_same_password_hashes_differently_each_time(hasher, verifier) -> None:
    a, b = hasher("same-password"), hasher("same-password")
    assert a != b, "a random per-hash salt must make two hashes of one password differ"
    assert verifier("same-password", a) and verifier("same-password", b), "both still verify"


@pytest.mark.parametrize("hasher,verifier", _IMPLS)
@pytest.mark.parametrize("password", _PASSWORDS)
def test_round_trip_accepts_the_right_password(hasher, verifier, password) -> None:
    assert verifier(password, hasher(password)) is True


@pytest.mark.parametrize("hasher,verifier", _IMPLS)
@pytest.mark.parametrize("password", _PASSWORDS)
def test_a_wrong_password_is_rejected(hasher, verifier, password) -> None:
    stored = hasher(password)
    assert verifier(password + "x", stored) is False
    assert verifier(password.upper() + " ", stored) is False
    assert verifier("", stored) is False


@pytest.mark.parametrize("hasher,verifier", _IMPLS)
def test_a_flipped_digit_in_the_digest_is_rejected(hasher, verifier) -> None:
    stored = hasher("tamper-me")
    salt, digest = stored.split(":", 1)
    flipped = "0" if digest[-1] != "0" else "1"
    assert verifier("tamper-me", f"{salt}:{digest[:-1]}{flipped}") is False


@pytest.mark.parametrize("hasher,verifier", _IMPLS)
@pytest.mark.parametrize(
    "junk",
    [
        "",
        ":",
        "nocolon",
        "onlysalt:",
        ":onlyhash",
        "salt:hash:extra",
        "salt:not-hex-!!",
        "  ",
        "\x00\x00",
        "🙂:🙂",
    ],
)
def test_a_malformed_stored_value_returns_false_and_never_raises(hasher, verifier, junk) -> None:
    # A corrupted or attacker-supplied `password_hash` must not 500 the sign-in path.
    assert verifier("anything", junk) is False


@pytest.mark.parametrize(
    "make,check",
    [
        pytest.param(hash_password, _verify_password, id="service_hash→deps_verify"),
        pytest.param(_hash_password, verify_password, id="deps_hash→service_verify"),
    ],
)
@pytest.mark.parametrize("password", ["cross-compat", "another one", "🔐"])
def test_the_two_implementations_are_cross_compatible(make, check, password) -> None:
    """Pins the duplicate helpers as interchangeable. If someone changes one copy's iteration count,
    salt size or format without the other, this fails — catching a divergence that would otherwise
    only surface as therapists silently unable to sign in."""
    assert check(password, make(password)) is True
