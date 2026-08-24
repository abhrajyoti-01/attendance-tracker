import secrets
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models import Organization, PasswordResetToken, User
from src.services.auth_service import revoke_all_user_tokens
from src.utils.security import hash_reset_token

logger = structlog.get_logger(__name__)


async def create_reset_token(
    db: AsyncSession,
    *,
    user: User,
    client_ip: str | None = None,
) -> tuple[str, PasswordResetToken]:
    """Create a one-time reset token. Returns (raw_token, record); raw token goes to email only."""
    for _ in range(5):
        raw = secrets.token_urlsafe(32)
        digest = hash_reset_token(raw)
        existing = await db.execute(
            select(PasswordResetToken).where(PasswordResetToken.token_hash == digest)
        )
        if existing.scalar_one_or_none() is None:
            break
    else:  # pragma: no cover - 2^256 collision
        raise RuntimeError("Unable to generate unique reset token")

    record = PasswordResetToken(
        token_hash=digest,
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=settings.jwt.reset_token_expire_minutes),
        requested_ip=client_ip,
    )
    db.add(record)

    # A user may only have one pending reset at a time.
    pending = await db.execute(
        select(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .where(PasswordResetToken.used_at.is_(None))
        .where(PasswordResetToken.id != record.id)
    )
    for stale in pending.scalars():
        stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    await db.commit()
    return raw, record


async def consume_reset_token(
    db: AsyncSession,
    *,
    raw_token: str,
    new_password_hash: str,
) -> bool:
    digest = hash_reset_token(raw_token)
    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == digest)
    )
    record = result.scalar_one_or_none()
    if record is None or not record.is_usable:
        return False

    user_result = await db.execute(select(User).where(User.id == record.user_id))
    user = user_result.scalar_one_or_none()
    if user is None or not user.is_active:
        return False

    record.used_at = datetime.now(UTC)
    user.password_hash = new_password_hash
    await db.commit()

    await revoke_all_user_tokens(db, user.id, reason="password_reset")
    logger.info("Password reset consumed", user_id=str(user.id))
    return True


async def resolve_user_for_reset(
    db: AsyncSession, *, organization: Organization, email: str
) -> User | None:
    result = await db.execute(
        select(User).where(
            (User.organization_id == organization.id) & (User.email == email.strip().lower())
        )
    )
    return result.scalar_one_or_none()
