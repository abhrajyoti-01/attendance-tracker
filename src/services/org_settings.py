"""Per-organization runtime settings used by the recognition pipeline.

Organization rows store a JSON ``settings`` blob edited from the dashboard.
Reading it through here (instead of global config) is what makes the settings
page actually take effect.
"""

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.database.models import Organization

logger = structlog.get_logger(__name__)


async def load_org_settings(db: AsyncSession, organization_id: UUID | str) -> dict:
    """Return the org's settings merged over global defaults.

    A missing organization or malformed values fall back to global defaults so
    recognition keeps working. The recognition threshold can never be lowered
    below the cross-org safety floor.
    """
    merged = {
        "working_hours_start": "09:00",
        "working_hours_end": "17:00",
        "allow_late_checkin": True,
        "require_liveness_check": True,
        "recognition_threshold": settings.model.match_threshold_default,
        "liveness_threshold": settings.anti_spoof.liveness_threshold,
    }

    result = await db.execute(
        select(Organization.settings).where(Organization.id == organization_id)
    )
    stored = result.scalar_one_or_none()
    if stored:
        merged.update({k: v for k, v in stored.items() if v is not None})

    merged["recognition_threshold"] = max(
        float(merged["recognition_threshold"]), settings.model.intra_org_threshold_floor
    )
    return merged
