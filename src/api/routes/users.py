from uuid import UUID

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.dependencies import (
    PaginationParams,
    get_current_user,
    get_org_admin_user,
)
from src.api.schemas.users import (
    BulkImportRequest,
    BulkImportResponse,
    SetPasswordRequest,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from src.config import settings
from src.database.models import Department, Embedding, Organization, User
from src.database.session import get_db
from src.services import email as email_service
from src.services.audit import record_audit
from src.utils.logger import get_logger
from src.utils.security import get_password_hash, validate_password_strength

router = APIRouter()
logger = get_logger(__name__)
audit_logger = structlog.get_logger("audit")


def _to_response(
    user: User,
    department_name: str | None,
    *,
    embedding_loaded: bool = True,
) -> UserResponse:
    if embedding_loaded:
        is_registered = user.embedding is not None
        quality = user.embedding.quality_score if user.embedding else None
    else:
        # Never trigger implicit lazy IO under asyncio - caller must eager-load.
        is_registered = False
        quality = None
    return UserResponse(
        id=user.id,
        name=user.name,
        external_id=user.external_id,
        email=user.email,
        phone=user.phone,
        department_id=user.department_id,
        department_name=department_name,
        role=user.role,
        is_active=user.is_active,
        is_registered=is_registered,
        registration_quality=quality,
        created_at=user.created_at,
    )


async def _department_names(db: AsyncSession, organization_id: UUID) -> dict[UUID, str]:
    result = await db.execute(
        select(Department.id, Department.name).where(Department.organization_id == organization_id)
    )
    return {row[0]: row[1] for row in result.all()}


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    org_result = await db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    count_result = await db.execute(
        select(func.count(User.id)).where(User.organization_id == current_user.organization_id)
    )
    if (count_result.scalar() or 0) >= org.max_users:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Organization user limit reached ({org.max_users})",
        )

    if user_data.role == "superadmin" and not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a superadmin can create superadmin accounts",
        )

    if current_user.role == "org_admin" and user_data.role == "org_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admins cannot create additional admins",
        )

    if user_data.external_id is not None:
        existing = await db.execute(
            select(User.id).where(
                (User.organization_id == current_user.organization_id)
                & (User.external_id == user_data.external_id)
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this external ID already exists",
            )

    if user_data.email is not None:
        normalized_email = user_data.email.lower()
        existing_email = await db.execute(
            select(User.id).where(
                (User.organization_id == current_user.organization_id)
                & (User.email == normalized_email)
            )
        )
        if existing_email.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User with this email already exists",
            )

    password_hash = None
    if user_data.password is not None:
        is_strong, message = validate_password_strength(user_data.password)
        if not is_strong:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=message)
        try:
            password_hash = get_password_hash(user_data.password)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if user_data.department_id is not None:
        dept = await db.execute(
            select(Department.id).where(
                (Department.id == user_data.department_id)
                & (Department.organization_id == current_user.organization_id)
            )
        )
        if dept.scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")

    new_user = User(
        organization_id=current_user.organization_id,
        name=user_data.name.strip(),
        external_id=user_data.external_id,
        email=normalized_email if user_data.email else None,
        phone=user_data.phone,
        department_id=user_data.department_id,
        role=user_data.role,
        password_hash=password_hash,
        metadata_=user_data.metadata,
    )
    db.add(new_user)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(new_user)

    if (
        user_data.send_invite_email
        and new_user.email
        and settings.email.enabled
        and settings.features.enable_email
    ):
        org_name = org.name if org else "your organization"
        background_tasks.add_task(
            email_service.send_invite_email,
            to=new_user.email,
            name=new_user.name,
            organization_name=org_name,
            org_slug=org.slug if org else "",
            has_temporary_password=password_hash is not None,
        )

    await record_audit(
        db,
        "user.created",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="user",
        target_id=new_user.id,
        details={"role": new_user.role},
    )

    logger.info("User created", user_id=str(new_user.id), created_by=str(current_user.id))
    return _to_response(new_user, None, embedding_loaded=False)


