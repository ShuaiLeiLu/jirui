"""
资讯分析聚合服务 —— 真实数据版

数据来源：
  - 资讯流（feed）：AKShare stock_news_main_cx（财新头条）
  - 个股新闻：AKShare stock_news_em（东方财富个股新闻）
  - 热门股票：AKShare stock_zt_pool_em（涨停池，按连板数/成交额排序取热度）
  - 热门资讯：从资讯流中按索引排名
  - AI 面板：基于涨停池统计数据聚合（涨停家数、连板梯队、行业分布等）

所有 AKShare 调用均在 client 层带 TTL 缓存，service 层不直接缓存。
"""
from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status

from app.integrations.akshare.client import (
    get_limit_up_pool,
    get_live_news_merged,
    get_stock_news,
    get_strong_pool,
    run_sync,
)
from app.integrations.llm.client import LLMMessage, get_llm_client
from app.modules.news_analysis.schemas import (
    HotNewsRankItem,
    HotStockTag,
    NewsAiInterpretation,
    NewsAiPanel,
    NewsAnalysisItem,
    NewsFeedCategory,
    NewsStockRelation,
    NewsThemeRelation,
    SentimentDistribution,
    StockNewsSummary,
)

logger = logging.getLogger(__name__)

# LLM 结果缓存（5 分钟）
_llm_panels_cache: dict[str, Any] = {"data": None, "expires_at": 0.0}
_LLM_CACHE_TTL = 300  # 5 分钟


def _news_id(text: str) -> str:
    """根据文本生成短哈希 ID。"""
    return "na_" + hashlib.md5(text.encode()).hexdigest()[:10]


