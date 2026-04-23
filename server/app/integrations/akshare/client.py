"""
AKShare 数据源统一封装

功能：
  - 封装 AKShare 各类接口调用（行情、新闻、涨停池、指数等）
  - 内置 TTL 内存缓存，避免短时间内重复请求外部接口
  - 提供 async 包装函数，在 asyncio 事件循环中通过 run_in_executor 调用
  - 异常安全：网络错误返回空结果，不影响上游服务

可用数据源（已验证网络连通）：
  - 同花顺 7x24 快讯：stock_info_global_ths → 20 条（标题+内容+时间+链接）★主力
  - 财联社快讯：stock_info_global_cls → 20 条（标题+内容+时间）★补充
  - 财经新闻（财新）：stock_news_main_cx → 100 条
  - 个股新闻（东方财富）：stock_news_em → 10 条/股
  - 涨停池（东方财富）：stock_zt_pool_em → 70+ 条
  - 跌停池（东方财富）：stock_zt_pool_dtgc_em
  - 强势股池（东方财富）：stock_zt_pool_strong_em → 200+ 条
  - 炸板池（东方财富）：stock_zt_pool_zbgc_em
  - A 股实时行情（新浪）：stock_zh_a_spot（仅交易时间可用）
  - A 股指数实时（新浪）：stock_zh_index_spot_sina（仅交易时间可用）
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import partial
from threading import Lock
from typing import Any, Callable, TypeVar

import pandas as pd

logger = logging.getLogger(__name__)

# 用于 run_in_executor 的线程池（AKShare 是同步阻塞调用）
_executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="akshare")
T = TypeVar("T")
_akshare_proxy_guard = Lock()
_AKSHARE_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


# ════════════════════════════════════════════════════════════
# 异步包装：将同步 AKShare 调用放到线程池执行
# ════════════════════════════════════════════════════════════

async def run_sync(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """在线程池中执行同步函数，返回异步结果。

    用法：result = await run_sync(get_limit_up_pool, trade_date)
    """
    loop = asyncio.get_running_loop()
    func = partial(fn, *args, **kwargs)
    return await loop.run_in_executor(_executor, func)


@contextmanager
def _without_proxy_env() -> Any:
    """Temporarily remove process-level proxy env vars for AKShare HTTP calls."""
    previous = {key: os.environ.pop(key, None) for key in _AKSHARE_PROXY_ENV_KEYS}
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def call_akshare_api(api_name: str, /, *args: Any, **kwargs: Any) -> Any:
    """Call an AKShare API while bypassing broken shell proxy settings."""
    import akshare as ak

    api = getattr(ak, api_name)
    with _akshare_proxy_guard:
        with _without_proxy_env():
            return api(*args, **kwargs)


# ════════════════════════════════════════════════════════════
# 内存 TTL 缓存
# ════════════════════════════════════════════════════════════

@dataclass
class _CacheEntry:
    """缓存条目：保存数据和过期时间。"""
    data: Any
    expires_at: float  # time.monotonic 时间戳


class TTLCache:
    """简易 TTL 内存缓存，线程安全性由 GIL 保证（单进程场景足够）。"""

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        """获取缓存值，过期返回 None。"""
        entry = self._store.get(key)
        if entry is None or time.monotonic() > entry.expires_at:
            return None
        return entry.data

    def set(self, key: str, data: Any, ttl_seconds: int) -> None:
        """写入缓存。"""
        self._store[key] = _CacheEntry(
            data=data,
            expires_at=time.monotonic() + ttl_seconds,
        )

    def invalidate(self, prefix: str = "") -> None:
        """清除指定前缀的缓存，空前缀则清除全部。"""
        if not prefix:
            self._store.clear()
        else:
            keys_to_remove = [k for k in self._store if k.startswith(prefix)]
            for k in keys_to_remove:
                del self._store[k]


# 全局缓存实例
_cache = TTLCache()
_stock_quotes_lock = Lock()
_stock_quote_locks_guard = Lock()
_stock_quote_locks: dict[str, Lock] = {}

# 缓存 TTL 配置（秒）
CACHE_TTL_REALTIME = 15       # 实时行情：15 秒
CACHE_TTL_NEWS = 120          # 新闻资讯：2 分钟
CACHE_TTL_POOL = 60           # 涨停/跌停/强势池：1 分钟
CACHE_TTL_INDEX = 30          # 指数行情：30 秒


# ════════════════════════════════════════════════════════════
# 数据返回类型（纯 dataclass，不依赖 pydantic）
# ════════════════════════════════════════════════════════════

@dataclass
class StockQuote:
    """个股实时行情快照。"""
    symbol: str          # 代码（如 "300308"，不含交易所前缀）
    name: str            # 名称
    price: float         # 最新价
    change: float        # 涨跌额
    change_pct: float    # 涨跌幅（%）
    open: float          # 今开
    high: float          # 最高
    low: float           # 最低
    prev_close: float    # 昨收
    volume: float        # 成交量（股）
    amount: float        # 成交额（元）
    timestamp: str       # 数据时间戳


@dataclass
class IndexQuote:
    """指数实时行情快照。"""
    code: str            # 指数代码
    name: str            # 指数名称
    price: float         # 最新价
    change: float        # 涨跌额
    change_pct: float    # 涨跌幅（%）
    volume: float        # 成交量
    amount: float        # 成交额


@dataclass
class LiveNewsItem:
    """7x24 实时快讯条目（同花顺/财联社）。"""
    title: str           # 标题
    content: str         # 正文内容
    publish_time: str    # 发布时间（如 "2026-04-19 22:10:55"）
    url: str             # 原文链接
    source: str          # 来源（"同花顺" / "财联社"）


@dataclass
class NewsItem:
    """财经新闻条目（财新）。"""
    tag: str             # 分类标签
    summary: str         # 摘要内容
    url: str             # 原文链接
    source: str = ""     # 来源（如 "财新"）


@dataclass
class StockNewsItem:
    """个股新闻条目。"""
    symbol: str          # 关联股票代码
    title: str           # 新闻标题
    content: str         # 新闻内容
    publish_time: str    # 发布时间
    source: str          # 来源
    url: str             # 原文链接


@dataclass
class LimitUpStock:
    """涨停股信息。"""
    symbol: str          # 代码
    name: str            # 名称
    change_pct: float    # 涨跌幅（%）
    price: float         # 最新价
    amount: float        # 成交额
    turnover_ratio: float  # 换手率
    seal_amount: float   # 封板资金
    first_seal_time: str # 首次封板时间
    last_seal_time: str  # 最后封板时间
    break_count: int     # 炸板次数
    consecutive: int     # 连板数
    industry: str        # 所属行业


@dataclass
class LimitDownStock:
    """跌停股信息。"""
    symbol: str
    name: str
    change_pct: float
    price: float
    amount: float
    turnover_ratio: float


@dataclass
class StrongStock:
    """强势股信息。"""
    symbol: str
    name: str
    change_pct: float
    price: float
    amount: float
    turnover_ratio: float


@dataclass
class IndustryBoard:
    """同花顺行业板块涨跌概况。"""
    name: str             # 板块名称
    change_pct: float     # 涨跌幅（%）
    total_volume: float   # 总成交量（万手）
    total_amount: float   # 总成交额（亿元）
    net_inflow: float     # 净流入（亿元）
    rise_count: int       # 上涨家数
    fall_count: int       # 下跌家数
    leading_stock: str    # 领涨股名称
    leading_stock_pct: float  # 领涨股涨跌幅


# ════════════════════════════════════════════════════════════
# 核心获取函数
# ════════════════════════════════════════════════════════════

def _safe_float(val: Any, default: float = 0.0) -> float:
    """安全转换为 float。"""
    try:
        if pd.isna(val):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    """安全转换为 int。"""
    try:
        if pd.isna(val):
            return default
        return int(val)
    except (ValueError, TypeError):
        return default


def _safe_str(val: Any, default: str = "") -> str:
    """安全转换为 str。"""
    try:
        if pd.isna(val):
            return default
        return str(val).strip()
    except (ValueError, TypeError):
        return default


def _strip_exchange_prefix(code: str) -> str:
    """去掉新浪行情返回的交易所前缀（sh/sz）。"""
    if code.startswith(("sh", "sz", "SH", "SZ")):
        return code[2:]
    return code


def _stock_quote_cache_key(symbol: str) -> str:
    return f"stock_quote:{symbol}"


def _get_stock_quote_lock(symbol: str) -> Lock:
    with _stock_quote_locks_guard:
        lock = _stock_quote_locks.get(symbol)
        if lock is None:
            lock = Lock()
            _stock_quote_locks[symbol] = lock
        return lock


def _find_quote_in_all_cache(symbol: str) -> StockQuote | None:
    cached = _cache.get("stock_quotes_all")
    if not isinstance(cached, list):
        return None
    for quote in cached:
        if isinstance(quote, StockQuote) and quote.symbol == symbol:
            return quote
    return None


def _peek_stock_quote(symbol: str) -> StockQuote | None:
    cached = _cache.get(_stock_quote_cache_key(symbol))
    if isinstance(cached, StockQuote):
        return cached
    return _find_quote_in_all_cache(symbol)


def _fetch_stock_quote(symbol: str) -> StockQuote | None:
    cached = _peek_stock_quote(symbol)
    if cached is not None:
        return cached

    quote_lock = _get_stock_quote_lock(symbol)
    with quote_lock:
        cached = _peek_stock_quote(symbol)
        if cached is not None:
            return cached

        try:
            df = call_akshare_api("stock_bid_ask_em", symbol=symbol)
        except Exception:
            logger.exception("获取个股实时行情失败：%s", symbol)
            return None

        if df.empty:
            return None

        item_map = {
            _safe_str(row.get("item")): row.get("value")
            for _, row in df.iterrows()
        }
        price = _safe_float(item_map.get("最新"))
        if price <= 0:
            return None

        quote = StockQuote(
            symbol=symbol,
            name=symbol,
            price=price,
            change=_safe_float(item_map.get("涨跌")),
            change_pct=_safe_float(item_map.get("涨幅")),
            open=_safe_float(item_map.get("今开")),
            high=_safe_float(item_map.get("最高")),
            low=_safe_float(item_map.get("最低")),
            prev_close=_safe_float(item_map.get("昨收")),
            volume=_safe_float(item_map.get("总手")),
            amount=_safe_float(item_map.get("金额")),
            timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        _cache.set(_stock_quote_cache_key(symbol), quote, CACHE_TTL_REALTIME)
        return quote


def get_stock_quotes() -> list[StockQuote]:
    """获取 A 股全市场实时行情（新浪数据源）。

    返回约 5500 只股票的实时行情快照。
    数据缓存 15 秒。
    """
    cache_key = "stock_quotes_all"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    # 并发去重：同一时刻只允许一个线程去外部拉全市场行情。
    # 其他请求等待第一个请求完成后直接读缓存，避免 account / positions / stats 并发时重复打满外部源。
    with _stock_quotes_lock:
        cached = _cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            df = call_akshare_api("stock_zh_a_spot")
        except Exception:
            logger.exception("获取 A 股实时行情失败")
            return []

        quotes: list[StockQuote] = []
        for _, row in df.iterrows():
            symbol = _strip_exchange_prefix(_safe_str(row.get("代码")))
            if not symbol:
                continue
            quotes.append(StockQuote(
                symbol=symbol,
                name=_safe_str(row.get("名称")),
                price=_safe_float(row.get("最新价")),
                change=_safe_float(row.get("涨跌额")),
                change_pct=_safe_float(row.get("涨跌幅")),
                open=_safe_float(row.get("今开")),
                high=_safe_float(row.get("最高")),
                low=_safe_float(row.get("最低")),
                prev_close=_safe_float(row.get("昨收")),
                volume=_safe_float(row.get("成交量")),
                amount=_safe_float(row.get("成交额")),
                timestamp=_safe_str(row.get("时间戳")),
            ))

        _cache.set(cache_key, quotes, CACHE_TTL_REALTIME)
        logger.info("获取 A 股实时行情成功：%d 条", len(quotes))
        return quotes


def get_stock_quote_by_symbols(symbols: list[str]) -> dict[str, StockQuote]:
    """按股票代码批量获取行情，返回 {代码: 行情} 字典。

    交易模块只需要当前持仓的少量股票，不应该为此触发全市场行情拉取。
    这里改为按 symbol 拉取，并对每只股票做 15 秒缓存。
    """
    normalized_symbols = sorted({symbol for symbol in symbols if symbol})
    quotes: dict[str, StockQuote] = {}
    for symbol in normalized_symbols:
        quote = _fetch_stock_quote(symbol)
        if quote is not None:
            quotes[symbol] = quote
    return quotes


def peek_stock_quote_by_symbols(symbols: list[str]) -> dict[str, StockQuote]:
    """只从本地缓存读取个股行情，不触发外部行情拉取。"""
    normalized_symbols = sorted({symbol for symbol in symbols if symbol})
    quotes: dict[str, StockQuote] = {}
    for symbol in normalized_symbols:
        quote = _peek_stock_quote(symbol)
        if quote is not None:
            quotes[symbol] = quote
    return quotes


def get_index_quotes() -> list[IndexQuote]:
    """获取 A 股主要指数实时行情（新浪数据源）。

    数据缓存 30 秒。
    """
    cache_key = "index_quotes"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_zh_index_spot_sina")
    except Exception:
        logger.exception("获取指数实时行情失败")
        return []

    quotes: list[IndexQuote] = []
    for _, row in df.iterrows():
        code = _safe_str(row.get("代码"))
        if not code:
            continue
        quotes.append(IndexQuote(
            code=code,
            name=_safe_str(row.get("名称")),
            price=_safe_float(row.get("最新价")),
            change=_safe_float(row.get("涨跌额")),
            change_pct=_safe_float(row.get("涨跌幅")),
            volume=_safe_float(row.get("成交量")),
            amount=_safe_float(row.get("成交额")),
        ))

    _cache.set(cache_key, quotes, CACHE_TTL_INDEX)
    logger.info("获取指数行情成功：%d 条", len(quotes))
    return quotes


def get_main_index_quotes() -> dict[str, IndexQuote]:
    """获取主要指数（上证、深证、创业板）行情。"""
    all_idx = get_index_quotes()
    # 新浪指数代码格式：sh000001（上证综指）、sz399001（深证成指）、sz399006（创业板指）
    target_map = {"sh000001": "上证综指", "sz399001": "深证成指", "sz399006": "创业板指"}
    return {code: q for q in all_idx for code in target_map if q.code == code}


def get_news_main() -> list[NewsItem]:
    """获取财经头条新闻（财新数据源）。

    返回约 100 条最新财经新闻。
    数据缓存 2 分钟。
    """
    cache_key = "news_main"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_news_main_cx")
    except Exception:
        logger.exception("获取财经新闻失败")
        return []

    items: list[NewsItem] = []
    for _, row in df.iterrows():
        items.append(NewsItem(
            tag=_safe_str(row.get("tag")),
            summary=_safe_str(row.get("summary")),
            url=_safe_str(row.get("url")),
            source="财新",
        ))

    _cache.set(cache_key, items, CACHE_TTL_NEWS)
    logger.info("获取财经新闻成功：%d 条", len(items))
    return items


def get_stock_news(symbol: str, limit: int = 20) -> list[StockNewsItem]:
    """获取个股新闻（东方财富数据源）。

    数据缓存 2 分钟（按个股缓存）。
    """
    cache_key = f"stock_news:{symbol}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached[:limit]

    try:
        df = call_akshare_api("stock_news_em", symbol=symbol)
    except Exception:
        logger.exception("获取个股新闻失败：%s", symbol)
        return []

    items: list[StockNewsItem] = []
    for _, row in df.iterrows():
        items.append(StockNewsItem(
            symbol=symbol,
            title=_safe_str(row.get("新闻标题")),
            content=_safe_str(row.get("新闻内容")),
            publish_time=_safe_str(row.get("发布时间")),
            source=_safe_str(row.get("文章来源")),
            url=_safe_str(row.get("新闻链接")),
        ))

    _cache.set(cache_key, items, CACHE_TTL_NEWS)
    logger.info("获取个股新闻成功：%s -> %d 条", symbol, len(items))
    return items[:limit]


def get_live_news_ths() -> list[LiveNewsItem]:
    """获取同花顺 7x24 全球快讯。

    数据质量高：有标题、正文、精确时间和原文链接。
    返回约 20 条最新快讯。
    数据缓存 2 分钟。
    """
    cache_key = "live_news_ths"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_info_global_ths")
    except Exception:
        logger.exception("获取同花顺 7x24 快讯失败")
        return []

    items: list[LiveNewsItem] = []
    for _, row in df.iterrows():
        items.append(LiveNewsItem(
            title=_safe_str(row.get("标题")),
            content=_safe_str(row.get("内容")),
            publish_time=_safe_str(row.get("发布时间")),
            url=_safe_str(row.get("链接")),
            source="同花顺",
        ))

    _cache.set(cache_key, items, CACHE_TTL_NEWS)
    logger.info("获取同花顺 7x24 快讯成功：%d 条", len(items))
    return items


def get_live_news_cls() -> list[LiveNewsItem]:
    """获取财联社全球快讯。

    返回约 20 条最新快讯。
    数据缓存 2 分钟。
    """
    cache_key = "live_news_cls"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_info_global_cls")
    except Exception:
        logger.exception("获取财联社快讯失败")
        return []

    items: list[LiveNewsItem] = []
    for _, row in df.iterrows():
        pub_date = _safe_str(row.get("发布日期"))
        pub_time = _safe_str(row.get("发布时间"))
        publish_time = f"{pub_date} {pub_time}" if pub_date and pub_time else (pub_date or pub_time)
        items.append(LiveNewsItem(
            title=_safe_str(row.get("标题")),
            content=_safe_str(row.get("内容")),
            publish_time=publish_time,
            url="",  # 财联社接口不含链接
            source="财联社",
        ))

    _cache.set(cache_key, items, CACHE_TTL_NEWS)
    logger.info("获取财联社快讯成功：%d 条", len(items))
    return items


def get_live_news_merged() -> list[LiveNewsItem]:
    """合并同花顺 + 财联社快讯，按时间倒序排列。

    同花顺为主力数据源，财联社为补充。
    合并后去重（按标题前 20 字去重）。
    """
    ths = get_live_news_ths()
    cls = get_live_news_cls()

    # 用标题前 20 字做简单去重
    seen: set[str] = set()
    merged: list[LiveNewsItem] = []
    for item in ths + cls:
        key = item.title[:20]
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    # 按发布时间倒序
    merged.sort(key=lambda x: x.publish_time, reverse=True)
    return merged


def get_industry_boards() -> list[IndustryBoard]:
    """获取同花顺行业板块涨跌概况。

    返回约 90 个行业板块的实时涨跌数据。
    数据缓存 1 分钟。
    """
    cache_key = "industry_boards"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_board_industry_summary_ths")
    except Exception:
        logger.exception("获取行业板块涨跌失败")
        return []

    items: list[IndustryBoard] = []
    for _, row in df.iterrows():
        items.append(IndustryBoard(
            name=_safe_str(row.get("板块")),
            change_pct=_safe_float(row.get("涨跌幅")),
            total_volume=_safe_float(row.get("总成交量")),
            total_amount=_safe_float(row.get("总成交额")),
            net_inflow=_safe_float(row.get("净流入")),
            rise_count=_safe_int(row.get("上涨家数")),
            fall_count=_safe_int(row.get("下跌家数")),
            leading_stock=_safe_str(row.get("领涨股")),
            leading_stock_pct=_safe_float(row.get("领涨股-涨跌幅")),
        ))

    _cache.set(cache_key, items, CACHE_TTL_POOL)
    logger.info("获取行业板块成功：%d 个板块", len(items))
    return items


def get_limit_up_pool(trade_date: date | None = None) -> list[LimitUpStock]:
    """获取涨停股池（东方财富数据源）。

    数据缓存 1 分钟。
    """
    dt_str = (trade_date or _latest_trade_date()).strftime("%Y%m%d")
    cache_key = f"limit_up:{dt_str}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_zt_pool_em", date=dt_str)
    except Exception:
        logger.exception("获取涨停池失败：%s", dt_str)
        return []

    items: list[LimitUpStock] = []
    for _, row in df.iterrows():
        items.append(LimitUpStock(
            symbol=_safe_str(row.get("代码")),
            name=_safe_str(row.get("名称")),
            change_pct=_safe_float(row.get("涨跌幅")),
            price=_safe_float(row.get("最新价")),
            amount=_safe_float(row.get("成交额")),
            turnover_ratio=_safe_float(row.get("换手率")),
            seal_amount=_safe_float(row.get("封板资金")),
            first_seal_time=_safe_str(row.get("首次封板时间")),
            last_seal_time=_safe_str(row.get("最后封板时间")),
            break_count=_safe_int(row.get("炸板次数")),
            consecutive=_safe_int(row.get("连板数")),
            industry=_safe_str(row.get("所属行业")),
        ))

    _cache.set(cache_key, items, CACHE_TTL_POOL)
    logger.info("获取涨停池成功：%s -> %d 条", dt_str, len(items))
    return items


def get_limit_down_pool(trade_date: date | None = None) -> list[LimitDownStock]:
    """获取跌停股池（东方财富数据源）。"""
    dt_str = (trade_date or _latest_trade_date()).strftime("%Y%m%d")
    cache_key = f"limit_down:{dt_str}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_zt_pool_dtgc_em", date=dt_str)
    except Exception:
        logger.exception("获取跌停池失败：%s", dt_str)
        return []

    items: list[LimitDownStock] = []
    for _, row in df.iterrows():
        items.append(LimitDownStock(
            symbol=_safe_str(row.get("代码")),
            name=_safe_str(row.get("名称")),
            change_pct=_safe_float(row.get("涨跌幅")),
            price=_safe_float(row.get("最新价")),
            amount=_safe_float(row.get("成交额")),
            turnover_ratio=_safe_float(row.get("换手率")),
        ))

    _cache.set(cache_key, items, CACHE_TTL_POOL)
    return items


def get_strong_pool(trade_date: date | None = None) -> list[StrongStock]:
    """获取强势股池（东方财富数据源）。"""
    dt_str = (trade_date or _latest_trade_date()).strftime("%Y%m%d")
    cache_key = f"strong:{dt_str}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        df = call_akshare_api("stock_zt_pool_strong_em", date=dt_str)
    except Exception:
        logger.exception("获取强势股池失败：%s", dt_str)
        return []

    items: list[StrongStock] = []
    for _, row in df.iterrows():
        items.append(StrongStock(
            symbol=_safe_str(row.get("代码")),
            name=_safe_str(row.get("名称")),
            change_pct=_safe_float(row.get("涨跌幅")),
            price=_safe_float(row.get("最新价")),
            amount=_safe_float(row.get("成交额")),
            turnover_ratio=_safe_float(row.get("换手率")),
        ))

    _cache.set(cache_key, items, CACHE_TTL_POOL)
    return items


# ════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════

def _latest_trade_date() -> date:
    """推算最近一个交易日（简单按工作日回推）。

    注意：不考虑法定假日，仅排除周末。
    实际生产环境应接入交易日历服务。
    """
    from datetime import timedelta
    today = date.today()
    cursor = today
    # 如果是周末或当天还没开盘（时间 < 09:30），回退
    now = datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30):
        cursor -= timedelta(days=1)
    while cursor.weekday() >= 5:  # 周六=5, 周日=6
        cursor -= timedelta(days=1)
    return cursor


def invalidate_cache(prefix: str = "") -> None:
    """手动清除缓存。可选按前缀清除。"""
    _cache.invalidate(prefix)
