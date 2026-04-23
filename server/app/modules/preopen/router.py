"""
盘前速览路由

所有 API 保持原有契约不变，内部改为通过 run_sync 异步调用 AKShare 数据源。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter

from app.integrations.akshare.client import run_sync
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
from app.schemas.common import ApiResponse, ListResponse

# ── 聚合接口缓存 ──
_all_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_ALL_CACHE_TTL = 60  # 60 秒

router = APIRouter(prefix="/preopen", tags=["preopen"])
service = PreopenService()


@router.get("/hot-news")
async def hot_news() -> ApiResponse[ListResponse[HotNewsItem]]:
    items = await run_sync(service.list_hot_news)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/ai-digest")
async def ai_digest() -> ApiResponse[AiDigest]:
    """盘前 AI 解读 —— 仅返回真实 LLM 分析结果。"""
    data = await service.generate_ai_digest_with_llm()
    return ApiResponse(data=data)


@router.get("/market-indicators")
async def market_indicators() -> ApiResponse[ListResponse[MarketIndicator]]:
    items = await run_sync(service.list_market_indicators)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/anomalies")
async def anomalies() -> ApiResponse[AnomalyOverview]:
    data = await run_sync(service.get_anomalies)
    return ApiResponse(data=data)


@router.get("/trends")
async def trends() -> ApiResponse[TrendOverview]:
    data = await run_sync(service.get_trends)
    return ApiResponse(data=data)


@router.get("/limit-up-ladder")
async def limit_up_ladder() -> ApiResponse[ListResponse[LimitUpLadderItem]]:
    items = await run_sync(service.list_limit_up_ladder)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/industry-boards")
async def industry_boards() -> ApiResponse[ListResponse[IndustryBoardItem]]:
    items = await run_sync(service.list_industry_boards)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/stock-rank")
async def stock_rank(direction: str = "up") -> ApiResponse[ListResponse[StockRankItem]]:
    items = await run_sync(service.list_stock_rank, direction=direction)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/all")
async def preopen_all() -> ApiResponse[PreopenAllData]:
    """聚合接口 —— 一次请求返回盘前速览全量数据，前端只需 1 次 HTTP 调用。"""
    now = time.monotonic()
    if _all_cache["data"] is not None and now < _all_cache["expires_at"]:
        return ApiResponse(data=_all_cache["data"])

    # 并发执行所有数据获取
    (
        hot_news_items,
        ai_digest_data,
        indicators,
        anomalies_data,
        trends_data,
        ladder_items,
        boards_items,
        rank_up_items,
        rank_down_items,
    ) = await asyncio.gather(
        run_sync(service.list_hot_news),
        service.generate_ai_digest_with_llm(),
        run_sync(service.list_market_indicators),
        run_sync(service.get_anomalies),
        run_sync(service.get_trends),
        run_sync(service.list_limit_up_ladder),
        run_sync(service.list_industry_boards),
        run_sync(service.list_stock_rank, direction="up"),
        run_sync(service.list_stock_rank, direction="down"),
    )

    data = PreopenAllData(
        hot_news=hot_news_items,
        ai_digest=ai_digest_data,
        market_indicators=indicators,
        anomalies=anomalies_data,
        trends=trends_data,
        limit_up_ladder=ladder_items,
        industry_boards=boards_items,
        stock_rank_up=rank_up_items,
        stock_rank_down=rank_down_items,
    )
    _all_cache["data"] = data
    _all_cache["expires_at"] = time.monotonic() + _ALL_CACHE_TTL
    return ApiResponse(data=data)