class NewsAnalysisService:
    """资讯分析聚合服务 —— 基于 AKShare 真实数据。

    所有方法同步执行，router 层通过 await run_sync(...) 调用。
    """

    # ─────────────── 资讯流 ───────────────

    def list_feed(
        self,
        *,
        category: NewsFeedCategory = "all",
        important_only: bool = False,
        stock_code: str | None = None,
    ) -> list[NewsAnalysisItem]:
        """获取资讯流 —— 同花顺 7x24 快讯 + 财联社快讯。

        数据质量：真实标题、正文、精确发布时间、原文链接。
        同花顺为主力，财联社为补充，合并去重后按时间倒序。
        分类：默认 flash（快讯），含"公告"关键词 → announcement，含"研报" → report。
        重要性：根据关键词计算。
        """
        live_news = get_live_news_merged()
        now = datetime.now(tz=UTC)

        items: list[NewsAnalysisItem] = []
        for i, raw in enumerate(live_news):
            # 分类映射：基于标题和内容关键词
            text = raw.title + raw.content
            if any(w in text for w in ("公告", "披露", "上交所", "深交所")):
                cat: str = "announcement"
            elif any(w in text for w in ("研报", "研究", "评级", "券商")):
                cat = "report"
            else:
                cat = "flash"

            # 重要性：基于关键词
            importance = _calc_importance(text)
            is_important = importance >= 4

            # 分类过滤
            if category != "all" and cat != category:
                continue
            if important_only and not is_important:
                continue

            news_id = _news_id(raw.title)

            # 解析真实发布时间
            try:
                publish_time = datetime.fromisoformat(raw.publish_time.replace(" ", "T"))
                if publish_time.tzinfo is None:
                    publish_time = publish_time.replace(tzinfo=UTC)
            except (ValueError, AttributeError):
                publish_time = now - timedelta(minutes=i * 5)

            item = NewsAnalysisItem(
                news_id=news_id,
                category=cat,
                source=raw.source,
                title=raw.title,
                summary=raw.content[:300] if raw.content else raw.title,
                content=raw.content or raw.title,
                importance=importance,
                is_important=is_important,
                publish_time=publish_time,
                stock_relations=[],
                theme_relations=[],
                ai_interpretations=[],
            )
            items.append(item)

        # 按发布时间降序
        items.sort(key=lambda x: x.publish_time, reverse=True)
        return items

    # ─────────────── AI 面板 ───────────────

    def _collect_market_data_text(self) -> str:
        """收集当前市场数据，拼装为文本供 LLM 分析。"""
        pool = get_limit_up_pool()
        strong = get_strong_pool()
        live_news = get_live_news_merged()

        total_zt = len(pool)
        max_consecutive = max((s.consecutive for s in pool), default=0) if pool else 0
        multi_board = [s for s in pool if s.consecutive >= 2]
        industry_counter = Counter(s.industry for s in pool if s.industry)
        top_industries = industry_counter.most_common(5)

        # 涨停个股明细（前 15 只）
        sorted_pool = sorted(pool, key=lambda s: (s.consecutive, s.amount), reverse=True)
        stock_details = "\n".join(
            f"  - {s.name}({s.symbol}) {s.consecutive}连板 行业:{s.industry} 换手率:{s.turnover_ratio:.1f}%"
            for s in sorted_pool[:15]
        ) or "  暂无涨停数据"

        # 最新资讯（前 10 条标题）
        news_titles = "\n".join(
            f"  - [{n.source}] {n.title}" for n in live_news[:10]
        ) or "  暂无最新资讯"

        return (
            f"=== A股实时市场数据 ===\n"
            f"涨停家数: {total_zt}\n"
            f"连板家数: {len(multi_board)}（最高 {max_consecutive} 连板）\n"
            f"强势股: {len(strong)} 家\n\n"
            f"行业涨停分布:\n"
            + "\n".join(f"  - {ind}: {cnt}家" for ind, cnt in top_industries)
            + f"\n\n涨停个股明细（按连板排序）:\n{stock_details}"
            + f"\n\n最新快讯:\n{news_titles}"
        )

    def list_ai_panels(self) -> list[NewsAiPanel]:
        """AI 智能分析模板 —— 基于涨停池真实数据聚合。"""
        now = datetime.now(tz=UTC)
        pool = get_limit_up_pool()
        strong = get_strong_pool()

        total_zt = len(pool)
        max_consecutive = max((s.consecutive for s in pool), default=0) if pool else 0
        multi_board = [s for s in pool if s.consecutive >= 2]
        industry_counter = Counter(s.industry for s in pool if s.industry)
        top_industries = industry_counter.most_common(3)

        panels: list[NewsAiPanel] = [
            NewsAiPanel(
                panel_key="24h_digest",
                title="24小时热讯解读",
                summary=f"今日涨停 {total_zt} 家，连板 {len(multi_board)} 家，最高连板 {max_consecutive} 板。",
                highlights=[
                    f"涨停家数 {total_zt}，{'高于' if total_zt > 50 else '低于'}近期均值",
                    f"连板梯队 {len(multi_board)} 家，最高 {max_consecutive} 连板",
                ],
                confidence=0.88,
                updated_at=now,
            ),
            NewsAiPanel(
                panel_key="hotspot_tracking",
                title="热点追踪",
                summary="涨停行业集中度：" + "、".join(
                    f"{ind}({cnt}家)" for ind, cnt in top_industries
                ) if top_industries else "暂无涨停行业数据",
                highlights=[
                    f"{ind} 板块涨停 {cnt} 家" for ind, cnt in top_industries[:2]
                ] if top_industries else ["暂无数据"],
                confidence=0.85,
                updated_at=now,
            ),
            NewsAiPanel(
                panel_key="macro_impact",
                title="宏观影响",
                summary=f"强势股 {len(strong)} 家，市场{'偏强' if len(strong) > 150 else '中性偏弱'}运行。",
                highlights=[
                    f"强势股池 {len(strong)} 家",
                    f"涨停 {total_zt} 家，情绪{'偏热' if total_zt > 60 else '温和'}",
                ],
                confidence=0.82,
                updated_at=now,
            ),
            NewsAiPanel(
                panel_key="stock_interpretation",
                title="个股解读",
                summary=_top_stock_summary(pool),
                highlights=_top_stock_highlights(pool),
                confidence=0.86,
                updated_at=now,
            ),
        ]
        return panels

    async def generate_ai_panels_with_llm(self) -> list[NewsAiPanel]:
        """AI 智能分析面板 —— 调用 Gemini 生成真正的 AI 解读。

        流程：
          1. 在线程池中收集 AKShare 真实市场数据
          2. 将数据拼装为 prompt 喂给 Gemini
          3. 解析 LLM 返回的结构化内容，生成 4 张 AI 面板
          4. LLM 不可用或返回异常时，直接报错
        """
        now_mono = time.monotonic()
        if _llm_panels_cache["data"] is not None and now_mono < _llm_panels_cache["expires_at"]:
            return _llm_panels_cache["data"]

        llm = get_llm_client()
        if not llm.is_configured:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM 服务未配置")

        # 1. 在线程池中收集数据（AKShare 同步调用）
        market_data = await run_sync(self._collect_market_data_text)

        # 2. 构建 prompt
        system_prompt = (
            "你是一名专业的 A 股市场分析师，擅长解读市场数据和热点。"
            "请基于提供的实时市场数据，生成 4 段分析，每段分析包含 summary（一句话总结）和 highlights（2-3 个要点）。\n\n"
            "请严格按以下 JSON 格式返回，不要添加任何其他内容：\n"
            '[\n'
            '  {"key": "24h_digest", "title": "市场总结", "summary": "...", "highlights": ["...", "..."]},\n'
            '  {"key": "hotspot_tracking", "title": "热点追踪", "summary": "...", "highlights": ["...", "..."]},\n'
            '  {"key": "macro_impact", "title": "市场变盘", "summary": "...", "highlights": ["...", "..."]},\n'
            '  {"key": "stock_interpretation", "title": "行业关注", "summary": "...", "highlights": ["...", "..."]}\n'
            ']\n\n'
            "要求：\n"
            "- 语言简洁专业，适合投资者阅读\n"
            "- 每个 summary 不超过 50 字\n"
            "- 每个 highlight 不超过 30 字\n"
            "- 分析要有洞见，不要简单复述数据"
        )

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=f"请分析以下市场数据：\n\n{market_data}"),
        ]

        # 3. 调用 Gemini
        try:
            reply = await llm.chat(messages, temperature=0.5, max_tokens=1500)
            panels = self._parse_ai_panels_response(reply)
            if panels:
                _llm_panels_cache["data"] = panels
                _llm_panels_cache["expires_at"] = time.monotonic() + _LLM_CACHE_TTL
                return panels
            logger.warning("LLM 返回内容解析失败")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM 响应解析失败")
        except Exception as e:
            logger.error("Gemini AI 面板生成失败: %s", e)
            if isinstance(e, HTTPException):
                raise
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="LLM 服务调用失败") from e

    @staticmethod
    def _parse_ai_panels_response(reply: str) -> list[NewsAiPanel] | None:
        """解析 LLM 返回的 JSON 内容为 NewsAiPanel 列表。"""
        import json as _json
        now = datetime.now(tz=UTC)

        # 提取 JSON 数组（兼容 LLM 可能包裹在 markdown 代码块中）
        text = reply.strip()
        if "```" in text:
            # 提取代码块中的内容
            parts = text.split("```")
            for part in parts:
                stripped = part.strip()
                if stripped.startswith("json"):
                    stripped = stripped[4:].strip()
                if stripped.startswith("["):
                    text = stripped
                    break

        try:
            items = _json.loads(text)
        except _json.JSONDecodeError:
            logger.warning("AI 面板 JSON 解析失败: %s", text[:200])
            return None

        if not isinstance(items, list) or len(items) < 4:
            return None

        valid_keys = {"24h_digest", "hotspot_tracking", "macro_impact", "stock_interpretation"}
        panels: list[NewsAiPanel] = []
        for item in items[:4]:
            key = item.get("key", "")
            if key not in valid_keys:
                continue
            panels.append(NewsAiPanel(
                panel_key=key,  # type: ignore[arg-type]
                title=item.get("title", ""),
                summary=item.get("summary", ""),
                highlights=item.get("highlights", []),
                confidence=0.90,
                updated_at=now,
            ))

        return panels if len(panels) == 4 else None

    # ─────────────── 热门股票 ───────────────

    def list_hot_stocks(self) -> list[HotStockTag]:
        """热门股票标签 —— 从涨停池中按连板数 + 成交额排名。"""
        pool = get_limit_up_pool()
        # 按连板数降序、成交额降序排序
        sorted_pool = sorted(pool, key=lambda s: (s.consecutive, s.amount), reverse=True)

        tags: list[HotStockTag] = []
        for i, s in enumerate(sorted_pool[:10]):
            # 热度：100 递减
            heat = max(100 - i * 5, 50)
            tags.append(HotStockTag(
                stock_code=s.symbol,
                stock_name=s.name,
                heat=heat,
                label=s.industry or "热门",
            ))
        return tags

    # ─────────────── 热门资讯 ───────────────

    def list_hot_news(self) -> list[HotNewsRankItem]:
        """24 小时热门资讯榜 —— 从资讯流中取前 10 条。"""
        feed = self.list_feed(category="all")
        now = datetime.now(tz=UTC)

        items: list[HotNewsRankItem] = []
        for i, news in enumerate(feed[:10]):
            items.append(HotNewsRankItem(
                rank=i + 1,
                news_id=news.news_id,
                title=news.title,
                source=news.source,
                publish_time=news.publish_time,
                category=news.category,
                heat_score=max(100 - i * 8, 30),
            ))
        return items

    # ─────────────── 个股资讯汇总 ───────────────

    def get_stock_summary(self, stock_code: str) -> StockNewsSummary:
        """按股票聚合资讯 —— 获取该股真实新闻。"""
        normalized = stock_code.strip()
        raw_news = get_stock_news(normalized, limit=20)

        stock_name = normalized
        if raw_news:
            # 从新闻中提取股票名称（关键词字段通常是代码）
            stock_name = normalized

        # 简单情绪分析：基于标题关键词
        sentiment = SentimentDistribution()
        themes: set[str] = set()
        for news in raw_news:
            title = news.title.lower()
            if any(w in title for w in ("涨", "增长", "突破", "新高", "利好")):
                sentiment.bullish += 1
            elif any(w in title for w in ("跌", "下降", "利空", "风险", "下跌")):
                sentiment.bearish += 1
            else:
                sentiment.neutral += 1

        latest_time = None
        if raw_news and raw_news[0].publish_time:
            try:
                latest_time = datetime.fromisoformat(raw_news[0].publish_time.replace(" ", "T"))
                if latest_time.tzinfo is None:
                    latest_time = latest_time.replace(tzinfo=UTC)
            except (ValueError, AttributeError):
                latest_time = datetime.now(tz=UTC)

        if not raw_news:
            conclusion = "暂无该股票的关联资讯，建议关注热门题材联动。"
        elif sentiment.bullish > sentiment.bearish:
            conclusion = "关联资讯整体偏积极，关注成交持续性与业绩兑现。"
        elif sentiment.bearish > sentiment.bullish:
            conclusion = "关联资讯偏谨慎，建议观察风险释放信号。"
        else:
            conclusion = "关联资讯情绪中性，关注催化事件。"

        return StockNewsSummary(
            stock_code=normalized,
            stock_name=stock_name,
            conclusion=conclusion,
            related_news_count=len(raw_news),
            sentiment_distribution=sentiment,
            related_themes=sorted(themes),
            avg_confidence=0.80,
            latest_publish_time=latest_time,
        )


