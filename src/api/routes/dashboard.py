import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_current_user,
    get_org_admin_user,
    get_org_admin_user_short_lived,
)
from src.api.schemas.dashboard import (
    AttendanceFeedEvent,
    DailyAttendanceChartResponse,
    DailyChartPoint,
    DashboardSummaryResponse,
    DepartmentChartResponse,
    DepartmentStats,
    HourlyAttendancePoint,
    HourlyChartResponse,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdate,
)
from src.config import settings
from src.database.models import Attendance, Department, Embedding, Organization, SpoofAttempt, User
from src.database.session import get_db, get_db_context
from src.services.audit import record_audit
from src.services.events import subscribe_attendance_events
from src.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("/summary", response_model=DashboardSummaryResponse)
async def get_dashboard_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    org_id = current_user.organization_id

    user_counts = (
        await db.execute(
            select(
                func.count(User.id),
                func.count(User.id).filter(User.is_active.is_(True)),
            ).where(User.organization_id == org_id)
        )
    ).one()
    total_users, active_users = int(user_counts[0]), int(user_counts[1])

    registered_users = (
        await db.execute(
            select(func.count(Embedding.user_id))
            .select_from(Embedding)
            .join(User, User.id == Embedding.user_id)
            .where(User.organization_id == org_id)
        )
    ).scalar() or 0

    day_scope = (
        (Attendance.organization_id == org_id)
        & (func.date(Attendance.timestamp) == today)
        & (Attendance.is_spoof.is_(False))
    )

    total_attendance_today = (
        await db.execute(select(func.count(Attendance.id)).where(day_scope))
    ).scalar() or 0

    present_today = (
        await db.execute(select(func.count(func.distinct(Attendance.user_id))).where(day_scope))
    ).scalar() or 0

    spoof_attempts_today = (
        await db.execute(select(func.count(SpoofAttempt.id)).where(spoof_scope(org_id, today)))
    ).scalar() or 0

    return DashboardSummaryResponse(
        total_users=total_users,
        registered_users=registered_users,
        unregistered_users=max(0, total_users - registered_users),
        active_users=active_users,
        present_today=present_today,
        absent_today=max(0, active_users - present_today),
        total_attendance_today=total_attendance_today,
        spoof_attempts_today=spoof_attempts_today,
        last_updated=datetime.now().astimezone(),
    )


def spoof_scope(org_id: UUID, today: date):
    return (SpoofAttempt.organization_id == org_id) & (func.date(SpoofAttempt.timestamp) == today)


