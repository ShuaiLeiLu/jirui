"""Redis-backed stock quote cache for paper trading views."""
from __future__ import annotations

import json
import logging
import asyncio
from dataclasses import asdict
from typing import TYPE_CHECKING

from app.integrations.akshare.client import StockQuote, get_stock_quote_by_symbols, run_sync

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

QUOTE_CACHE_TTL_SECONDS = 180
QUOTE_KEY_PREFIX = "trading:quote:"


def _quote_key(symbol: str) -> str:
    return f"{QUOTE_KEY_PREFIX}{symbol}"


def _quote_from_json(raw: str | None) -> StockQuote | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return StockQuote(**payload)
    except Exception:
        return None


async def get_cached_quotes(redis: Redis, symbols: list[str]) -> dict[str, StockQuote]:
    normalized_symbols = sorted({symbol for symbol in symbols if symbol})
    if not normalized_symbols:
        return {}

    values = await redis.mget([_quote_key(symbol) for symbol in normalized_symbols])
    quotes: dict[str, StockQuote] = {}
    for symbol, raw in zip(normalized_symbols, values, strict=False):
        quote = _quote_from_json(raw)
        if quote is not None:
            quotes[symbol] = quote
    return quotes


async def set_cached_quotes(redis: Redis, quotes: dict[str, StockQuote]) -> None:
    if not quotes:
        return

    pipe = redis.pipeline()
    for symbol, quote in quotes.items():
        pipe.setex(
            _quote_key(symbol),
            QUOTE_CACHE_TTL_SECONDS,
            json.dumps(asdict(quote), ensure_ascii=False),
        )
    await pipe.execute()


async def refresh_cached_quotes(redis: Redis, symbols: list[str]) -> dict[str, StockQuote]:
    normalized_symbols = sorted({symbol for symbol in symbols if symbol})
    if not normalized_symbols:
        return {}

    quotes = await asyncio.wait_for(run_sync(get_stock_quote_by_symbols, normalized_symbols), timeout=45)
    await set_cached_quotes(redis, quotes)
    logger.info("[行情缓存] 刷新 %d/%d 只股票", len(quotes), len(normalized_symbols))
    return quotes


async def get_or_refresh_cached_quotes(redis: Redis, symbols: list[str]) -> dict[str, StockQuote]:
    normalized_symbols = sorted({symbol for symbol in symbols if symbol})
    cached = await get_cached_quotes(redis, normalized_symbols)
    missing_symbols = [symbol for symbol in normalized_symbols if symbol not in cached]
    if not missing_symbols:
        return cached

    refreshed = await refresh_cached_quotes(redis, missing_symbols)
    return {**cached, **refreshed}
