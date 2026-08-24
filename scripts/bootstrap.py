"""Bootstrap the first organization and superadmin account.

Idempotent: safe to re-run; existing records are left untouched unless --reset-password.

Usage:
    python -m scripts.bootstrap \
        --org-name "Acme Corp" --org-slug acme \
        --admin-email admin@acme.com \
        [--admin-name "System Admin"] \
        [--admin-password '...' | prompted securely if omitted]
"""

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select  # noqa: E402

from src.database.models import Organization, User  # noqa: E402
from src.database.session import get_db_context  # noqa: E402
from src.utils.logger import configure_logging, get_logger  # noqa: E402
from src.utils.security import (  # noqa: E402
    get_password_hash,
    validate_password_strength,
)

configure_logging("INFO", "console")
logger = get_logger("bootstrap")

SLUG_MIN_LENGTH = 2


async def bootstrap(
    *,
    org_name: str,
    org_slug: str,
    admin_email: str,
    admin_name: str,
    admin_password: str,
) -> int:
    async with get_db_context() as db:
        org_result = await db.execute(select(Organization).where(Organization.slug == org_slug))
        org = org_result.scalar_one_or_none()

        created_org = False
        if org is None:
            org = Organization(name=org_name, slug=org_slug, is_active=True)
            db.add(org)
            await db.flush()
            created_org = True
            logger.info("Organization created", slug=org.slug, id=str(org.id))
        else:
            logger.info("Organization already exists", slug=org.slug)

        admin_result = await db.execute(
            select(User).where(
                (User.organization_id == org.id) & (User.email == admin_email.lower())
            )
        )
        admin = admin_result.scalar_one_or_none()

        if admin is not None:
            logger.info(
                "Superadmin already exists",
                email=admin_email,
                id=str(admin.id),
                hint="Use --reset-password to rotate the password",
            )
            if not args.reset_password:
                return 0
            admin.password_hash = get_password_hash(admin_password)
            await db.commit()
            logger.info("Password rotated", email=admin_email)
            return 0

        admin = User(
            organization_id=org.id,
            name=admin_name,
            email=admin_email.lower(),
            role="superadmin",
            password_hash=get_password_hash(admin_password),
            is_active=True,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

        action = "Created" if created_org else "Attached admin to existing"
        logger.info(
            "Bootstrap complete",
            organization=org.slug,
            admin_email=admin.email,
            admin_id=str(admin.id),
            note=action,
        )
    return 0


def main() -> int:
    global args
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-name", required=True)
    parser.add_argument("--org-slug", required=True)
    parser.add_argument("--admin-email", required=True)
    parser.add_argument("--admin-name", default="System Admin")
    parser.add_argument("--admin-password", default=None)
    parser.add_argument(
        "--reset-password",
        action="store_true",
        help="Rotate password when the admin already exists",
    )
    args = parser.parse_args()

    if len(args.org_slug) < SLUG_MIN_LENGTH or not args.org_slug.replace("-", "").isalnum():
        parser.error("--org-slug must be alphanumeric with optional inner hyphens")

    password = args.admin_password or getpass.getpass("Superadmin password: ")
    is_strong, message = validate_password_strength(password)
    if not is_strong:
        print(f"ERROR: {message}", file=sys.stderr)
        return 2

    try:
        return asyncio.run(
            bootstrap(
                org_name=args.org_name,
                org_slug=args.org_slug.strip().lower(),
                admin_email=args.admin_email,
                admin_name=args.admin_name,
                admin_password=password,
            )
        )
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
