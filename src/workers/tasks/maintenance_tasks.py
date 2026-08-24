"""Periodic maintenance tasks run by Celery beat."""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from src.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task
def cleanup_expired_tokens_task(retention_days: int = 30):
    """Delete consumed/expired password-reset and revoked refresh tokens past retention."""
    from sqlalchemy import delete, or_

    from src.database.models import PasswordResetToken, RefreshTokenRecord
    from src.database.session import get_db_context

    async def _cleanup():
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with get_db_context() as db:
            reset_result = await db.execute(
                delete(PasswordResetToken).where(
                    or_(
                        PasswordResetToken.expires_at < cutoff,
                        PasswordResetToken.used_at.isnot(None)
                        & (PasswordResetToken.used_at < cutoff),
                    )
                )
            )
            refresh_result = await db.execute(
                delete(RefreshTokenRecord).where(
                    or_(
                        RefreshTokenRecord.expires_at < cutoff,
                        RefreshTokenRecord.revoked_at.isnot(None)
                        & (RefreshTokenRecord.revoked_at < cutoff),
                    )
                )
            )
            await db.commit()
            return {
                "success": True,
                "password_reset_tokens_deleted": reset_result.rowcount,
                "refresh_tokens_deleted": refresh_result.rowcount,
            }

    try:
        result = asyncio.run(_cleanup())
        logger.info("Token cleanup complete", **result)
        return result
    except Exception as exc:
        logger.exception("Token cleanup failed")
        return {"success": False, "error": str(exc)}


@celery_app.task
def purge_old_spoof_attempts_task(retention_days: int = 90):
    """Delete spoof-attempt records older than the retention window."""
    from sqlalchemy import delete

    from src.database.models import SpoofAttempt
    from src.database.session import get_db_context

    async def _purge():
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with get_db_context() as db:
            result = await db.execute(delete(SpoofAttempt).where(SpoofAttempt.timestamp < cutoff))
            await db.commit()
            return {"success": True, "spoof_attempts_deleted": result.rowcount}

    try:
        result = asyncio.run(_purge())
        logger.info("Spoof attempt purge complete", **result)
        return result
    except Exception as exc:
        logger.exception("Spoof attempt purge failed")
        return {"success": False, "error": str(exc)}


@celery_app.task
def refresh_matcher_indexes_task():
    """Periodically rebuild in-memory matcher indexes to pick up external changes."""
    from src.inference.index_manager import matcher_registry

    try:
        asyncio.run(matcher_registry.refresh_all())
        return {"success": True}
    except Exception as exc:
        logger.exception("Matcher refresh failed")
        return {"success": False, "error": str(exc)}
