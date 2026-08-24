from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models import RefreshTokenRecord, User
from src.utils.security import create_access_token, create_refresh_token, decode_token

logger = structlog.get_logger(__name__)


def _ensure_utc(value: datetime) -> datetime:
    """Treat DB-provided naive datetimes as UTC (SQLite returns naive values)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class TokenPair:
    __slots__ = ("access_token", "refresh_token", "expires_in")

    def __init__(self, access_token: str, refresh_token: str, expires_in: int):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_in = expires_in


async def issue_token_pair(
    db: AsyncSession,
    *,
    user: User,
    client_ip: str | None = None,
) -> TokenPair:
    """Create a fresh access/refresh pair and persist the refresh jti."""
    subject = str(user.id)
    org_id = str(user.organization_id)

    access_token = create_access_token(data={"sub": subject, "org_id": org_id})
    refresh_token, jti = create_refresh_token(data={"sub": subject, "org_id": org_id})

    record = RefreshTokenRecord(
        jti=jti,
        user_id=user.id,
        organization_id=user.organization_id,
        expires_at=datetime.now(UTC) + timedelta(days=settings.jwt.refresh_token_expire_days),
        created_ip=client_ip,
    )
    db.add(record)
    await db.commit()

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt.access_token_expire_minutes * 60,
    )


async def rotate_refresh_token(
    db: AsyncSession,
    raw_refresh_token: str,
    *,
    client_ip: str | None = None,
) -> tuple[TokenPair, User]:
    """Rotate a refresh token.

    - Unknown or expired tokens are rejected.
    - Presenting an already-rotated (revoked) token indicates theft: every
      active session for that user is revoked immediately.
    """
    payload = decode_token(raw_refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise _invalid_credentials()

    jti = payload.get("jti")
    if not jti:
        raise _invalid_credentials()

    result = await db.execute(select(RefreshTokenRecord).where(RefreshTokenRecord.jti == jti))
    record = result.scalar_one_or_none()
    if record is None:
        raise _invalid_credentials()

    now = datetime.now(UTC)
    if record.revoked_at is not None:
        await revoke_all_user_tokens(db, record.user_id, reason="reuse_detected")
        logger.warning(
            "Refresh token reuse detected - all sessions revoked",
            user_id=str(record.user_id),
            jti=jti[:8],
        )
        raise _invalid_credentials()

    if _ensure_utc(record.expires_at) <= now:
        raise _invalid_credentials()

    result = await db.execute(select(User).where(User.id == record.user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise _invalid_credentials()

    new_refresh_token, new_jti = create_refresh_token(
        data={"sub": str(user.id), "org_id": str(user.organization_id)}
    )
    record.revoked_at = now
    record.replaced_by_jti = new_jti

    db.add(
        RefreshTokenRecord(
            jti=new_jti,
            user_id=user.id,
            organization_id=user.organization_id,
            expires_at=now + timedelta(days=settings.jwt.refresh_token_expire_days),
            created_ip=client_ip,
        )
    )
    await db.commit()

    access_token = create_access_token(
        data={"sub": str(user.id), "org_id": str(user.organization_id)}
    )
    return (
        TokenPair(
            access_token=access_token,
            refresh_token=new_refresh_token,
            expires_in=settings.jwt.access_token_expire_minutes * 60,
        ),
        user,
    )


async def revoke_refresh_token(db: AsyncSession, raw_refresh_token: str) -> bool:
    payload = decode_token(raw_refresh_token)
    if payload is None:
        return False
    jti = payload.get("jti")
    if not jti:
        return False

    result = await db.execute(
        update(RefreshTokenRecord)
        .where(RefreshTokenRecord.jti == jti)
        .where(RefreshTokenRecord.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    return result.rowcount > 0


async def revoke_all_user_tokens(
    db: AsyncSession, user_id: UUID | str, *, reason: str = "logout_all"
) -> int:
    result = await db.execute(
        update(RefreshTokenRecord)
        .where(RefreshTokenRecord.user_id == user_id)
        .where(RefreshTokenRecord.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    await db.commit()
    revoked = result.rowcount or 0
    if revoked:
        logger.info("Revoked user sessions", user_id=str(user_id), count=revoked, reason=reason)
    return revoked


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token",
    )
