import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import structlog
from jose import JWTError, jwt

from src.config import settings

logger = structlog.get_logger(__name__)

# bcrypt silently truncates input at 72 bytes - reject earlier instead.
BCRYPT_MAX_BYTES = 72
BCRYPT_ROUNDS = 12

API_KEY_PREFIX = "ak"
API_KEY_BYTES = 32


def _utcnow() -> datetime:
    return datetime.now(UTC)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str) -> str:
    if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
        raise ValueError(f"Password must be at most {BCRYPT_MAX_BYTES} bytes (bcrypt limit)")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode(
        "utf-8"
    )


def validate_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"

    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in "!@#$%^&*()-_=+[]{}|;:,.<>?" for c in password)

    checks = [has_upper, has_lower, has_digit, has_special]
    passed = sum(checks)

    if passed < 3:
        return False, (
            "Password must contain at least 3 of: uppercase, lowercase, digit, " "special character"
        )

    return True, "Password is strong"


def _encode_token(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.jwt.secret, algorithm=settings.jwt.algorithm)


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    expire = _utcnow() + (
        expires_delta or timedelta(minutes=settings.jwt.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire, "type": "access", "jti": secrets.token_hex(16)})
    return _encode_token(to_encode)


def create_refresh_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> tuple[str, str]:
    """Create a refresh token. Returns (token, jti) so callers can persist the jti."""
    to_encode = data.copy()
    expire = _utcnow() + (expires_delta or timedelta(days=settings.jwt.refresh_token_expire_days))
    jti = secrets.token_hex(16)
    to_encode.update({"exp": expire, "type": "refresh", "jti": jti})
    return _encode_token(to_encode), jti


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(
            token,
            settings.jwt.secret,
            algorithms=[settings.jwt.algorithm],
            options={"require": ["exp", "type", "sub", "jti"]},
        )
        return payload
    except JWTError as e:
        logger.warning("Token decode failed", error=str(e))
        return None


def verify_token(token: str, token_type: str = "access") -> dict[str, Any] | None:
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != token_type:
        return None
    return payload


def _domain_hash(domain: str, value: str) -> str:
    return hmac.new(
        settings.jwt.secret.encode("utf-8"),
        f"{domain}:{value}".encode(),
        hashlib.sha256,
    ).hexdigest()


def hash_api_key(api_key: str) -> str:
    """HMAC-SHA256 of the API key using the server secret; domain-separated."""
    return _domain_hash("api-key", api_key)


def hash_reset_token(token: str) -> str:
    """Reset tokens are stored only as HMAC digests; the raw token lives in email only."""
    return _domain_hash("password-reset", token)


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key. Returns (plaintext_key, prefix).

    The plaintext is shown exactly once to the caller; only its HMAC is stored.
    """
    secret_part = secrets.token_urlsafe(API_KEY_BYTES)
    plaintext = f"{API_KEY_PREFIX}_{secret_part}"
    prefix = plaintext[:12]
    return plaintext, prefix


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class PasswordValidator:
    def __init__(
        self,
        min_length: int = 8,
        require_upper: bool = True,
        require_lower: bool = True,
        require_digit: bool = True,
        require_special: bool = False,
        max_repeating: int = 3,
    ):
        self.min_length = min_length
        self.require_upper = require_upper
        self.require_lower = require_lower
        self.require_digit = require_digit
        self.require_special = require_special
        self.max_repeating = max_repeating

    def validate(self, password: str) -> tuple[bool, list[str]]:
        errors: list[str] = []

        if len(password) < self.min_length:
            errors.append(f"Password must be at least {self.min_length} characters long")

        if self.require_upper and not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")

        if self.require_lower and not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")

        if self.require_digit and not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")

        special_chars = "!@#$%^&*()-_=+[]{}|;:,.<>?"
        if self.require_special and not any(c in special_chars for c in password):
            errors.append("Password must contain at least one special character")

        if len(password.encode("utf-8")) > BCRYPT_MAX_BYTES:
            errors.append(f"Password must be at most {BCRYPT_MAX_BYTES} bytes")

        if self.max_repeating > 0:
            run = 1
            for i in range(1, len(password)):
                run = run + 1 if password[i] == password[i - 1] else 1
                if run >= self.max_repeating:
                    errors.append(
                        f"Password must not contain {self.max_repeating} repeating characters"
                    )
                    break

        return len(errors) == 0, errors


def sanitize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    import re

    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None
