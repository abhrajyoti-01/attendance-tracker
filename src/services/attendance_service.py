from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.metrics import attendance_events_total
from src.config import settings
from src.database.models import Attendance

logger = structlog.get_logger(__name__)

VALID_METHODS = ("auto", "manual", "kiosk")


async def find_recent_attendance(
    db: AsyncSession,
    *,
    user_id: UUID,
    window_start: datetime,
) -> Attendance | None:
    result = await db.execute(
        select(Attendance)
        .where(
            and_(
                Attendance.user_id == user_id,
                Attendance.is_spoof.is_(False),
                Attendance.timestamp >= window_start,
            )
        )
        .order_by(Attendance.timestamp.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def mark_attendance(
    db: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    method: str = "auto",
    confidence: float | None = None,
    device_id: str | None = None,
    metadata: dict | None = None,
    timestamp: datetime | None = None,
    is_spoof: bool = False,
    dedupe_window_minutes: int | None = None,
) -> tuple[Attendance | None, bool]:
    """Record attendance with duplicate suppression inside the dedupe window.

    Returns (record, created). When suppressed, returns (most_recent_record, False).
    """
    if method not in VALID_METHODS:
        raise ValueError(f"Invalid attendance method: {method}")

    now = datetime.now(UTC)
    ts = timestamp or now
    window_minutes = (
        dedupe_window_minutes
        if dedupe_window_minutes is not None
        else settings.org.attendance_dedupe_window_minutes
    )

    recent = None
    if not is_spoof and window_minutes > 0:
        from datetime import timedelta

        recent = await find_recent_attendance(
            db,
            user_id=user_id,
            window_start=now - timedelta(minutes=window_minutes),
        )
        if recent is not None:
            attendance_events_total.labels(method=method, result="deduplicated").inc()
            return recent, False

    record = Attendance(
        organization_id=organization_id,
        user_id=user_id,
        timestamp=ts,
        method=method,
        confidence=confidence,
        is_spoof=is_spoof,
        device_id=device_id,
        metadata_=metadata or {},
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    attendance_events_total.labels(
        method=method, result="spoof_blocked" if is_spoof else "marked"
    ).inc()
    logger.info(
        "Attendance marked",
        user_id=str(user_id),
        org_id=str(organization_id),
        method=method,
        is_spoof=is_spoof,
    )

    from src.services.events import publish_attendance_event

    await publish_attendance_event(
        str(organization_id),
        {
            "id": str(record.id),
            "user_id": str(user_id),
            "timestamp": record.timestamp.isoformat(),
            "method": method,
            "confidence": confidence,
            "is_spoof": is_spoof,
            "device_id": device_id,
        },
    )
    return record, True


async def count_today_present(db: AsyncSession, *, organization_id: UUID) -> int:
    today_utc = datetime.now(UTC).date()
    result = await db.execute(
        select(func.count(func.distinct(Attendance.user_id))).where(
            and_(
                Attendance.organization_id == organization_id,
                Attendance.is_spoof.is_(False),
                func.date(Attendance.timestamp) == today_utc,
            )
        )
    )
    return result.scalar() or 0
