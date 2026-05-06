"""Background refresh jobs for Redis-backed preopen snapshots."""
from __future__ import annotations

import logging
import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime as dt
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

from app.core.redis_factory import RedisFactory
from app.integrations.akshare.client import run_sync
from app.modules.preopen import snapshots
from app.modules.preopen.service import PreopenService
from app.modules.preopen.snapshot_cache import (
    SnapshotSpec,
    acquire_snapshot_lock,
    load_snapshot,
    release_snapshot_lock,
    save_snapshot,
)

logger = logging.getLogger(__name__)

_TRADING_START = time(9, 15)
_TRADING_END = time(15, 0)


@dataclass(frozen=True)
class RefreshTarget:
    spec: SnapshotSpec[Any]
    fetch: Callable[[PreopenService], Awaitable[Any]]
    min_items: int = 0


@dataclass(frozen=True)
class RefreshGroup:
    name: str
    interval_seconds: int
    trading_hours_only: bool
    targets: tuple[RefreshTarget, ...]


def is_a_share_trading_hours() -> bool:
    now = dt.now(tz=ZoneInfo("Asia/Shanghai"))
    if now.weekday() > 4:
        return False
    return _TRADING_START <= now.time() <= _TRADING_END


async def _run_service_method(service: PreopenService, method_name: str, *args: Any) -> Any:
    method = getattr(service, method_name)
    return await asyncio.wait_for(run_sync(method, *args), timeout=45)


def _has_min_items(data: Any, min_items: int) -> bool:
    if min_items <= 0:
        return True
    if isinstance(data, list):
        return len(data) >= min_items
    return True


async def _refresh_target(redis: Any, service: PreopenService, target: RefreshTarget) -> bool:
    token = await acquire_snapshot_lock(redis, target.spec.name, ttl_seconds=120)
    if token is None:
        logger.debug("[盘前快照] %s 已有刷新任务在执行，跳过", target.spec.name)
        return False

    try:
        data = await target.fetch(service)
        if not _has_min_items(data, target.min_items) and await load_snapshot(redis, target.spec) is not None:
            logger.warning("[盘前快照] %s 本次结果为空，保留上一份快照", target.spec.name)
            return False
        await save_snapshot(redis, target.spec, data)
        logger.info("[盘前快照] %s 刷新完成", target.spec.name)
        return True
    except Exception:
        logger.exception("[盘前快照] %s 刷新失败，保留上一份快照", target.spec.name)
        return False
    finally:
        await release_snapshot_lock(redis, target.spec.name, token)


async def refresh_preopen_group(redis_factory: RedisFactory, group_name: str) -> None:
    group = PREOPEN_REFRESH_GROUPS[group_name]
    if group.trading_hours_only and not is_a_share_trading_hours():
        logger.info("[盘前快照] 当前非交易时段，跳过 %s 刷新", group.name)
        return

    redis = redis_factory.get_client()
    service = PreopenService()
    refreshed = 0
    for target in group.targets:
        if await _refresh_target(redis, service, target):
            refreshed += 1
    logger.info("[盘前快照] 分组 %s 刷新结束：%d/%d", group.name, refreshed, len(group.targets))


async def refresh_all_preopen_groups(redis_factory: RedisFactory) -> None:
    for group in PREOPEN_REFRESH_GROUPS.values():
        await refresh_preopen_group(redis_factory, group.name)


PREOPEN_REFRESH_GROUPS: dict[str, RefreshGroup] = {
    "realtime": RefreshGroup(
        name="realtime",
        interval_seconds=60,
        trading_hours_only=True,
        targets=(
            RefreshTarget(
                spec=snapshots.STOCK_RANK_UP,
                fetch=lambda service: _run_service_method(service, "list_stock_rank", "up"),
                min_items=1,
            ),
            RefreshTarget(
                spec=snapshots.STOCK_RANK_DOWN,
                fetch=lambda service: _run_service_method(service, "list_stock_rank", "down"),
            ),
        ),
    ),
    "hot_news": RefreshGroup(
        name="hot_news",
        interval_seconds=60,
        trading_hours_only=False,
        targets=(
            RefreshTarget(
                spec=snapshots.HOT_NEWS,
                fetch=lambda service: _run_service_method(service, "list_hot_news"),
                min_items=1,
            ),
        ),
    ),
    "industry_boards": RefreshGroup(
        name="industry_boards",
        interval_seconds=60,
        trading_hours_only=True,
        targets=(
            RefreshTarget(
                spec=snapshots.INDUSTRY_BOARDS,
                fetch=lambda service: _run_service_method(service, "list_industry_boards"),
                min_items=1,
            ),
        ),
    ),
    "limit_pool": RefreshGroup(
        name="limit_pool",
        interval_seconds=120,
        trading_hours_only=True,
        targets=(
            RefreshTarget(
                spec=snapshots.MARKET_INDICATORS,
                fetch=lambda service: _run_service_method(service, "list_market_indicators"),
                min_items=1,
            ),
            RefreshTarget(
                spec=snapshots.LIMIT_UP_LADDER,
                fetch=lambda service: _run_service_method(service, "list_limit_up_ladder"),
            ),
            RefreshTarget(
                spec=snapshots.ANOMALIES,
                fetch=lambda service: _run_service_method(service, "get_anomalies"),
            ),
        ),
    ),
    "trends": RefreshGroup(
        name="trends",
        interval_seconds=60 * 60,
        trading_hours_only=True,
        targets=(
            RefreshTarget(
                spec=snapshots.TRENDS,
                fetch=lambda service: _run_service_method(service, "get_trends"),
            ),
        ),
    ),
}
