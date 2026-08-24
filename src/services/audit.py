from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models import AuditLog

logger = structlog.get_logger(__name__)


async def record_audit(
    db: AsyncSession,
    action: str,
    *,
    organization_id: UUID | str | None = None,
    actor_id: UUID | str | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    details: dict | None = None,
    ip_address: str | None = None,
    commit: bool = False,
) -> None:
    """Persist an audit trail entry on the caller's session.

    The row is flushed with the caller's transaction so business changes and
    their audit entries commit atomically. Pass commit=True for standalone
    events such as failed logins where no other write occurs.
    """
    entry = AuditLog(
        organization_id=organization_id,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
        ip_address=ip_address,
    )
    try:
        db.add(entry)
        if commit:
            await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to persist audit entry", action=action)


def redact_pii(details: dict) -> dict:
    """Shallow copy of details safe for logs - masks obvious secrets."""
    sensitive_keys = {"password", "new_password", "current_password", "token", "secret"}
    return {
        k: ("***" if any(s in k.lower() for s in sensitive_keys) else v) for k, v in details.items()
    }
