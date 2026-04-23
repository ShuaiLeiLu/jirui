"""
资讯分析路由

所有 API 保持原有契约不变，内部改为通过 run_sync 异步调用 AKShare 数据源。
AKShare 是同步阻塞调用，通过线程池避免阻塞 asyncio 事件循环。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter

from app.integrations.akshare.client import run_sync
from app.modules.news_analysis.schemas import (
    HotNewsRankItem,
    HotStockTag,
    NewsAiPanel,
    NewsAnalysisAllData,
    NewsAnalysisItem,
    NewsFeedCategory,
    StockNewsSummary,
)
from app.modules.news_analysis.service import NewsAnalysisService
from app.schemas.common import ApiResponse, ListResponse

# ── 聚合接口缓存 ──
_all_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_ALL_CACHE_TTL = 60  # 60 秒

router = APIRouter(prefix="/news-analysis", tags=["news-analysis"])
service = NewsAnalysisService()


@router.get("/feed")
async def feed(
    category: NewsFeedCategory = "all",
    important_only: bool = False,
    stock_code: str | None = None,
) -> ApiResponse[ListResponse[NewsAnalysisItem]]:
    items = await run_sync(
        service.list_feed, category=category, important_only=important_only, stock_code=stock_code
    )
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/ai-panels")
async def ai_panels() -> ApiResponse[ListResponse[NewsAiPanel]]:
    """AI 智能分析面板 —— 仅返回真实 LLM 解读结果。"""
    items = await service.generate_ai_panels_with_llm()
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/hot-stocks")
async def hot_stocks() -> ApiResponse[ListResponse[HotStockTag]]:
    items = await run_sync(service.list_hot_stocks)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/hot-news")
async def hot_news() -> ApiResponse[ListResponse[HotNewsRankItem]]:
    items = await run_sync(service.list_hot_news)
    return ApiResponse(data=ListResponse(items=items, total=len(items)))


@router.get("/by-stock/{stock_code}/summary")
async def by_stock_summary(stock_code: str) -> ApiResponse[StockNewsSummary]:
    data = await run_sync(service.get_stock_summary, stock_code=stock_code)
    return ApiResponse(data=data)


@router.get("/all")
async def news_analysis_all() -> ApiResponse[NewsAnalysisAllData]:
    """聚合接口 —— 一次请求返回资讯分析全量数据。"""
    now = time.monotonic()
    if _all_cache["data"] is not None and now < _all_cache["expires_at"]:
        return ApiResponse(data=_all_cache["data"])

    feed_items, ai_panels_items, hot_stocks_items, hot_news_items = await asyncio.gather(
        run_sync(service.list_feed),
        service.generate_ai_panels_with_llm(),
        run_sync(service.list_hot_stocks),
        run_sync(service.list_hot_news),
    )

    data = NewsAnalysisAllData(
        feed=feed_items,
        ai_panels=ai_panels_items,
        hot_stocks=hot_stocks_items,
        hot_news=hot_news_items,
    )
    _all_cache["data"] = data
    _all_cache["expires_at"] = time.monotonic() + _ALL_CACHE_TTL
    return ApiResponse(data=data)