@router.get("/chart/daily", response_model=DailyAttendanceChartResponse)
async def get_daily_chart(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    rows = (
        await db.execute(
            select(
                func.date(Attendance.timestamp).label("day"),
                func.count(func.distinct(Attendance.user_id)).label("present"),
                func.count(Attendance.id).label("total"),
            )
            .where(
                (Attendance.organization_id == current_user.organization_id)
                & (Attendance.is_spoof.is_(False))
                & (func.date(Attendance.timestamp) >= start_date)
                & (func.date(Attendance.timestamp) <= end_date)
            )
            .group_by("day")
            .order_by("day")
        )
    ).all()

    by_date = {row[0]: row for row in rows}

    active_users = (
        await db.execute(
            select(func.count(User.id)).where(
                (User.organization_id == current_user.organization_id) & User.is_active.is_(True)
            )
        )
    ).scalar() or 0

    data = []
    for offset in range(days):
        day = start_date + timedelta(days=offset)
        row = by_date.get(day)
        present = int(row[1]) if row else 0
        total = int(row[2]) if row else 0
        data.append(
            DailyChartPoint(
                date=day,
                present=present,
                absent=max(0, active_users - present),
                total=total,
            )
        )

    return DailyAttendanceChartResponse(data=data, start_date=start_date, end_date=end_date)


@router.get("/chart/department", response_model=DepartmentChartResponse)
async def get_department_chart(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()
    org_id = current_user.organization_id

    dept_counts = (
        await db.execute(
            select(Department.id, Department.name, func.count(User.id))
            .outerjoin(User, User.department_id == Department.id)
            .where(Department.organization_id == org_id)
            .group_by(Department.id, Department.name)
            .order_by(Department.name)
        )
    ).all()

    present_rows = (
        await db.execute(
            select(
                User.department_id,
                func.count(func.distinct(Attendance.user_id)),
            )
            .select_from(Attendance)
            .join(User, User.id == Attendance.user_id)
            .where(
                (Attendance.organization_id == org_id)
                & (Attendance.is_spoof.is_(False))
                & (User.department_id.isnot(None))
                & (func.date(Attendance.timestamp) == today)
            )
            .group_by(User.department_id)
        )
    ).all()
    present_by_dept = {row[0]: row[1] for row in present_rows}

    data = [
        DepartmentStats(
            department_id=dept_id,
            department_name=name,
            total_users=int(count),
            present_today=present_by_dept.get(dept_id, 0),
            absent_today=max(0, int(count) - present_by_dept.get(dept_id, 0)),
            attendance_rate=round(present_by_dept.get(dept_id, 0) / count, 4) if count else 0.0,
        )
        for dept_id, name, count in dept_counts
    ]

    return DepartmentChartResponse(data=data, total_departments=len(data))


@router.get("/chart/hours", response_model=HourlyChartResponse)
async def get_hourly_chart(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    today = date.today()

    rows = (
        await db.execute(
            select(
                func.extract("hour", Attendance.timestamp).label("hour"),
                func.count(Attendance.id).label("count"),
            )
            .where(
                (Attendance.organization_id == current_user.organization_id)
                & (Attendance.is_spoof.is_(False))
                & (func.date(Attendance.timestamp) == today)
            )
            .group_by("hour")
        )
    ).all()

    counts = {int(row[0]): int(row[1]) for row in rows if 0 <= int(row[0]) < 24}
    data = [HourlyAttendancePoint(hour=h, check_ins=counts.get(h, 0)) for h in range(24)]

    return HourlyChartResponse(data=data, date=today)


@router.get("/feed")
async def get_attendance_feed(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(Attendance, User.name, User.external_id, User.department_id)
        .outerjoin(User, User.id == Attendance.user_id)
        .where(Attendance.organization_id == current_user.organization_id)
        .order_by(Attendance.timestamp.desc())
        .limit(limit)
    )
    rows = (await db.execute(q)).all()

    dept_ids = {row[3] for row in rows if row[3]}
    dept_names: dict[UUID, str] = {}
    if dept_ids:
        dept_rows = (
            await db.execute(
                select(Department.id, Department.name).where(Department.id.in_(dept_ids))
            )
        ).all()
        dept_names = {row[0]: row[1] for row in dept_rows}

    events = [
        AttendanceFeedEvent(
            id=str(att.id),
            user_id=att.user_id,
            user_name=name or "Unknown",
            user_external_id=external_id,
            timestamp=att.timestamp,
            method=att.method,
            confidence=att.confidence,
            is_spoof=att.is_spoof,
            department_name=dept_names.get(dept_id) if dept_id else None,
        )
        for att, name, external_id, dept_id in rows
    ]

    return {"events": events}


DEFAULT_ORG_SETTINGS_RESPONSE: dict[str, Any] = {
    "working_hours_start": "09:00",
    "working_hours_end": "17:00",
    "allow_late_checkin": True,
    "recognition_threshold": 0.55,
    "require_liveness_check": True,
    "liveness_threshold": 0.67,
}


def _settings_response(org: Organization) -> OrganizationSettingsResponse:
    merged = DEFAULT_ORG_SETTINGS_RESPONSE | (org.settings or {})
    merged["max_users"] = org.max_users
    return OrganizationSettingsResponse(**merged)


@router.get("/settings", response_model=OrganizationSettingsResponse)
async def get_organization_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Organization not found")
    return _settings_response(org)


@router.patch("/settings", response_model=OrganizationSettingsResponse)
async def update_organization_settings(
    settings_update: OrganizationSettingsUpdate,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(Organization.id == current_user.organization_id)
    )
    org = result.scalar_one_or_none()
    if org is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Organization not found")

    current_settings = org.settings or {}
    updates = settings_update.model_dump(exclude_unset=True)

    for key in ("working_hours_start", "working_hours_end"):
        if key in updates:
            try:
                datetime.strptime(updates[key], "%H:%M")
            except ValueError as exc:
                from fastapi import HTTPException

                raise HTTPException(status_code=400, detail=f"{key} must be HH:MM") from exc

    current_settings.update(updates)
    org.settings = current_settings
    await db.commit()
    await db.refresh(org)

    await record_audit(
        db,
        "org.settings_updated",
        organization_id=org.id,
        actor_id=current_user.id,
        details={"keys": sorted(updates.keys())},
        commit=False,
    )
    logger.info(
        "Organization settings updated",
        org_id=str(org.id),
        keys=sorted(updates.keys()),
        updated_by=str(current_user.id),
    )

    return _settings_response(org)


SSE_HEARTBEAT_SECONDS = 15


async def _enrich_live_event(raw: str) -> str | None:
    """Attach display fields (name/department) to a raw attendance event."""
    try:
        payload: dict[str, Any] = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload.get("user_id"), str):
        return None

    async with get_db_context() as db:
        row = (
            await db.execute(
                select(User.name, User.external_id, Department.name)
                .select_from(User)
                .outerjoin(Department, Department.id == User.department_id)
                .where(User.id == UUID(payload["user_id"]))
            )
        ).first()

    payload["user_name"] = row[0] if row and row[0] else "Unknown"
    payload["user_external_id"] = row[1] if row else None
    payload["department_name"] = row[2] if row else None
    return json.dumps(payload, default=str)


@router.get("/stream", summary="Live attendance event stream (Server-Sent Events)")
async def stream_attendance_events(
    request: Request,
    current_user: User = Depends(get_org_admin_user_short_lived),
):
    """Stream real-time attendance events for the caller's organization.

    Admin-only. Emits `data:` JSON events matching the /feed shape plus a
    heartbeat comment every SSE_HEARTBEAT_SECONDS to survive idle proxies.

    Uses the short-lived auth dependency so no pooled DB connection is pinned
    for the lifetime of the stream.
    """
    if not settings.features.enable_live_feed:
        raise HTTPException(status_code=404, detail="Live feed is disabled")

    org_id = str(current_user.organization_id)

    async def generate():
        yield ": connected\n\n"
        source = subscribe_attendance_events(org_id)
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(source.__anext__(), timeout=SSE_HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                except StopAsyncIteration:
                    break
                enriched = await _enrich_live_event(raw)
                if enriched:
                    yield f"data: {enriched}\n\n"
        finally:
            await source.aclose()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
