import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_organization_by_slug
from src.api.schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    TokenResponse,
)
from src.config import settings
from src.database.models import Organization, User
from src.database.session import get_db
from src.services import email as email_service
from src.services.audit import record_audit
from src.services.auth_service import (
    issue_token_pair,
    revoke_all_user_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
)
from src.services.password_reset import (
    consume_reset_token,
    create_reset_token,
    resolve_user_for_reset,
)
from src.utils.logger import get_logger
from src.utils.security import (
    get_password_hash,
    validate_password_strength,
    verify_password,
)

# Hashing target used to equalize response time when the account does not exist.
_DUMMY_HASH = get_password_hash("timing-equalizer-dummy-value")

router = APIRouter()
logger = get_logger(__name__)
audit_logger = structlog.get_logger("audit")


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    client_ip = _client_ip(http_request)

    result = await db.execute(
        select(Organization).where(Organization.slug == request.organization_slug)
    )
    org = result.scalar_one_or_none()

    if org is None:
        verify_password(request.password, _DUMMY_HASH)
        await record_audit(
            db,
            "auth.login_failed",
            details={"reason": "unknown_organization", "org_slug": request.organization_slug},
            ip_address=client_ip,
            commit=True,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not org.is_active:
        await record_audit(
            db,
            "auth.login_failed",
            organization_id=org.id,
            details={"reason": "organization_inactive"},
            ip_address=client_ip,
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Organization is inactive"
        )

    email_normalized = request.email.lower().strip()
    result = await db.execute(
        select(User).where((User.organization_id == org.id) & (User.email == email_normalized))
    )
    user = result.scalar_one_or_none()

    if user is None:
        verify_password(request.password, _DUMMY_HASH)
        await record_audit(
            db,
            "auth.login_failed",
            organization_id=org.id,
            details={"reason": "unknown_user"},
            ip_address=client_ip,
            commit=True,
        )
        logger.warning("Login failed - unknown user", org=org.slug)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        await record_audit(
            db,
            "auth.login_failed",
            organization_id=org.id,
            actor_id=user.id,
            details={"reason": "user_inactive"},
            ip_address=client_ip,
            commit=True,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive"
        )

    if user.password_hash is None or not verify_password(request.password, user.password_hash):
        await record_audit(
            db,
            "auth.login_failed",
            organization_id=org.id,
            actor_id=user.id,
            details={"reason": "bad_password"},
            ip_address=client_ip,
            commit=True,
        )
        logger.warning("Login failed - bad password", user_id=str(user.id))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    pair = await issue_token_pair(db, user=user, client_ip=client_ip)
    await record_audit(
        db,
        "auth.login_success",
        organization_id=org.id,
        actor_id=user.id,
        ip_address=client_ip,
        commit=False,
    )

    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user_id=str(user.id),
        user_name=user.name,
        organization_id=str(org.id),
        role=user.role,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: RefreshTokenRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    pair, user = await rotate_refresh_token(
        db, request.refresh_token, client_ip=_client_ip(http_request)
    )
    return TokenResponse(
        access_token=pair.access_token,
        refresh_token=pair.refresh_token,
        expires_in=pair.expires_in,
        user_id=str(user.id),
        user_name=user.name,
        organization_id=str(user.organization_id),
        role=user.role,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: LogoutRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await revoke_refresh_token(db, request.refresh_token)
    await revoke_all_user_tokens(db, current_user.id, reason="logout")
    await record_audit(
        db,
        "auth.logout",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        ip_address=_client_ip(http_request),
        commit=False,
    )
    return None


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    request: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    http_request: Request,
    org: Organization = Depends(get_organization_by_slug),
    db: AsyncSession = Depends(get_db),
):
    """Responds identically whether or not the account exists."""
    client_ip = _client_ip(http_request)
    user = await resolve_user_for_reset(db, organization=org, email=request.email)

    if user is not None and user.is_active:
        raw_token, record = await create_reset_token(db, user=user, client_ip=client_ip)
        if settings.email.enabled and settings.features.enable_email:
            background_tasks.add_task(
                email_service.send_password_reset_email,
                to=user.email,
                name=user.name,
                raw_token=raw_token,
                organization_name=org.name,
                org_slug=org.slug,
                expiry_minutes=settings.jwt.reset_token_expire_minutes,
            )
        else:
            logger.error(
                "Password reset requested but SMTP is not configured; "
                "reset link could not be delivered",
                user_id=str(user.id),
            )
        await record_audit(
            db,
            "auth.password_reset_requested",
            organization_id=org.id,
            actor_id=user.id,
            ip_address=client_ip,
            commit=False,
        )

    return {
        "message": "If the email exists, a reset link has been sent",
        "email_delivery_configured": settings.email.enabled,
    }


@router.post("/reset-password", status_code=status.HTTP_200_OK)
async def reset_password(
    request: ResetPasswordRequest,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
):
    is_strong, strength_message = validate_password_strength(request.new_password)
    if not is_strong:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=strength_message)

    try:
        new_hash = get_password_hash(request.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    consumed = await consume_reset_token(db, raw_token=request.token, new_password_hash=new_hash)
    if not consumed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token",
        )

    await record_audit(
        db,
        "auth.password_reset_completed",
        ip_address=_client_ip(http_request),
        commit=False,
    )
    return {"message": "Password reset successful"}


@router.post("/change-password", status_code=status.HTTP_200_OK)
async def change_password(
    request: ChangePasswordRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.password_hash is None or not verify_password(
        request.current_password, current_user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Current password is incorrect",
        )

    is_strong, strength_message = validate_password_strength(request.new_password)
    if not is_strong:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=strength_message)

    try:
        current_user.password_hash = get_password_hash(request.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()

    # Any other session must re-authenticate after a credential change.
    await revoke_all_user_tokens(db, current_user.id, reason="password_changed")

    await record_audit(
        db,
        "auth.password_changed",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        ip_address=_client_ip(http_request),
        commit=False,
    )
    return {"message": "Password changed successfully"}


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host if request.client else None
