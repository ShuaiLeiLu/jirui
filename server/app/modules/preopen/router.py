"""
盘前速览路由

页面数据接口优先读取 Redis 快照；快照缺失时实时调用 AKShare 聚合兜底。
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import date, datetime, time
from typing import TypeVar
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import db_session_dependency, get_optional_session
from app.core.container import get_container
from app.integrations.openclaw.digest_push import discard_digest_pushes, flush_digest_pushes
from app.modules.preopen.schemas import (
    AiDigest,
    AnomalyOverview,
    HotNewsItem,
    IndustryBoardItem,
    LimitUpLadderItem,
    MarketIndicator,
    PreopenAllData,
    StockRankItem,
    TrendOverview,
)
from app.modules.preopen.service import PreopenService
from app.modules.preopen.service import ai_digest_from_persisted
from app.modules.preopen.skill_service import (
    get_existing_preopen_digest,
    run_preopen_chain,
    stream_preopen_chain,
)
from app.modules.preopen import snapshots
from app.modules.preopen.snapshot_cache import SnapshotSpec, load_snapshot_payload
from app.integrations.akshare.client import run_sync
from app.schemas.common import ApiResponse, ListResponse

logger = logging.getLogger(__name__)
T = TypeVar("T")

router = APIRouter(prefix="/preopen", tags=["preopen"])
service = PreopenService()

_A_SHARE_MARKET_SNAPSHOT_NAMES = {
    snapshots.MARKET_INDICATORS.name,
    snapshots.STOCK_RANK_UP.name,
    snapshots.STOCK_RANK_DOWN.name,
    snapshots.INDUSTRY_BOARDS.name,
    snapshots.LIMIT_UP_LADDER.name,
    snapshots.ANOMALIES.name,
}
_CN_TZ = ZoneInfo("Asia/Shanghai")
_TODAY_SNAPSHOT_START = time(9, 15)


def _requires_today_snapshot(spec: SnapshotSpec[object]) -> bool:
    now = datetime.now(tz=_CN_TZ)
    return (
        spec.name in _A_SHARE_MARKET_SNAPSHOT_NAMES
        and now.weekday() < 5
        and now.time() >= _TODAY_SNAPSHOT_START
    )


def _snapshot_matches_current_session(spec: SnapshotSpec[object], updated_at: datetime) -> bool:
    if not _requires_today_snapshot(spec):
        return True
    return updated_at.astimezone(_CN_TZ).date() == datetime.now(tz=_CN_TZ).date()


async def _fetch_live_or_empty(spec: SnapshotSpec[T], fetch: Callable[[], T] | None) -> T:
    if fetch is None:
        return spec.empty_factory()
    try:
        return await asyncio.wait_for(run_sync(fetch), timeout=45)
    except Exception:
        logger.exception("[盘前速览] 实时数据拉取失败，返回空快照：%s", spec.name)
        return spec.empty_factory()


async def _load_snapshot_or_empty(redis: object, spec: SnapshotSpec[T]) -> T:
    try:
        payload = await load_snapshot_payload(redis, spec)
        return payload.data if payload is not None else spec.empty_factory()
    except Exception:
        logger.info("[盘前速览] 快照不可用，返回空快照：%s", spec.name)
        return spec.empty_factory()


async def _load_or_live(redis: object, spec: SnapshotSpec[T], fetch: Callable[[], T] | None = None) -> T:
    try:
        payload = await load_snapshot_payload(redis, spec)
        if payload is not None and _snapshot_matches_current_session(spec, payload.updated_at):
            return payload.data
    except Exception:
        logger.info("[盘前速览] 快照不可用，尝试实时拉取：%s", spec.name)
    return await _fetch_live_or_empty(spec, fetch)


async def _load_list_or_live(
    redis: object,
    spec: SnapshotSpec[list[T]],
    fetch: Callable[[], list[T]] | None = None,
) -> list[T]:
    return await _load_or_live(redis, spec, fetch)


async def _load_anomalies_or_live(redis: object) -> AnomalyOverview:
    return await _load_or_live(redis, snapshots.ANOMALIES, service.get_anomalies)


async def _load_trends_or_live(redis: object) -> TrendOverview:
    return await _load_snapshot_or_empty(redis, snapshots.TRENDS)


@router.get("/all")
async def preopen_all(
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[PreopenAllData]:
    """聚合接口 —— 一次请求返回盘前速览全量缓存快照。"""
    del session
    redis = get_container().redis.get_client()
    (
        hot_news_items,
        indicator_items,
        anomaly_data,
        trend_data,
        ladder_items,
        board_items,
        rank_up_items,
        rank_down_items,
    ) = await asyncio.gather(
        _load_list_or_live(redis, snapshots.HOT_NEWS, service.list_hot_news),
        _load_list_or_live(redis, snapshots.MARKET_INDICATORS, service.list_market_indicators),
        _load_anomalies_or_live(redis),
        _load_trends_or_live(redis),
        _load_list_or_live(redis, snapshots.LIMIT_UP_LADDER, service.list_limit_up_ladder),
        _load_list_or_live(redis, snapshots.INDUSTRY_BOARDS, service.list_industry_boards),
        _load_list_or_live(redis, snapshots.STOCK_RANK_UP, lambda: service.list_stock_rank("up")),
        _load_list_or_live(redis, snapshots.STOCK_RANK_DOWN, lambda: service.list_stock_rank("down")),
    )
    data = PreopenAllData(
        hot_news=hot_news_items,
        market_indicators=indicator_items,
        anomalies=anomaly_data,
        trends=trend_data,
        limit_up_ladder=ladder_items,
        industry_boards=board_items,
        stock_rank_up=rank_up_items,
        stock_rank_down=rank_down_items,
    )
    return ApiResponse(data=data)


@router.get("/hot-news")
async def hot_news() -> ApiResponse[ListResponse[HotNewsItem]]:
    redis = get_container().redis.get_client()
    items = await _load_list_or_live(redis, snapshots.HOT_NEWS, service.list_hot_news)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/ai-digest")
async def ai_digest(
    session: AsyncSession = Depends(db_session_dependency),
) -> ApiResponse[AiDigest]:
    """盘前 AI 解读 —— 只读取当天已落库结果，不在用户点击时调用 LLM。"""
    digest = await get_existing_preopen_digest(session, date.today())
    if digest is None:
        raise HTTPException(status_code=404, detail="今日盘前 AI 解读尚未生成")
    return ApiResponse(data=ai_digest_from_persisted(digest))


@router.get("/ai-digest-v2/stream")
async def ai_digest_v2_stream(
    session: AsyncSession = Depends(db_session_dependency),
) -> StreamingResponse:
    """盘前 AI 解读 v2 —— SSE 流式输出 skill chain 各阶段事件。

    事件类型:
      - started:整体启动
      - skill_started:某 skill 开始
      - skill_chunk:synthesis 类 skill 流式文本片段
      - skill_completed:某 skill 完成
      - skill_failed:某 skill 失败
      - done:整体完成
      - persisted:digest 已落库
      - error:服务级错误
    """
    return StreamingResponse(
        stream_preopen_chain(session),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/ai-digest-v2")
async def ai_digest_v2(
    session: AsyncSession = Depends(db_session_dependency),
) -> ApiResponse[dict]:
    """盘前 AI 解读 v2 —— 非流式版本(供测试 / 调度任务调用)。"""
    data = await run_preopen_chain(session)
    try:
        await session.commit()
    except Exception:
        discard_digest_pushes(session)
        raise
    await flush_digest_pushes(session)
    return ApiResponse(data=data)


@router.get("/market-indicators")
async def market_indicators() -> ApiResponse[ListResponse[MarketIndicator]]:
    redis = get_container().redis.get_client()
    items = await _load_list_or_live(redis, snapshots.MARKET_INDICATORS, service.list_market_indicators)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/anomalies")
async def anomalies() -> ApiResponse[AnomalyOverview]:
    redis = get_container().redis.get_client()
    data = await _load_anomalies_or_live(redis)
    return ApiResponse(data=data)


@router.get("/trends")
async def trends(
    session: AsyncSession | None = Depends(get_optional_session),
) -> ApiResponse[TrendOverview]:
    del session
    redis = get_container().redis.get_client()
    data = await _load_trends_or_live(redis)
    return ApiResponse(data=data)


@router.get("/limit-up-ladder")
async def limit_up_ladder() -> ApiResponse[ListResponse[LimitUpLadderItem]]:
    redis = get_container().redis.get_client()
    items = await _load_list_or_live(redis, snapshots.LIMIT_UP_LADDER, service.list_limit_up_ladder)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/industry-boards")
async def industry_boards() -> ApiResponse[ListResponse[IndustryBoardItem]]:
    redis = get_container().redis.get_client()
    items = await _load_list_or_live(redis, snapshots.INDUSTRY_BOARDS, service.list_industry_boards)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/stock-rank")
async def stock_rank(direction: str = "up") -> ApiResponse[ListResponse[StockRankItem]]:
    redis = get_container().redis.get_client()
    spec = snapshots.STOCK_RANK_DOWN if direction == "down" else snapshots.STOCK_RANK_UP
    items = await _load_list_or_live(redis, spec, lambda: service.list_stock_rank(direction))
    return ApiResponse(data=ListResponse(items=items, total=len(items)))
