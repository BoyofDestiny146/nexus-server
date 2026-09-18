"""Database engine + session dependency."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from .settings import settings


engine = create_async_engine(
    settings.db_url_async,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)

async_session_factory = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a per-request DB session."""
    async with async_session_factory() as session:
        yield session
