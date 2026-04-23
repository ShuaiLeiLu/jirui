"""
FastAPI 依赖注入

提供：
  - db_session_dependency: 必须成功获取 AsyncSession（适用于数据库就绪的接口）
  - get_optional_session: 尝试获取 AsyncSession，连接失败时返回 None
  - redis_dependency: Redis 客户端
  - settings_dependency: 全局配置
"""
from __future__ import annotations

import logging
import time as _time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.container import AppContainer, get_container

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)


def container_dependency() -> AppContainer:
    return get_container()


async def db_session_dependency(
    container: AppContainer = Depends(container_dependency),
) -> AsyncIterator[AsyncSession]:
    """必须获取数据库 session（数据库不可用则报 500）"""
    async for session in container.session_dependency():
        yield session


_db_ready: bool | None = None
_db_ready_checked_at: float = 0.0
_DB_RETRY_INTERVAL: float = 30.0


async def _check_db_ready() -> bool:
    """检测数据库连通性和 schema 就绪状态（带缓存）"""
    global _db_ready, _db_ready_checked_at
    now = _time.monotonic()

    if _db_ready is True:
        return True
    if _db_ready is False and (now - _db_ready_checked_at) < _DB_RETRY_INTERVAL:
        return False

    from sqlalchemy import text

    _db_ready_checked_at = now
    try:
        container = get_container()
        session_factory = container.database.session_factory
        session = session_factory()
        try:
            await session.execute(text("SELECT id, phone FROM users LIMIT 0"))
            await session.rollback()
            _db_ready = True
            logger.info("数据库连通性检测通过，启用 DB 模式")
            return True
        finally:
            await session.close()
    except Exception:
        _db_ready = False
        logger.debug("数据库不可用或 schema 未迁移，相关接口将返回空结果或明确错误（%.0f 秒后重试）", _DB_RETRY_INTERVAL)
        return False


async def get_optional_session() -> AsyncIterator[AsyncSession | None]:
    """尝试获取数据库 session，连接失败时 yield None。"""
    if not await _check_db_ready():
        yield None
        return

    container = get_container()
    session = container.database.session_factory()
    try:
        yield session
    finally:
        await session.close()


async def redis_dependency(
    container: AppContainer = Depends(container_dependency),
) -> AsyncIterator[Redis]:
    async for client in container.redis_dependency():
        yield client


def settings_dependency() -> Settings:
    return container_dependency().settings
