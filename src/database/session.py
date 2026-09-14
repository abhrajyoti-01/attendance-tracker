from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import BASE_DIR, settings

if TYPE_CHECKING:
    from alembic.config import Config as AlembicConfig

MIGRATIONS_DIR = BASE_DIR / "migrations"

engine = create_async_engine(
    settings.database.url,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    echo=settings.database.echo,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args={"timeout": settings.database.command_timeout},
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Request-scoped unit of work.

    Commits on clean exit so any rows added without an explicit commit (audit
    entries in particular) are persisted, and rolls back if the handler raised.
    """
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _alembic_config() -> "AlembicConfig":
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


async def _get_applied_revision(conn: AsyncConnection) -> str | None:
    result = await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
    row = result.first()
    return row[0] if row else None


async def _get_head_revision() -> str | None:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config())
    return script.get_current_head()


async def check_schema_current(conn: AsyncConnection) -> bool:
    """True when the database is migrated to the current Alembic head."""
    applied = await _get_applied_revision(conn)
    if applied is None:
        return False
    head = await _get_head_revision()
    return applied == head


async def create_all_for_tests() -> None:
    """Create all tables directly from metadata. Test/dev helper only - never production."""
    from src.database.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    await engine.dispose()
