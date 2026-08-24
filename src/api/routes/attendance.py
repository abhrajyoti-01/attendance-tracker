from datetime import date, datetime, timedelta
from io import BytesIO
from uuid import UUID

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    PaginationParams,
    get_current_user,
    get_org_admin_user,
    require_api_key_scope,
)
from src.api.metrics import matches_total
from src.api.schemas.attendance import (
    AttendanceExportRequest,
    AttendanceListResponse,
    AttendanceQueryFilters,
    AttendanceResponse,
    AttendanceStatsResponse,
    ManualMarkRequest,
    MarkAttendanceRequest,
)
from src.config import settings
from src.database.models import APIKey, Attendance, Department, User
from src.database.session import get_db
from src.services.attendance_service import VALID_METHODS, mark_attendance
from src.services.audit import record_audit
from src.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

EXPORT_MAX_DAYS = 366


def _row_to_response(record: Attendance, user: User | None, dept_names: dict) -> AttendanceResponse:
    return AttendanceResponse(
        id=record.id,
        user_id=record.user_id,
        user_name=user.name if user else "Unknown",
        user_external_id=user.external_id if user else None,
        timestamp=record.timestamp,
        method=record.method,
        confidence=record.confidence,
        is_spoof=record.is_spoof,
        device_id=record.device_id,
        department_name=(
            dept_names.get(user.department_id) if user is not None and user.department_id else None
        ),
    )


async def _dept_name_map(db: AsyncSession, organization_id) -> dict[UUID, str]:
    result = await db.execute(
        select(Department.id, Department.name).where(Department.organization_id == organization_id)
    )
    return {row[0]: row[1] for row in result.all()}


@router.post("", response_model=AttendanceResponse, status_code=status.HTTP_201_CREATED)
async def mark_attendance_endpoint(
    request: MarkAttendanceRequest,
    api_key: APIKey = Depends(require_api_key_scope("attendance:write")),
    db: AsyncSession = Depends(get_db),
):
    """Machine endpoint - API key with attendance:write scope; scoped to the key's org."""
    user_result = await db.execute(select(User).where(User.id == request.user_id))
    user = user_result.scalar_one_or_none()

    if user is None or not user.is_active or user.organization_id != api_key.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in this organization")

    record, created = await mark_attendance(
        db,
        organization_id=api_key.organization_id,
        user_id=user.id,
        method=request.method,
        confidence=request.confidence,
        device_id=request.device_id,
        metadata=request.metadata,
        is_spoof=request.is_spoof,
    )

    matches_total.labels(result="attendance_marked" if created else "deduplicated").inc()

    return _row_to_response(record, user, {})