@router.get("", response_model=UserListResponse)
async def list_users(
    search: str | None = Query(None, max_length=200),
    department_id: UUID | None = None,
    is_active: bool | None = None,
    is_registered: bool | None = None,
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(User)
        .options(joinedload(User.embedding))
        .where(User.organization_id == current_user.organization_id)
    )
    count_query = select(func.count(User.id)).where(
        User.organization_id == current_user.organization_id
    )

    filters = []
    if search:
        term = f"%{search}%"
        filters.append(
            (User.name.ilike(term)) | (User.email.ilike(term)) | (User.external_id.ilike(term))
        )
    if department_id is not None:
        filters.append(User.department_id == department_id)
    if is_active is not None:
        filters.append(User.is_active.is_(is_active))
    if is_registered is not None:
        registered_ids = select(Embedding.user_id).scalar_subquery()
        if is_registered:
            filters.append(User.id.in_(registered_ids))
        else:
            filters.append(User.id.not_in(registered_ids))

    for condition in filters:
        query = query.where(condition)
        count_query = count_query.where(condition)

    total = (await db.execute(count_query)).scalar() or 0

    query = (
        query.order_by(User.created_at.desc()).offset(pagination.offset).limit(pagination.page_size)
    )
    result = await db.execute(query)
    users = result.scalars().unique().all()

    dept_names = await _department_names(db, current_user.organization_id)
    responses = [
        _to_response(u, dept_names.get(u.department_id) if u.department_id else None) for u in users
    ]

    return UserListResponse(
        users=responses,
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    refreshed = await db.execute(
        select(User).options(joinedload(User.embedding)).where(User.id == current_user.id)
    )
    me = refreshed.scalar_one()
    dept_name = None
    if me.department_id:
        row = await db.execute(select(Department.name).where(Department.id == me.department_id))
        dept_name = row.scalar_one_or_none()
    return _to_response(me, dept_name)


async def _get_scoped_user(
    user_id: UUID, requester: User, db: AsyncSession, *, allow_cross_org_for_superadmin: bool = True
) -> User:
    query = select(User).options(joinedload(User.embedding)).where(User.id == user_id)
    if not (requester.is_superadmin and allow_cross_org_for_superadmin):
        query = query.where(User.organization_id == requester.organization_id)
    result = await db.execute(query)
    return result.scalar_one_or_none()


def _assert_can_manage_target(requester: User, target: User) -> None:
    """A non-superadmin may never act on a superadmin, even in their own org.

    Bootstrap places the superadmin inside an organization, so without this an
    org admin could reset their password or deactivate them.
    """
    if target.is_superadmin and not requester.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot manage a superadmin account",
        )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Members may read their own record; admins may read anyone in the same org.
    if current_user.is_org_admin or current_user.is_superadmin:
        query = select(User).options(joinedload(User.embedding)).where(User.id == user_id)
        if not current_user.is_superadmin:
            query = query.where(User.organization_id == current_user.organization_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()
    else:
        if current_user.id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You can only view your own profile",
            )
        result = await db.execute(
            select(User).options(joinedload(User.embedding)).where(User.id == current_user.id)
        )
        user = result.scalar_one()

    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    dept_name = None
    if user.department_id:
        row = await db.execute(select(Department.name).where(Department.id == user.department_id))
        dept_name = row.scalar_one_or_none()

    return _to_response(user, dept_name)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    user_data: UserUpdate,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    user = await _get_scoped_user(user_id, current_user, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_can_manage_target(current_user, user)

    changes: dict = {}
    if user_data.name is not None:
        user.name = user_data.name.strip()
        changes["name"] = True
    if user_data.external_id is not None:
        conflict = await db.execute(
            select(User.id).where(
                (User.organization_id == user.organization_id)
                & (User.external_id == user_data.external_id)
                & (User.id != user.id)
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="Another user already has this external ID"
            )
        user.external_id = user_data.external_id
        changes["external_id"] = True
    if user_data.email is not None:
        normalized = user_data.email.lower()
        conflict = await db.execute(
            select(User.id).where(
                (User.organization_id == user.organization_id)
                & (User.email == normalized)
                & (User.id != user.id)
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="Another user already has this email"
            )
        user.email = normalized
        changes["email"] = True
    if user_data.phone is not None:
        user.phone = user_data.phone
        changes["phone"] = True
    if user_data.department_id is not None:
        if user.organization_id == current_user.organization_id or current_user.is_superadmin:
            dept = await db.execute(
                select(Department.id).where(
                    (Department.id == user_data.department_id)
                    & (Department.organization_id == user.organization_id)
                )
            )
            if dept.scalar_one_or_none() is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")
        user.department_id = user_data.department_id
        changes["department_id"] = True
    if user_data.role is not None:
        if user_data.role == "superadmin" and not current_user.is_superadmin:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "Only a superadmin can grant superadmin role"
            )
        user.role = user_data.role
        changes["role"] = user_data.role
    if user_data.is_active is not None:
        user.is_active = user_data.is_active
        changes["is_active"] = user_data.is_active
    if user_data.metadata is not None:
        user.metadata_ = user_data.metadata
        changes["metadata"] = True

    await db.commit()
    logger.info("User updated", user_id=str(user.id), updated_by=str(current_user.id))

    await record_audit(
        db,
        "user.updated",
        organization_id=user.organization_id,
        actor_id=current_user.id,
        target_type="user",
        target_id=user.id,
        details={"changes": list(changes.keys())},
    )

    # Re-fetch with eager loading for the response payload.
    refreshed = await db.execute(
        select(User).options(joinedload(User.embedding)).where(User.id == user.id)
    )
    user = refreshed.scalar_one()

    dept_name = None
    if user.department_id:
        row = await db.execute(select(Department.name).where(Department.id == user.department_id))
        dept_name = row.scalar_one_or_none()

    from src.inference.index_manager import matcher_registry

    if changes.get("is_active") and not user.is_active and user.embedding is not None:
        await matcher_registry.remove_user(str(user.organization_id), str(user.id))

    return _to_response(user, dept_name)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    if user_id == current_user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account"
        )

    user = await _get_scoped_user(user_id, current_user, db)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_can_manage_target(current_user, user)

    was_active_with_embedding = user.is_active and user.embedding is not None
    user.is_active = False
    await db.commit()

    if was_active_with_embedding:
        from src.inference.index_manager import matcher_registry

        await matcher_registry.remove_user(str(user.organization_id), str(user.id))

    await record_audit(
        db,
        "user.deactivated",
        organization_id=user.organization_id,
        actor_id=current_user.id,
        target_type="user",
        target_id=user.id,
        commit=False,
    )
    logger.info("User deactivated", user_id=str(user.id), deactivated_by=str(current_user.id))
    return None


@router.post("/bulk-import", response_model=BulkImportResponse)
async def bulk_import_users(
    import_data: BulkImportRequest,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    imported = 0
    errors: list[dict] = []

    if import_data.role == "superadmin" and not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a superadmin can create superadmin accounts",
        )
    if current_user.role == "org_admin" and import_data.role == "org_admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization admins cannot create additional admins",
        )

    org_result = await db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )
    org = org_result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    count_result = await db.execute(
        select(func.count(User.id)).where(User.organization_id == current_user.organization_id)
    )
    remaining_slots = max(0, org.max_users - (count_result.scalar() or 0))

    if import_data.department_id is not None:
        dept = await db.execute(
            select(Department.id).where(
                (Department.id == import_data.department_id)
                & (Department.organization_id == current_user.organization_id)
            )
        )
        if dept.scalar_one_or_none() is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")

    seen_external: set[str] = set()
    seen_email: set[str] = set()

    for index, item in enumerate(import_data.users):
        external_key = item.external_id.strip() if item.external_id else ""
        email_key = item.email.lower() if item.email else ""

        if imported >= remaining_slots:
            errors.append(
                {
                    "row": index,
                    "name": item.name,
                    "error": f"Organization user limit reached ({org.max_users})",
                }
            )
            continue

        if external_key and external_key in seen_external:
            errors.append(
                {"row": index, "name": item.name, "error": "Duplicate external_id in request"}
            )
            continue
        if email_key and email_key in seen_email:
            errors.append({"row": index, "name": item.name, "error": "Duplicate email in request"})
            continue

        try:
            async with db.begin_nested():
                new_user = User(
                    organization_id=current_user.organization_id,
                    name=item.name.strip(),
                    external_id=item.external_id,
                    email=email_key or None,
                    phone=item.phone,
                    department_id=import_data.department_id,
                    role=import_data.role,
                )
                db.add(new_user)
                await db.flush()
            imported += 1
            if external_key:
                seen_external.add(external_key)
            if email_key:
                seen_email.add(email_key)
        except Exception as exc:
            errors.append({"row": index, "name": item.name, "error": type(exc).__name__})

    await db.commit()

    await record_audit(
        db,
        "user.bulk_imported",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        details={"imported": imported, "failed": len(errors)},
    )
    logger.info(
        "Bulk import completed",
        imported=imported,
        failed=len(errors),
        org_id=str(current_user.organization_id),
    )

    return BulkImportResponse(imported=imported, failed=len(errors), errors=errors)


@router.post("/{user_id}/password", status_code=status.HTTP_200_OK)
async def set_user_password(
    user_id: UUID,
    request: SetPasswordRequest,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Admin-set initial or replacement password; all sessions are revoked."""
    user = await _get_scoped_user(user_id, current_user, db)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    _assert_can_manage_target(current_user, user)

    is_strong, message = validate_password_strength(request.new_password)
    if not is_strong:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=message)

    try:
        user.password_hash = get_password_hash(request.new_password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()

    from src.services.auth_service import revoke_all_user_tokens

    await revoke_all_user_tokens(db, user.id, reason="admin_password_set")

    await record_audit(
        db,
        "user.password_set_by_admin",
        organization_id=user.organization_id,
        actor_id=current_user.id,
        target_type="user",
        target_id=user.id,
        commit=False,
    )
    return {"message": "Password updated"}