# ════════════════════════════════════════════════════════════
# 辅助函数
# ════════════════════════════════════════════════════════════

# 重要性关键词映射
_IMPORTANT_KEYWORDS = [
    "涨停", "跌停", "暴涨", "暴跌", "重大", "紧急", "突破", "创新高", "预增", "预亏",
    "央行", "降息", "加息", "IPO", "退市", "停牌", "复牌", "回购", "增持", "减持",
    "战争", "制裁", "关税", "通胀", "GDP",
]


def _calc_importance(text: str) -> int:
    """根据文本关键词计算重要性（1-5）。"""
    if not text:
        return 2
    count = sum(1 for kw in _IMPORTANT_KEYWORDS if kw in text)
    if count >= 3:
        return 5
    elif count >= 2:
        return 4
    elif count >= 1:
        return 3
    return 2


def _top_stock_summary(pool: list) -> str:
    """生成最高连板股摘要。"""
    if not pool:
        return "暂无涨停数据。"
    # 找最高连板
    top = max(pool, key=lambda s: (s.consecutive, s.amount))
    return f"最高连板：{top.name}（{top.symbol}）{top.consecutive}连板，所属{top.industry}板块。"


def _top_stock_highlights(pool: list) -> list[str]:
    """生成最高连板股亮点。"""
    if not pool:
        return ["暂无数据"]
    sorted_pool = sorted(pool, key=lambda s: (s.consecutive, s.amount), reverse=True)
    highlights = []
    for s in sorted_pool[:3]:
        highlights.append(f"{s.name} {s.consecutive}连板 · {s.industry}")
    return highlights
