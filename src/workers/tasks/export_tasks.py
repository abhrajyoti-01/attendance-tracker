"""Asynchronous attendance exports.

The heavy lifting happens in the worker: rows are streamed from PostgreSQL,
materialized into CSV/Excel, uploaded to object storage, and a presigned URL
is stored in the task result for the requester.
"""

import asyncio
from datetime import date, datetime, timedelta
from io import BytesIO

import pandas as pd
import structlog
from sqlalchemy import select

from src.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)

EXPORT_HARD_LIMIT_ROWS = 500_000


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


async def _fetch_rows(org_id, date_from: date, date_to: date):
    from src.database.models import Attendance, Department, User
    from src.database.session import get_db_context

    start_dt = datetime.combine(date_from, datetime.min.time())
    end_dt = datetime.combine(date_to + timedelta(days=1), datetime.min.time())

    async with get_db_context() as db:
        query = (
            select(Attendance, User.name, User.external_id, Department.name)
            .outerjoin(User, User.id == Attendance.user_id)
            .outerjoin(Department, Department.id == User.department_id)
            .where(
                (Attendance.organization_id == org_id)
                & (Attendance.timestamp >= start_dt)
                & (Attendance.timestamp < end_dt)
            )
            .order_by(Attendance.timestamp.desc())
            .limit(EXPORT_HARD_LIMIT_ROWS + 1)
        )
        result = await db.execute(query)
        return result.all()


def _build_dataframe(rows, include_metadata: bool) -> pd.DataFrame:
    data = [
        {
            "ID": str(att.id),
            "User": name or "Unknown",
            "External ID": external_id or "",
            "Department": dept or "",
            "Timestamp": att.timestamp.isoformat(),
            "Method": att.method,
            "Confidence": f"{att.confidence:.3f}" if att.confidence is not None else "",
            "Device": att.device_id or "",
            "Is Spoof": "Yes" if att.is_spoof else "No",
            **({"Metadata": str(att.metadata_)} if include_metadata else {}),
        }
        for att, name, external_id, dept in rows
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


@celery_app.task(bind=True)
def generate_export_task(
    self,
    organization_id: str,
    date_from: str,
    date_to: str,
    fmt: str = "csv",
    include_metadata: bool = False,
):
    """Generate an export file and upload it to object storage.

    Returns a dict containing a presigned download URL valid for a limited time.
    """
    try:
        from src.config import settings as app_settings
        from src.services import storage as storage_service

        if not storage_service.is_available():
            raise RuntimeError("Object storage is not configured")

        parsed_from = _parse_date(date_from) if isinstance(date_from, str) else date_from
        parsed_to = _parse_date(date_to) if isinstance(date_to, str) else date_to
        if fmt not in ("csv", "excel"):
            raise ValueError(f"Unsupported export format: {fmt}")

        rows = asyncio.run(_fetch_rows(organization_id, parsed_from, parsed_to))
        if len(rows) > EXPORT_HARD_LIMIT_ROWS:
            raise ValueError(
                f"Range contains more than {EXPORT_HARD_LIMIT_ROWS} records; narrow the range"
            )

        df = _build_dataframe(rows, include_metadata)

        buffer = BytesIO()
        if fmt == "csv":
            df.to_csv(buffer, index=False)
            content_type = "text/csv"
            suffix = "csv"
        else:
            df.to_excel(buffer, index=False, engine="openpyxl")
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            suffix = "xlsx"

        key = storage_service.export_object_key(organization_id, suffix, datetime.utcnow())
        storage_service.upload_bytes(key, buffer.getvalue(), content_type)

        url = storage_service.presign_get_url(
            f"{app_settings.storage.export_bucket_name}/{key}",
            app_settings.storage.presigned_url_expiry_seconds,
        )

        logger.info(
            "Export generated",
            org_id=organization_id,
            rows=len(df),
            format=fmt,
            task_id=self.request.id,
        )

        return {
            "success": True,
            "org_id": organization_id,
            "records_count": len(df),
            "format": fmt,
            "storage_key": key,
            "download_url": url,
            "url_expires_in_seconds": app_settings.storage.presigned_url_expiry_seconds,
        }

    except Exception as exc:
        logger.exception("Export failed", org_id=organization_id)
        return {"success": False, "org_id": organization_id, "error": str(exc)}
