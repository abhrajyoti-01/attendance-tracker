"""Password/token security helper tests."""

from datetime import timedelta

import pytest

from src.utils.security import (
    create_access_token,
    decode_token,
    get_password_hash,
    hash_api_key,
    hash_reset_token,
    validate_password_strength,
    verify_password,
)


def test_bcrypt_roundtrip():
    hashed = get_password_hash("Correct-Horse9!")
    assert verify_password("Correct-Horse9!", hashed)
    assert not verify_password("wrong-password", hashed)


def test_password_over_72_bytes_rejected():
    """bcrypt silently truncates past 72 bytes; that must be an explicit error."""
    with pytest.raises(ValueError):
        get_password_hash("a" * 73)


def test_weak_password_rejected():
    ok, message = validate_password_strength("short")
    assert ok is False
    assert message


def test_strong_password_accepted():
    ok, _ = validate_password_strength("Str0ng-Passw0rd!")
    assert ok is True


def test_access_token_carries_expected_claims():
    token = create_access_token({"sub": "user-1", "org_id": "org-1", "tv": 3})
    payload = decode_token(token)
    assert payload is not None
    assert payload["type"] == "access"
    assert payload["sub"] == "user-1"
    assert payload["tv"] == 3
    assert "jti" in payload


def test_expired_token_is_rejected():
    token = create_access_token({"sub": "user-1"}, expires_delta=timedelta(seconds=-10))
    assert decode_token(token) is None


def test_tampered_token_is_rejected():
    token = create_access_token({"sub": "user-1"})
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    assert decode_token(tampered) is None


def test_api_key_and_reset_token_hashes_are_domain_separated():
    raw = "same-input-value"
    assert hash_api_key(raw) != hash_reset_token(raw)


def test_hashing_is_deterministic():
    assert hash_api_key("key-abc") == hash_api_key("key-abc")
    assert hash_reset_token("tok-abc") == hash_reset_token("tok-abc")
