from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from nexus.config import settings


@lru_cache
def get_engine() -> AsyncEngine:
    kwargs: dict = {"echo": settings.db_echo, "future": True, "pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs.pop("pool_pre_ping")
    else:
        kwargs |= {"pool_size": 10, "max_overflow": 20, "pool_recycle": 1800}
    return create_async_engine(settings.database_url, **kwargs)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency."""
    async with get_sessionmaker()() as session:
        yield session


async def create_all() -> None:
    """Dev/test convenience. Production uses Alembic."""
    from nexus.db import models  # noqa: F401
    from nexus.db.base import Base

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
