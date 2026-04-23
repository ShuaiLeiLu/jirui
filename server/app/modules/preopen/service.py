"""
盘前速览聚合服务 —— 真实数据版

数据来源：
  - 热讯榜：AKShare stock_news_main_cx（财新头条）
  - 市场指标：AKShare stock_zt_pool_em（涨停池统计：涨停家数、最高连板、封板率等）
  - 涨停天梯：AKShare stock_zt_pool_em（按连板数排序）
  - 异常波动：AKShare stock_zt_pool_strong_em + stock_zt_pool_dtgc_em（强势股+跌停股中筛选）
  - AI 解读：基于涨停池数据聚合生成
  - 趋势数据：仍为样例（需要历史涨停池数据，后续接入）

所有 AKShare 调用在 client 层带 TTL 缓存。
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from app.integrations.akshare.client import (
    get_industry_boards,
    get_limit_down_pool,
    get_limit_up_pool,
    get_live_news_merged,
    get_strong_pool,
    run_sync,
)
from app.integrations.llm.client import LLMMessage, get_llm_client
from app.modules.preopen.schemas import (
    AiDigest,
    AnomalyItem,
    AnomalyOverview,
    HotNewsItem,
    IndustryBoardItem,
    LimitUpLadderItem,
    MarketIndicator,
    StockRankItem,
    TradingCalendarHint,
    TrendOverview,
    TrendPoint,
    TrendSeries,
)

logger = logging.getLogger(__name__)

# LLM 结果缓存（5 分钟）
_llm_digest_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_LLM_CACHE_TTL = 300  # 5 分钟


def _make_calendar() -> TradingCalendarHint:
    """构建交易日历提示。"""
    today = date.today()
    is_trading = today.weekday() < 5
    return TradingCalendarHint(
        trade_date=today,
        is_trading_day=is_trading,
        notice="非交易日展示最近交易日快照" if not is_trading else "盘前快照数据",
    )


class PreopenService:
    """盘前速览聚合服务 —— 基于 AKShare 真实数据。

    所有方法同步执行，router 层通过 await run_sync(...) 调用。
    """

    # ─────────────── 热讯榜 ───────────────

    def list_hot_news(self) -> list[HotNewsItem]:
        """热讯榜 —— 同花顺 7x24 快讯 + 财联社快讯合并。

        数据质量：真实标题、正文、精确发布时间、原文链接。
        同花顺为主力数据源，财联社为补充，合并去重后按时间倒序。
        """
        live_news = get_live_news_merged()
        now = datetime.now(tz=UTC)

        items: list[HotNewsItem] = []
        for i, raw in enumerate(live_news[:15]):
            news_id = "hn_" + hashlib.md5(raw.title.encode()).hexdigest()[:8]

            # 解析真实发布时间
            try:
                published_at = datetime.fromisoformat(raw.publish_time.replace(" ", "T"))
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=UTC)
            except (ValueError, AttributeError):
                published_at = now - timedelta(minutes=i * 10)

            # 情绪判断：基于标题+内容关键词
            text = raw.title + raw.content
            if any(w in text for w in ("涨", "突破", "新高", "利好", "增长", "上调", "预增")):
                sentiment = "bullish"
            elif any(w in text for w in ("跌", "利空", "风险", "下降", "战争", "制裁", "封锁", "暴跌")):
                sentiment = "bearish"
            else:
                sentiment = "neutral"

            items.append(HotNewsItem(
                news_id=news_id,
                title=raw.title,
                summary=raw.content[:200] if raw.content else raw.title,
                source=raw.source,
                published_at=published_at,
                heat=max(100 - i * 5, 30),
                sentiment=sentiment,
                symbols=[],
                jump_type="news",
                jump_target=raw.url or "/news",
            ))
        return items

    # ─────────────── AI 解读 ───────────────

    def _collect_preopen_data_text(self) -> str:
        """收集盘前相关数据，拼装为文本供 LLM 分析。"""
        pool = get_limit_up_pool()
        strong = get_strong_pool()
        dt_pool = get_limit_down_pool()
        live_news = get_live_news_merged()

        total_zt = len(pool)
        total_dt = len(dt_pool)
        max_consecutive = max((s.consecutive for s in pool), default=0) if pool else 0
        multi_board = sum(1 for s in pool if s.consecutive >= 2)

        industry_counter = Counter(s.industry for s in pool if s.industry)
        top_industries = industry_counter.most_common(5)

        # 涨停个股明细（前 10 只）
        sorted_pool = sorted(pool, key=lambda s: (s.consecutive, s.amount), reverse=True)
        stock_details = "\n".join(
            f"  - {s.name}({s.symbol}) {s.consecutive}连板 行业:{s.industry}"
            for s in sorted_pool[:10]
        ) or "  暂无涨停数据"

        # 最新快讯（前 8 条）
        news_titles = "\n".join(
            f"  - [{n.source}] {n.title}" for n in live_news[:8]
        ) or "  暂无快讯"

        return (
            f"=== 盘前市场快照 ===\n"
            f"涨停: {total_zt} 家 | 跌停: {total_dt} 家 | 强势股: {len(strong)} 家\n"
            f"连板: {multi_board} 家（最高 {max_consecutive} 连板）\n\n"
            f"行业涨停分布:\n"
            + "\n".join(f"  - {ind}: {cnt}家" for ind, cnt in top_industries)
            + f"\n\n涨停龙头:\n{stock_details}"
            + f"\n\n最新快讯:\n{news_titles}"
        )

    def get_ai_digest(self) -> AiDigest:
        """AI 热讯解读模板 —— 基于涨停池数据自动生成摘要。"""
        now = datetime.now(tz=UTC)
        calendar = _make_calendar()
        pool = get_limit_up_pool()

        total_zt = len(pool)
        max_consecutive = max((s.consecutive for s in pool), default=0) if pool else 0
        multi_board = sum(1 for s in pool if s.consecutive >= 2)

        industry_counter = Counter(s.industry for s in pool if s.industry)
        top_industries = industry_counter.most_common(3)
        industry_text = "、".join(f"{ind}" for ind, _ in top_industries) if top_industries else "暂无行业数据"

        if total_zt > 60:
            sentiment = "bullish"
            mood = "偏多"
        elif total_zt < 30:
            sentiment = "bearish"
            mood = "偏空"
        else:
            sentiment = "neutral"
            mood = "中性"

        headline = f"盘前情绪{mood}，涨停 {total_zt} 家，资金主攻 {industry_text}"

        key_points = [
            f"涨停家数 {total_zt}，连板 {multi_board} 家，最高 {max_consecutive} 连板。",
            f"涨停行业集中于：{industry_text}。",
        ]

        strong = get_strong_pool()
        if strong:
            key_points.append(f"强势股池 {len(strong)} 家，{'市场整体偏强' if len(strong) > 150 else '市场强度温和'}。")

        return AiDigest(
            digest_id=f"digest_{calendar.trade_date.isoformat()}",
            headline=headline,
            interval_start=now - timedelta(hours=12),
            interval_end=now,
            generated_at=now,
            sentiment=sentiment,
            key_points=key_points,
        )

    async def generate_ai_digest_with_llm(self) -> AiDigest:
        """盘前 AI 解读 —— 调用 Gemini 生成专业盘前分析。

        流程：
          1. 在线程池中收集 AKShare 真实市场数据
          2. 将数据拼装为 prompt 喂给 Gemini
          3. 解析 LLM 返回的结构化内容
          4. LLM 不可用或返回异常时，直接报错
        """
        # 缓存命中直接返回
        now_mono = time.monotonic()
        if _llm_digest_cache["data"] is not None and now_mono < _llm_digest_cache["expires_at"]:
            return _llm_digest_cache["data"]

        llm = get_llm_client()
        if not llm.is_configured:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM 服务未配置")

        # 1. 收集数据
        preopen_data = await run_sync(self._collect_preopen_data_text)

        # 2. 构建 prompt
        system_prompt = (
            "你是一名专业的 A 股盘前分析师，每天为投资者提供盘前市场解读。"
            "请基于提供的实时市场数据，生成一段盘前解读。\n\n"
            "请严格按以下 JSON 格式返回，不要添加任何其他内容：\n"
            '{\n'
            '  "headline": "一句话盘前概述（不超过 40 字）",\n'
            '  "sentiment": "bullish 或 bearish 或 neutral",\n'
            '  "key_points": ["要点1", "要点2", "要点3", "要点4"]\n'
            '}\n\n'
            "要求：\n"
            "- headline 概括今日盘前核心看点\n"
            "- sentiment 基于数据判断市场情绪偏向\n"
            "- key_points 3-5 个要点，每个不超过 40 字\n"
            "- 分析要有投资参考价值，不要简单复述数字"
        )

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=f"请分析以下盘前数据：\n\n{preopen_data}"),
        ]

        # 3. 调用 Gemini
        try:
            reply = await llm.chat(messages, temperature=0.5, max_tokens=800)
            digest = self._parse_ai_digest_response(reply)
            if digest:
                _llm_digest_cache["data"] = digest
                _llm_digest_cache["expires_at"] = time.monotonic() + _LLM_CACHE_TTL
                return digest
            logger.warning("LLM 盘前解读解析失败")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM 响应解析失败")
        except Exception as e:
            logger.error("Gemini 盘前解读生成失败: %s", e)
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM 服务调用失败") from e

    @staticmethod
    def _parse_ai_digest_response(reply: str) -> AiDigest | None:
        """解析 LLM 返回的 JSON 内容为 AiDigest。"""
        import json as _json
        now = datetime.now(tz=UTC)
        calendar = _make_calendar()

        # 提取 JSON（兼容 markdown 代码块）
        text = reply.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):
                    stripped = stripped[4:].strip()
                if stripped.startswith("{"):
                    text = stripped
                    break

        try:
            data = _json.loads(text)
        except _json.JSONDecodeError:
            logger.warning("盘前解读 JSON 解析失败: %s", text[:200])
            return None

        if not isinstance(data, dict):
            return None

        headline = data.get("headline", "")
        sentiment = data.get("sentiment", "neutral")
        key_points = data.get("key_points", [])

        if not headline or not key_points:
            return None

        if sentiment not in ("bullish", "bearish", "neutral"):
            sentiment = "neutral"

        return AiDigest(
            digest_id=f"digest_{calendar.trade_date.isoformat()}",
            headline=headline,
            interval_start=now - timedelta(hours=12),
            interval_end=now,
            generated_at=now,
            sentiment=sentiment,
            key_points=key_points,
        )

    # ─────────────── 市场指标 ───────────────

    def list_market_indicators(self) -> list[MarketIndicator]:
        """市场指标卡 —— 基于涨停池真实数据统计。"""
        pool = get_limit_up_pool()
        dt_pool = get_limit_down_pool()

        total_zt = len(pool)
        max_consecutive = max((s.consecutive for s in pool), default=0) if pool else 0

        # 封板率：无炸板的涨停数 / 总涨停数
        no_break = sum(1 for s in pool if s.break_count == 0) if pool else 0
        seal_ratio = round(no_break / total_zt * 100, 1) if total_zt else 0.0

        # 连板率：连板数 >= 2 的比例
        multi_board = sum(1 for s in pool if s.consecutive >= 2) if pool else 0
        consecutive_ratio = round(multi_board / total_zt * 100, 1) if total_zt else 0.0

        # 跌停家数
        total_dt = len(dt_pool)

        indicators = [
            MarketIndicator(
                indicator="highest_consecutive_limit_up",
                label="最高连板",
                value=float(max_consecutive),
                unit="板",
                direction="up" if max_consecutive >= 5 else ("down" if max_consecutive <= 2 else "flat"),
                reference=f"涨停 {total_zt} 家",
            ),
            MarketIndicator(
                indicator="limit_up_seal_ratio",
                label="封板率",
                value=seal_ratio,
                unit="%",
                direction="up" if seal_ratio > 70 else ("down" if seal_ratio < 50 else "flat"),
                reference=f"未炸板 {no_break}/{total_zt}",
            ),
            MarketIndicator(
                indicator="consecutive_limit_up_ratio",
                label="连板率",
                value=consecutive_ratio,
                unit="%",
                direction="up" if consecutive_ratio > 25 else "flat",
                reference=f"连板 {multi_board} 家",
            ),
            MarketIndicator(
                indicator="turnover_growth",
                label="涨跌停比",
                value=round(total_zt / max(total_dt, 1), 1),
                unit="倍",
                direction="up" if total_zt > total_dt * 5 else "flat",
                reference=f"涨停 {total_zt} / 跌停 {total_dt}",
            ),
        ]
        return indicators

    # ─────────────── 异常波动 ───────────────

    def get_anomalies(self) -> AnomalyOverview:
        """异常波动概览 —— 从涨停池筛选高换手 + 跌停池。"""
        calendar = _make_calendar()
        pool = get_limit_up_pool()
        dt_pool = get_limit_down_pool()

        # 尾盘异动：涨停池中高换手（> 10%）或多次炸板的
        tail_moves: list[AnomalyItem] = []
        for s in pool:
            if s.turnover_ratio > 10 or s.break_count >= 2:
                tags = []
                if s.consecutive >= 2:
                    tags.append("consecutive_limit_up")
                if s.turnover_ratio > 10:
                    tags.append("high_turnover")
                tail_moves.append(AnomalyItem(
                    symbol=s.symbol,
                    name=s.name,
                    category="tail-session-move",
                    change_pct=s.change_pct,
                    turnover_ratio=s.turnover_ratio,
                    risk_tags=tags or ["high_turnover"],
                    note=f"换手 {s.turnover_ratio:.1f}%，炸板 {s.break_count} 次" if s.break_count else f"换手 {s.turnover_ratio:.1f}%，成交活跃",
                ))
        tail_moves = tail_moves[:5]  # 最多展示 5 条

        # 严重波动：跌停股
        severe: list[AnomalyItem] = []
        for s in dt_pool:
            severe.append(AnomalyItem(
                symbol=s.symbol,
                name=s.name,
                category="severe-volatility",
                change_pct=s.change_pct,
                turnover_ratio=s.turnover_ratio,
                risk_tags=["abnormal_volatility"],
                note=f"跌停，换手率 {s.turnover_ratio:.1f}%",
            ))
        severe = severe[:5]

        return AnomalyOverview(
            calendar=calendar,
            tail_session_moves=tail_moves,
            severe_volatility=severe,
        )

    # ─────────────── 涨停天梯 ───────────────

    def list_limit_up_ladder(self) -> list[LimitUpLadderItem]:
        """涨停天梯 —— 从涨停池中按连板数排序。"""
        pool = get_limit_up_pool()
        # 按连板数降序
        sorted_pool = sorted(pool, key=lambda s: (s.consecutive, s.amount), reverse=True)

        ladder: list[LimitUpLadderItem] = []
        for s in sorted_pool[:20]:
            tags = []
            if s.consecutive >= 3:
                tags.append("consecutive_limit_up")
            if s.turnover_ratio > 10:
                tags.append("high_turnover")
            if not tags:
                tags.append("high_turnover")

            ladder.append(LimitUpLadderItem(
                symbol=s.symbol,
                name=s.name,
                ladder_level=s.consecutive,
                first_seal_time=s.first_seal_time or "",
                final_seal_time=s.last_seal_time or "",
                reason=s.industry or "",
                risk_tags=tags,
            ))
        return ladder

    # ─────────────── 行业板块涨跌 ───────────────

    def list_industry_boards(self) -> list[IndustryBoardItem]:
        """行业板块涨跌 —— 同花顺行业板块实时数据。

        返回约 90 个行业板块，按涨跌幅排序。
        """
        boards = get_industry_boards()
        items = [
            IndustryBoardItem(
                name=b.name,
                change_pct=b.change_pct,
                total_amount=b.total_amount,
                net_inflow=b.net_inflow,
                rise_count=b.rise_count,
                fall_count=b.fall_count,
                leading_stock=b.leading_stock,
                leading_stock_pct=b.leading_stock_pct,
            )
            for b in boards
        ]
        # 按涨跌幅降序
        items.sort(key=lambda x: x.change_pct, reverse=True)
        return items

    # ─────────────── 涨跌榜 ───────────────

    def list_stock_rank(self, direction: str = "up") -> list[StockRankItem]:
        """涨跌榜 —— 强势股（涨）或跌停股（跌）。

        direction: "up" = 涨幅榜（强势股池前 20）, "down" = 跌幅榜（跌停池）
        """
        if direction == "down":
            pool = get_limit_down_pool()
            items = [
                StockRankItem(
                    symbol=s.symbol,
                    name=s.name,
                    change_pct=s.change_pct,
                    price=s.price,
                    amount=s.amount,
                    turnover_ratio=s.turnover_ratio,
                    industry="",
                    reason="跌停",
                )
                for s in pool
            ]
            items.sort(key=lambda x: x.change_pct)
        else:
            pool = get_strong_pool()
            items = [
                StockRankItem(
                    symbol=s.symbol,
                    name=s.name,
                    change_pct=s.change_pct,
                    price=s.price,
                    amount=s.amount,
                    turnover_ratio=s.turnover_ratio,
                    industry="",
                    reason="强势",
                )
                for s in pool[:20]
            ]
            items.sort(key=lambda x: x.change_pct, reverse=True)
        return items

    # ─────────────── 趋势数据 ───────────────

    def get_trends(self) -> TrendOverview:
        """15 日趋势数据。

        注意：AKShare 无法批量获取历史涨停池，此处仍用当日数据 + 模拟历史趋势。
        后续可接入历史数据服务替换。
        """
        calendar = _make_calendar()
        pool = get_limit_up_pool()
        dt_pool = get_limit_down_pool()

        today_zt = len(pool)
        today_dt = len(dt_pool)
        today_multi = sum(1 for s in pool if s.consecutive >= 2)

        points_dates = self._latest_trade_dates(days=15, ref_date=calendar.trade_date)

        # 用当日真实数据作为最后一天，其余模拟
        import random
        random.seed(42)  # 固定种子保证每次一样

        def _make_series(base: int, volatility: int, last_val: int) -> list[int]:
            vals = [base + random.randint(-volatility, volatility) for _ in range(14)]
            vals.append(last_val)
            return vals

        zt_values = _make_series(60, 15, today_zt)
        dt_values = _make_series(8, 4, today_dt)
        multi_values = _make_series(12, 5, today_multi)

        return TrendOverview(
            calendar=calendar,
            window_days=15,
            series=[
                TrendSeries(
                    metric="daily_limit_up_count",
                    label="每日涨停家数",
                    unit="家",
                    points=[TrendPoint(trade_date=d, value=v) for d, v in zip(points_dates, zt_values, strict=True)],
                ),
                TrendSeries(
                    metric="daily_limit_down_count",
                    label="每日跌停家数",
                    unit="家",
                    points=[TrendPoint(trade_date=d, value=v) for d, v in zip(points_dates, dt_values, strict=True)],
                ),
                TrendSeries(
                    metric="consecutive_limit_up_count",
                    label="连板家数",
                    unit="家",
                    points=[TrendPoint(trade_date=d, value=v) for d, v in zip(points_dates, multi_values, strict=True)],
                ),
            ],
        )

    @staticmethod
    def _latest_trade_dates(days: int, ref_date: date | None = None) -> list[date]:
        """返回最近 N 个交易日（简单按工作日回溯）。"""
        dates: list[date] = []
        cursor = ref_date or date.today()
        while len(dates) < days:
            if cursor.weekday() < 5:
                dates.append(cursor)
            cursor -= timedelta(days=1)
        return list(reversed(dates))
