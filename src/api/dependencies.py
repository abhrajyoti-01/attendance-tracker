from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models import APIKey, Organization, User
from src.database.session import get_db
from src.utils.security import hash_api_key, verify_token

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_short_lived(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> User:
    """Authenticate with a session that is closed before the response is sent.

    FastAPI tears request-scoped dependencies down only after a streaming
    response finishes, so depending on ``get_db`` for SSE holds a pooled
    connection for the whole connection lifetime. This variant performs the
    lookup in its own session and returns a detached user, freeing the
    connection immediately.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    from src.database.session import get_db_context

    async with get_db_context() as db:
        user = await _resolve_active_user(credentials.credentials, db)
        db.expunge(user)
        return user


async def get_org_admin_user_short_lived(
    current_user: User = Depends(get_current_user_short_lived),
) -> User:
    """Admin-gated variant of :func:`get_current_user_short_lived` for SSE."""
    if not current_user.is_org_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin privileges required",
        )
    return current_user


class RequesterContext:
    """Unified identity for either an authenticated user or an API key."""

    __slots__ = ("user", "api_key")

    def __init__(self, user: User | None = None, api_key: APIKey | None = None):
        self.user = user
        self.api_key = api_key
        if user is None and api_key is None:
            raise ValueError("RequesterContext requires a user or an api_key")

    @property
    def organization_id(self) -> UUID:
        return (
            self.api_key.organization_id if self.api_key is not None else self.user.organization_id  # type: ignore[union-attr]
        )

    @property
    def actor_id(self) -> UUID | None:
        return self.user.id if self.user is not None else None

    @property
    def is_superadmin(self) -> bool:
        return self.user is not None and self.user.is_superadmin


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await _resolve_active_user(credentials.credentials, db)


async def _resolve_active_user(token: str, db: AsyncSession) -> User:
    payload = verify_token(token, token_type="access")
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw_user_id = payload.get("sub")
    if raw_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    try:
        user_id = UUID(str(raw_user_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    # Tokens minted before the last logout/password change carry an older
    # token_version and must not be honoured.
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
    return current_user


async def get_org_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_org_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admin privileges required",
        )
    return current_user


async def get_superadmin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required",
        )
    return current_user


async def get_optional_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> APIKey | None:
    if x_api_key is None:
        return None
    return await _load_active_api_key(x_api_key, db)


async def require_api_key(
    x_api_key: str | None = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    if x_api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required",
        )
    return await _load_active_api_key(x_api_key, db)


async def _load_active_api_key(raw_key: str, db: AsyncSession) -> APIKey:
    result = await db.execute(select(APIKey).where(APIKey.key_hash == hash_api_key(raw_key)))
    api_key_obj = result.scalar_one_or_none()

    if api_key_obj is None or not api_key_obj.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or inactive API key",
        )

    now = datetime.now(UTC)
    if api_key_obj.expires_at is not None and api_key_obj.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key has expired",
        )

    api_key_obj.last_used_at = now
    await db.commit()
    return api_key_obj


def require_api_key_scope(*required_scopes: str):
    """Dependency factory enforcing that the presented API key carries all scopes."""

    async def _verify(
        api_key_obj: APIKey = Depends(require_api_key),
    ) -> APIKey:
        missing = [s for s in required_scopes if not api_key_obj.has_scope(s)]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing required scopes: {', '.join(missing)}",
            )
        return api_key_obj

    return _verify


async def bearer_or_none(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Resolve bearer credentials to a user without raising when absent."""
    if credentials is None:
        return None
    payload = verify_token(credentials.credentials, token_type="access")
    if payload is None:
        return None
    raw_user_id = payload.get("sub")
    if raw_user_id is None:
        return None
    try:
        user_id = UUID(str(raw_user_id))
    except ValueError:
        return None
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


async def authenticate_requester(
    request_user: User | None = Depends(bearer_or_none),
    api_key_obj: APIKey | None = Depends(get_optional_api_key),
) -> RequesterContext:
    """Accept either a Bearer user or X-API-Key machine identity."""
    if request_user is not None:
        return RequesterContext(user=request_user)
    if api_key_obj is not None:
        return RequesterContext(api_key=api_key_obj)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required (Bearer token or X-API-Key)",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_organization_by_slug(
    org_slug: str,
    db: AsyncSession = Depends(get_db),
) -> Organization:
    result = await db.execute(select(Organization).where(Organization.slug == org_slug))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    if not org.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is inactive",
        )
    return org


class PaginationParams:
    def __init__(
        self,
        page: int = Query(1, ge=1),
        page_size: int = Query(settings.api.default_page_size, ge=1),
    ):
        clamped_size = min(page_size, settings.api.max_page_size)
        self.page = page
        self.page_size = clamped_size
        self.offset = (page - 1) * clamped_size
