import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_org_admin_user, get_superadmin_user
from src.api.schemas.admin import (
    APIKeyCreate,
    APIKeyCreatedResponse,
    APIKeyListResponse,
    APIKeyResponse,
    OrganizationCreate,
    OrganizationListResponse,
    OrganizationResponse,
    OrganizationUpdate,
)
from src.database.models import APIKey, Organization, TrainingJob, User
from src.database.session import get_db
from src.services.audit import record_audit
from src.utils.logger import get_logger
from src.utils.security import generate_api_key, hash_api_key

router = APIRouter()
logger = get_logger(__name__)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")


def _org_response(org: Organization, user_count: int) -> OrganizationResponse:
    return OrganizationResponse(
        id=org.id,
        name=org.name,
        slug=org.slug,
        max_users=org.max_users,
        is_active=org.is_active,
        user_count=user_count,
        created_at=org.created_at,
    )


@router.get("/organizations", response_model=OrganizationListResponse)
async def list_organizations(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size

    total = (await db.execute(select(func.count(Organization.id)))).scalar() or 0

    result = await db.execute(
        select(Organization)
        .order_by(Organization.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    orgs = result.scalars().all()

    counts_result = await db.execute(
        select(User.organization_id, func.count(User.id)).group_by(User.organization_id)
    )
    counts = {row[0]: row[1] for row in counts_result.all()}

    return OrganizationListResponse(
        organizations=[_org_response(o, counts.get(o.id, 0)) for o in orgs],
        total=total,
    )


@router.post(
    "/organizations", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED
)
async def create_organization(
    data: OrganizationCreate,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    slug = data.slug.strip().lower()
    if not SLUG_PATTERN.match(slug):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Slug must be lowercase alphanumeric with inner hyphens",
        )

    existing = await db.execute(select(Organization.id).where(Organization.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Organization slug already exists")

    org = Organization(
        name=data.name.strip(),
        slug=slug,
        max_users=data.max_users,
        settings=data.settings or {},
        is_active=True,
    )
    db.add(org)
    await db.commit()
    await db.refresh(org)

    await record_audit(
        db,
        "organization.created",
        organization_id=org.id,
        actor_id=current_user.id,
        target_type="organization",
        target_id=org.id,
        details={"slug": org.slug},
        commit=False,
    )
    logger.info("Organization created", slug=org.slug, by=str(current_user.id))

    return _org_response(org, 0)


@router.get("/organizations/{organization_id}", response_model=OrganizationResponse)
async def get_organization(
    organization_id: UUID,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Organization).where(Organization.id == organization_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    count = (
        await db.execute(select(func.count(User.id)).where(User.organization_id == organization_id))
    ).scalar() or 0

    return _org_response(org, count)


@router.patch("/organizations/{organization_id}", response_model=OrganizationResponse)
async def update_organization(
    organization_id: UUID,
    data: OrganizationUpdate,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Organization).where(Organization.id == organization_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    if data.name is not None:
        org.name = data.name.strip()
    if data.max_users is not None:
        org.max_users = data.max_users
    if data.is_active is not None:
        org.is_active = data.is_active
    if data.settings is not None:
        merged = dict(org.settings or {})
        merged.update(data.settings)
        org.settings = merged

    await db.commit()

    await record_audit(
        db,
        "organization.updated",
        organization_id=org.id,
        actor_id=current_user.id,
        target_type="organization",
        target_id=org.id,
        details={"is_active": org.is_active},
        commit=False,
    )

    count = (
        await db.execute(select(func.count(User.id)).where(User.organization_id == organization_id))
    ).scalar() or 0

    return _org_response(org, count)


def _api_key_response(key: APIKey) -> APIKeyResponse:
    return APIKeyResponse(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        scopes=key.scopes,
        is_active=key.is_active,
        last_used_at=key.last_used_at,
        expires_at=key.expires_at,
        created_at=key.created_at,
    )


@router.get("/api-keys", response_model=APIKeyListResponse)
async def list_api_keys(
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(APIKey)
        .where(APIKey.organization_id == current_user.organization_id)
        .order_by(APIKey.created_at.desc())
    )
    keys = result.scalars().all()
    return APIKeyListResponse(api_keys=[_api_key_response(k) for k in keys], total=len(keys))


@router.post("/api-keys", response_model=APIKeyCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    data: APIKeyCreate,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    plaintext, prefix = generate_api_key()

    expires_at = None
    if data.expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=data.expires_in_days)

    key = APIKey(
        organization_id=current_user.organization_id,
        name=data.name.strip(),
        key_hash=hash_api_key(plaintext),
        prefix=prefix,
        scopes=data.scopes,
        created_by=current_user.id,
        expires_at=expires_at,
        is_active=True,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    await record_audit(
        db,
        "api_key.created",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="api_key",
        target_id=key.id,
        details={"name": key.name, "scopes": key.scopes},
        commit=False,
    )
    logger.info("API key created", id=str(key.id), by=str(current_user.id))

    response = APIKeyCreatedResponse(
        **_api_key_response(key).model_dump(),
        key=plaintext,
    )
    return response


@router.delete("/api-keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: UUID,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(APIKey).where(
            (APIKey.id == key_id) & (APIKey.organization_id == current_user.organization_id)
        )
    )
    key = result.scalar_one_or_none()
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="API key not found")

    key.is_active = False
    await db.commit()

    await record_audit(
        db,
        "api_key.revoked",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="api_key",
        target_id=key.id,
        commit=False,
    )
    logger.info("API key revoked", id=str(key.id), by=str(current_user.id))
    return None


class TrainingJobCreate(BaseModel):
    config: dict = Field(default_factory=dict)


class TrainingJobResponse(BaseModel):
    id: UUID
    status: str
    model_type: str
    metrics: dict
    error_message: str | None
    checkpoint_path: str | None
    onnx_path: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


@router.post("/training-jobs", response_model=TrainingJobResponse, status_code=201)
async def create_training_job(
    body: TrainingJobCreate,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue a model fine-tuning job. Requires data_root in config."""
    from src.config import settings as app_settings

    if not app_settings.features.enable_training:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Training disabled")

    if not body.config.get("data_root"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="config.data_root is required")

    job = TrainingJob(
        organization_id=current_user.organization_id
        if not current_user.is_superadmin or current_user.organization_id
        else None,
        status="pending",
        config=body.config,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    from src.workers.tasks.training_tasks import start_training_task

    start_training_task.delay(str(job.id))

    await record_audit(
        db,
        "training_job.created",
        organization_id=job.organization_id,
        actor_id=current_user.id,
        target_type="training_job",
        target_id=job.id,
        commit=False,
    )
    logger.info("Training job queued", job_id=str(job.id), by=str(current_user.id))
    return TrainingJobResponse.model_validate(job)


@router.get("/training-jobs", response_model=list[TrainingJobResponse])
async def list_training_jobs(
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(TrainingJob).order_by(TrainingJob.created_at.desc()).limit(50))
    return [TrainingJobResponse.model_validate(j) for j in result.scalars().all()]


@router.get("/training-jobs/{job_id}", response_model=TrainingJobResponse)
async def get_training_job(
    job_id: UUID,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(TrainingJob).where(TrainingJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Training job not found")
    return TrainingJobResponse.model_validate(job)
