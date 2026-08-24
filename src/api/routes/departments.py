from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_org_admin_user
from src.api.schemas.admin import (
    DepartmentCreate,
    DepartmentListResponse,
    DepartmentResponse,
    DepartmentUpdate,
)
from src.database.models import Department, User
from src.database.session import get_db
from src.services.audit import record_audit
from src.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


async def _scoped_department(
    department_id: UUID, organization_id: UUID, db: AsyncSession
) -> Department | None:
    result = await db.execute(
        select(Department).where(
            (Department.id == department_id) & (Department.organization_id == organization_id)
        )
    )
    return result.scalar_one_or_none()


@router.get("", response_model=DepartmentListResponse)
async def list_departments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Department)
        .where(Department.organization_id == current_user.organization_id)
        .order_by(Department.name)
    )
    departments = result.scalars().all()

    counts_result = await db.execute(
        select(User.department_id, func.count(User.id))
        .where(User.organization_id == current_user.organization_id)
        .group_by(User.department_id)
    )
    counts = {row[0]: row[1] for row in counts_result.all()}

    return DepartmentListResponse(
        departments=[
            DepartmentResponse(
                id=d.id,
                name=d.name,
                parent_id=d.parent_id,
                user_count=counts.get(d.id, 0),
                created_at=d.created_at,
            )
            for d in departments
        ],
        total=len(departments),
    )


@router.post("", response_model=DepartmentResponse, status_code=status.HTTP_201_CREATED)
async def create_department(
    data: DepartmentCreate,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(Department.id).where(
            (Department.organization_id == current_user.organization_id)
            & (Department.name == data.name.strip())
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Department name already exists"
        )

    if data.parent_id is not None:
        parent = await _scoped_department(data.parent_id, current_user.organization_id, db)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Parent department not found")
        if parent.parent_id is not None:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Department hierarchy limited to two levels"
            )

    dept = Department(
        organization_id=current_user.organization_id,
        name=data.name.strip(),
        parent_id=data.parent_id,
    )
    db.add(dept)
    await db.commit()
    await db.refresh(dept)

    await record_audit(
        db,
        "department.created",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="department",
        target_id=dept.id,
        details={"name": dept.name},
    )
    logger.info("Department created", id=str(dept.id), by=str(current_user.id))

    return DepartmentResponse(
        id=dept.id,
        name=dept.name,
        parent_id=dept.parent_id,
        user_count=0,
        created_at=dept.created_at,
    )


@router.patch("/{department_id}", response_model=DepartmentResponse)
async def update_department(
    department_id: UUID,
    data: DepartmentUpdate,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    dept = await _scoped_department(department_id, current_user.organization_id, db)
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")

    if data.name is not None:
        conflict = await db.execute(
            select(Department.id).where(
                (Department.organization_id == current_user.organization_id)
                & (Department.name == data.name.strip())
                & (Department.id != department_id)
            )
        )
        if conflict.scalar_one_or_none():
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Department name already exists")
        dept.name = data.name.strip()

    if data.parent_id is not None:
        if data.parent_id == department_id:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Department cannot be its own parent"
            )
        parent = await _scoped_department(data.parent_id, current_user.organization_id, db)
        if parent is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Parent department not found")

    if data.parent_id is not None:
        dept.parent_id = data.parent_id

    await db.commit()
    await db.refresh(dept)

    await record_audit(
        db,
        "department.updated",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="department",
        target_id=dept.id,
        commit=False,
    )

    count = (
        await db.execute(select(func.count(User.id)).where(User.department_id == dept.id))
    ).scalar() or 0

    return DepartmentResponse(
        id=dept.id,
        name=dept.name,
        parent_id=dept.parent_id,
        user_count=count,
        created_at=dept.created_at,
    )


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(
    department_id: UUID,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    dept = await _scoped_department(department_id, current_user.organization_id, db)
    if dept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Department not found")

    in_use = (
        await db.execute(select(func.count(User.id)).where(User.department_id == department_id))
    ).scalar() or 0
    if in_use > 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Department still has {in_use} users; reassign them first",
        )

    await db.delete(dept)
    await db.commit()

    await record_audit(
        db,
        "department.deleted",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="department",
        target_id=department_id,
        commit=False,
    )
    logger.info("Department deleted", id=str(department_id), by=str(current_user.id))
    return None
