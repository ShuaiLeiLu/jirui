from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.database_factory import DatabaseFactory
from app.core.redis_factory import RedisFactory

if TYPE_CHECKING:
    from redis.asyncio import Redis


class AppContainer:
    """Application-level dependency container."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.database = DatabaseFactory(settings=settings)
        self.redis = RedisFactory(settings=settings)

    async def startup(self) -> None:
        self.database.initialize()
        # 预热数据库连接池：启动时建立首个连接，避免首次请求等待
        try:
            async with self.database.session_factory() as session:
                from sqlalchemy import text
                await session.execute(text("SELECT 1"))
        except Exception:
            pass  # 数据库不可用时静默，由 get_optional_session 统一处理

    async def shutdown(self) -> None:
        await self.database.shutdown()
        await self.redis.shutdown()

    async def session_dependency(self) -> AsyncIterator[AsyncSession]:
        async for session in self.database.session_dependency():
            yield session

    async def redis_dependency(self) -> AsyncIterator[Redis]:
        async for client in self.redis.redis_dependency():
            yield client


@lru_cache(maxsize=1)
def get_container() -> AppContainer:
    return AppContainer(settings=get_settings())
