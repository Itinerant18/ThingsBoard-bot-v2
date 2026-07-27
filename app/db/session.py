from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def build_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict[str, object] = {"pool_pre_ping": True}
    if not settings.database_url.startswith("sqlite"):
        kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_pool_max_overflow)
    return create_async_engine(settings.database_url, **kwargs)


def build_session_factory(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(build_engine(settings), expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