@router.post("/manual", status_code=status.HTTP_201_CREATED)
async def manual_mark_attendance(
    request: ManualMarkRequest,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    marked_count = 0
    errors = []
    timestamp = request.timestamp or datetime.now().astimezone()

    for user_id in request.user_ids:
        user_result = await db.execute(
            select(User).where(
                (User.id == user_id) & (User.organization_id == current_user.organization_id)
            )
        )
        user = user_result.scalar_one_or_none()
        if user is None or not user.is_active:
            errors.append({"user_id": str(user_id), "error": "User not found or inactive"})
            continue

        _, created = await mark_attendance(
            db,
            organization_id=current_user.organization_id,
            user_id=user.id,
            method=request.method,
            confidence=None,
            device_id=request.device_id,
            metadata={"note": request.note} if request.note else {},
            timestamp=timestamp,
        )
        if created:
            marked_count += 1

    await record_audit(
        db,
        "attendance.manual_mark",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        details={"marked": marked_count, "requested": len(request.user_ids)},
        commit=False,
    )

    return {
        "message": f"Marked attendance for {marked_count} users",
        "marked_count": marked_count,
        "errors": errors,
    }


def _apply_attendance_filters(query, filters: AttendanceQueryFilters):
    if filters.date_from:
        query = query.where(
            Attendance.timestamp >= datetime.combine(filters.date_from, datetime.min.time())
        )
    if filters.date_to:
        query = query.where(
            Attendance.timestamp
            < datetime.combine(filters.date_to + timedelta(days=1), datetime.min.time())
        )
    if filters.user_id:
        query = query.where(Attendance.user_id == filters.user_id)
    if filters.department_id:
        query = query.where(User.department_id == filters.department_id)
    if filters.method:
        query = query.where(Attendance.method == filters.method)
    if filters.is_spoof is not None:
        query = query.where(Attendance.is_spoof.is_(filters.is_spoof))
    return query


@router.get("", response_model=AttendanceListResponse)
async def query_attendance(
    filters: AttendanceQueryFilters = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    base = (
        select(Attendance, User)
        .outerjoin(User, User.id == Attendance.user_id)
        .where(Attendance.organization_id == current_user.organization_id)
    )

    count_base = (
        select(func.count(Attendance.id))
        .outerjoin(User, User.id == Attendance.user_id)
        .where(Attendance.organization_id == current_user.organization_id)
    )

    filtered = _apply_attendance_filters(base, filters)
    count_filtered = _apply_attendance_filters(count_base, filters)

    total = (await db.execute(count_filtered)).scalar() or 0

    page_query = (
        filtered.order_by(Attendance.timestamp.desc())
        .offset((filters.page - 1) * filters.page_size)
        .limit(filters.page_size)
    )

    rows = (await db.execute(page_query)).all()
    dept_names = await _dept_name_map(db, current_user.organization_id)

    responses = [_row_to_response(att, user, dept_names) for att, user in rows]

    return AttendanceListResponse(
        records=responses,
        total=total,
        page=filters.page,
        page_size=filters.page_size,
    )


@router.get("/today", response_model=AttendanceListResponse)
async def get_today_attendance(
    pagination: PaginationParams = Depends(),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today_start = datetime.combine(date.today(), datetime.min.time())

    count_q = select(func.count(Attendance.id)).where(
        (Attendance.organization_id == current_user.organization_id)
        & (Attendance.timestamp >= today_start)
    )
    total = (await db.execute(count_q)).scalar() or 0

    q = (
        select(Attendance, User)
        .outerjoin(User, User.id == Attendance.user_id)
        .where(
            (Attendance.organization_id == current_user.organization_id)
            & (Attendance.timestamp >= today_start)
        )
        .order_by(Attendance.timestamp.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    rows = (await db.execute(q)).all()
    dept_names = await _dept_name_map(db, current_user.organization_id)

    return AttendanceListResponse(
        records=[_row_to_response(att, user, dept_names) for att, user in rows],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/stats", response_model=AttendanceStatsResponse)
async def get_attendance_stats(
    date_from: date | None = None,
    date_to: date | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    date_from = date_from or date.today()
    date_to = min(date_to or date.today(), date.today())
    start_dt = datetime.combine(date_from, datetime.min.time())
    end_dt = datetime.combine(date_to + timedelta(days=1), datetime.min.time())

    scope = (
        (Attendance.organization_id == current_user.organization_id)
        & (Attendance.timestamp >= start_dt)
        & (Attendance.timestamp < end_dt)
    )

    totals_row = (
        await db.execute(
            select(
                func.count(Attendance.id),
                func.count(func.distinct(Attendance.user_id)),
                func.count(Attendance.id).filter(Attendance.is_spoof.is_(True)),
            ).where(scope)
        )
    ).one()
    total_records, unique_users, spoof_attempts = (
        int(totals_row[0]),
        int(totals_row[1]),
        int(totals_row[2]),
    )

    by_method_rows = (
        await db.execute(
            select(Attendance.method, func.count(Attendance.id))
            .where(scope & (Attendance.is_spoof.is_(False)))
            .group_by(Attendance.method)
        )
    ).all()
    by_method = {row[0]: row[1] for row in by_method_rows}

    by_department_rows = (
        await db.execute(
            select(Department.name, func.count(Attendance.id))
            .select_from(Attendance)
            .join(User, User.id == Attendance.user_id)
            .join(Department, Department.id == User.department_id)
            .where(scope & (Attendance.is_spoof.is_(False)))
            .group_by(Department.name)
        )
    ).all()
    by_department = {row[0]: row[1] for row in by_department_rows}

    active_users = (
        await db.execute(
            select(func.count(User.id)).where(
                (User.organization_id == current_user.organization_id) & User.is_active.is_(True)
            )
        )
    ).scalar() or 0

    present_unique = (
        await db.execute(
            select(func.count(func.distinct(Attendance.user_id))).where(
                scope & (Attendance.is_spoof.is_(False))
            )
        )
    ).scalar() or 0

    return AttendanceStatsResponse(
        date=date_to,
        total_records=total_records,
        unique_users=unique_users,
        present_users=present_unique,
        absent_users=max(0, active_users - present_unique),
        by_department=by_department,
        by_method=by_method,
        spoof_attempts=spoof_attempts,
    )


def _build_export_dataframe(rows, include_metadata: bool) -> pd.DataFrame:
    data = [
        {
            "ID": str(att.id),
            "User": user.name if user else "Unknown",
            "External ID": user.external_id if user else "",
            "Department": "",
            "Timestamp": att.timestamp.isoformat(),
            "Method": att.method,
            "Confidence": f"{att.confidence:.3f}" if att.confidence is not None else "",
            "Device": att.device_id or "",
            "Is Spoof": "Yes" if att.is_spoof else "No",
            **({"Metadata": str(att.metadata_)} if include_metadata else {}),
        }
        for att, user in ((row[0], row[1]) for row in rows)
    ]
    df = pd.DataFrame(data)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "ID",
                "User",
                "External ID",
                "Department",
                "Timestamp",
                "Method",
                "Confidence",
                "Device",
                "Is Spoof",
            ]
        )
    return df


@router.post("/export")
async def export_attendance(
    request: AttendanceExportRequest,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Synchronous export for moderate ranges; async Celery variant handles large ones."""
    if request.format not in ("csv", "excel"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Unsupported format. Use 'csv' or 'excel'"
        )

    span_days = (request.date_to - request.date_from).days + 1
    if span_days > EXPORT_MAX_DAYS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Range exceeds {EXPORT_MAX_DAYS} days; use the asynchronous export job instead",
        )

    start_dt = datetime.combine(request.date_from, datetime.min.time())
    end_dt = datetime.combine(request.date_to + timedelta(days=1), datetime.min.time())

    query = (
        select(Attendance, User)
        .outerjoin(User, User.id == Attendance.user_id)
        .where(
            (Attendance.organization_id == current_user.organization_id)
            & (Attendance.timestamp >= start_dt)
            & (Attendance.timestamp < end_dt)
        )
        .order_by(Attendance.timestamp.desc())
    )

    if request.department_id:
        query = query.where(User.department_id == request.department_id)

    rows = (await db.execute(query)).all()

    if len(rows) > 50_000:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Result too large for synchronous export; use the asynchronous export job",
        )

    df = _build_export_dataframe(rows, request.include_metadata)

    output = BytesIO()
    if request.format == "csv":
        df.to_csv(output, index=False)
        media_type = "text/csv"
        filename = f"attendance_{request.date_from}_{request.date_to}.csv"
    else:
        df.to_excel(output, index=False, engine="openpyxl")
        media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = f"attendance_{request.date_from}_{request.date_to}.xlsx"
    output.seek(0)

    await record_audit(
        db,
        "attendance.exported",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        details={
            "format": request.format,
            "rows": len(df),
            "range": [str(request.date_from), str(request.date_to)],
        },
        commit=False,
    )

    logger.info("Attendance exported", rows=len(df), format=request.format, by=str(current_user.id))

    return StreamingResponse(
        output,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/async", status_code=status.HTTP_202_ACCEPTED)
async def export_attendance_async(
    request: AttendanceExportRequest,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Queue a large export; result lands in object storage behind a presigned URL."""
    from src.services import storage as storage_service

    if not (settings.features.enable_async_export and storage_service.is_available()):
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail="Async export disabled")

    from src.workers.tasks.export_tasks import generate_export_task

    task = generate_export_task.delay(
        str(current_user.organization_id),
        request.date_from.isoformat(),
        request.date_to.isoformat(),
        request.format,
        request.include_metadata,
    )

    await record_audit(
        db,
        "attendance.export_queued",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        details={"task_id": task.id},
        commit=False,
    )

    return {"task_id": task.id, "status": "queued"}


@router.get("/methods", response_model=list[str])
async def list_methods(_: User = Depends(get_current_user)):
    return list(VALID_METHODS)
