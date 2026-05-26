from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.modules.preopen import snapshots
from app.modules.preopen import router as preopen_router
from app.modules.preopen import skill_service as preopen_skill_service
from app.models.preopen import PreopenAiDigest
from app.modules.preopen.service import PreopenService
from app.modules.preopen.schemas import AiDigest, AnomalyItem, HotNewsItem, LimitUpLadderItem, TrendOverview
from app.modules.preopen.router import _load_list_or_live
from app.modules.preopen.snapshot_cache import load_snapshot, save_snapshot
from app.modules.preopen.snapshot_refresher import RefreshTarget, _refresh_target
from app.integrations.akshare import client as akshare_client
from app.skills.base import SkillContext, SkillResult


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    async def rename(self, source: str, target: str) -> None:
        self.store[target] = self.store.pop(source)

    async def expire(self, key: str, seconds: int) -> bool:
        return key in self.store

    async def eval(self, script: str, numkeys: int, key: str, token: str) -> int:
        if self.store.get(key) == token:
            del self.store[key]
            return 1
        return 0


class _ScalarResult:
    def __init__(self, value: object | None) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object | None:
        return self._value


class _FakeSession:
    def __init__(self, value: object | None) -> None:
        self.value = value
        self.flushed = False
        self.info: dict[str, object] = {}
        self.added: list[object] = []

    async def execute(self, _stmt: object) -> _ScalarResult:
        return _ScalarResult(self.value)

    async def flush(self) -> None:
        self.flushed = True

    def add(self, item: object) -> None:
        self.added.append(item)


def _hot_news_item(title: str) -> HotNewsItem:
    return HotNewsItem(
        news_id=f"hn_{title}",
        title=title,
        summary=title,
        source="测试",
        published_at=datetime(2026, 4, 26, 9, 30, tzinfo=UTC),
        heat=100,
        sentiment="neutral",
        symbols=[],
        jump_type="news",
        jump_target="/news",
    )


@pytest.mark.asyncio
async def test_preopen_snapshot_round_trip() -> None:
    redis = FakeRedis()
    item = _hot_news_item("快讯")

    await save_snapshot(redis, snapshots.HOT_NEWS, [item])

    raw = redis.store[snapshots.HOT_NEWS.redis_key]
    payload = json.loads(raw)
    loaded = await load_snapshot(redis, snapshots.HOT_NEWS)

    assert payload["name"] == "hot-news"
    assert payload["updated_at"]
    assert loaded == [item]


@pytest.mark.asyncio
async def test_refresh_target_keeps_last_snapshot_when_required_list_is_empty() -> None:
    redis = FakeRedis()
    old_item = _hot_news_item("旧快讯")
    await save_snapshot(redis, snapshots.HOT_NEWS, [old_item])

    async def empty_fetch(_service: object) -> list[HotNewsItem]:
        return []

    refreshed = await _refresh_target(
        redis,
        object(),  # type: ignore[arg-type]
        RefreshTarget(spec=snapshots.HOT_NEWS, fetch=empty_fetch, min_items=1),
    )
    loaded = await load_snapshot(redis, snapshots.HOT_NEWS)

    assert refreshed is False
    assert loaded == [old_item]


def test_ai_digest_parser_accepts_richer_workflow_fields() -> None:
    reply = json.dumps(
        {
            "headline": "新闻催化科技线升温",
            "sentiment": "bullish",
            "key_points": ["涨停家数回升", "连板高度打开"],
            "news_drivers": ["算力订单落地"],
            "opportunity_sectors": ["算力", "半导体"],
            "risk_sectors": ["高位地产"],
            "intraday_watch": ["观察算力涨停是否扩散"],
            "simulation_plan": ["只在板块共振时开仓"],
        },
        ensure_ascii=False,
    )

    digest = PreopenService._parse_ai_digest_response(reply)

    assert digest is not None
    assert digest.news_drivers == ["算力订单落地"]
    assert digest.opportunity_sectors == ["算力", "半导体"]
    assert digest.intraday_watch == ["观察算力涨停是否扩散"]
    assert digest.report_title
    assert len(digest.report_sections) >= 6
    assert digest.report_sections[0].title.startswith("一、先抛观点")


def test_trends_fallback_only_returns_real_latest_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.modules.preopen.service.get_limit_up_pool",
        lambda: [
            type("LimitItem", (), {"consecutive": 2})(),
            type("LimitItem", (), {"consecutive": 1})(),
        ],
    )
    monkeypatch.setattr("app.modules.preopen.service.get_limit_down_pool", lambda: [object()])

    trends = PreopenService().get_trends()

    assert trends.window_days == 1
    assert all(len(series.points) == 1 for series in trends.series)
    assert trends.series[0].points[0].value == 2
    assert trends.series[1].points[0].value == 1
    assert trends.series[2].points[0].value == 1


def test_anomaly_item_accepts_risk_prompt_fields() -> None:
    item = AnomalyItem(
        symbol="000001",
        name="平安银行",
        category="severe-volatility",
        change_pct=-9.8,
        turnover_ratio=12.3,
        risk_tags=["abnormal_volatility"],
        note="跌停，换手率 12.3%",
        risk_type="交易所异常波动风险",
        risk_window="连续10/30个交易日",
        is_new=True,
    )

    assert item.risk_type == "交易所异常波动风险"
    assert item.risk_window == "连续10/30个交易日"
    assert item.is_new is True


def test_trends_builds_real_multi_day_series_from_snapshots() -> None:
    rows = [
        SimpleNamespace(
            trade_date=date(2026, 4, 29),
            limit_up_count=45,
            limit_down_count=8,
            consecutive_limit_up_count=12,
        ),
        SimpleNamespace(
            trade_date=date(2026, 4, 30),
            limit_up_count=52,
            limit_down_count=5,
            consecutive_limit_up_count=18,
        ),
    ]

    trends = PreopenService._trend_overview_from_snapshots(rows, requested_days=15)  # type: ignore[arg-type]

    assert trends.window_days == 2
    assert [point.value for point in trends.series[0].points] == [45, 52]
    assert [point.trade_date for point in trends.series[1].points] == [
        date(2026, 4, 29),
        date(2026, 4, 30),
    ]


def test_market_data_trade_date_uses_shanghai_timezone(monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            current = datetime(2026, 5, 25, 6, 35, tzinfo=UTC)
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    class FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 5, 25)

    monkeypatch.setattr(akshare_client, "datetime", FixedDatetime)
    monkeypatch.setattr(akshare_client, "date", FixedDate)

    assert akshare_client.get_market_data_trade_date() == date(2026, 5, 25)


@pytest.mark.asyncio
async def test_empty_preopen_snapshot_falls_back_to_live_fetch() -> None:
    item = _hot_news_item("实时快讯")

    loaded = await _load_list_or_live(FakeRedis(), snapshots.HOT_NEWS, lambda: [item])

    assert loaded == [item]


@pytest.mark.asyncio
async def test_preopen_all_exposes_live_ladder_trade_date_when_snapshot_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    await save_snapshot(redis, snapshots.HOT_NEWS, [_hot_news_item("缓存快讯")])
    await save_snapshot(redis, snapshots.MARKET_INDICATORS, [])
    await save_snapshot(redis, snapshots.ANOMALIES, snapshots.empty_anomalies())
    await save_snapshot(redis, snapshots.TRENDS, snapshots.empty_trends())
    await save_snapshot(redis, snapshots.INDUSTRY_BOARDS, [])
    await save_snapshot(redis, snapshots.STOCK_RANK_UP, [])
    await save_snapshot(redis, snapshots.STOCK_RANK_DOWN, [])

    class FakeRedisFactory:
        def get_client(self) -> FakeRedis:
            return redis

    monkeypatch.setattr(
        preopen_router,
        "get_container",
        lambda: SimpleNamespace(redis=FakeRedisFactory()),
    )

    live_item = LimitUpLadderItem(
        symbol="603000",
        name="实时涨停",
        ladder_level=2,
        first_seal_time="09:35:00",
        final_seal_time="10:12:00",
        reason="测试行业",
        risk_tags=["consecutive_limit_up"],
        trade_date=date(2026, 5, 25),
    )
    monkeypatch.setattr(preopen_router.service, "list_limit_up_ladder", lambda: [live_item])

    response = await preopen_router.preopen_all(session=object())  # type: ignore[arg-type]

    assert response.data.limit_up_ladder == [live_item]
    assert response.data.limit_up_ladder[0].trade_date == date(2026, 5, 25)


@pytest.mark.asyncio
async def test_preopen_all_reads_trends_snapshot_without_recording_market_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    await save_snapshot(redis, snapshots.HOT_NEWS, [_hot_news_item("缓存快讯")])
    await save_snapshot(redis, snapshots.MARKET_INDICATORS, [])
    await save_snapshot(redis, snapshots.ANOMALIES, snapshots.empty_anomalies())
    await save_snapshot(redis, snapshots.TRENDS, snapshots.empty_trends())
    await save_snapshot(redis, snapshots.LIMIT_UP_LADDER, [])
    await save_snapshot(redis, snapshots.INDUSTRY_BOARDS, [])
    await save_snapshot(redis, snapshots.STOCK_RANK_UP, [])
    await save_snapshot(redis, snapshots.STOCK_RANK_DOWN, [])

    class FakeRedisFactory:
        def get_client(self) -> FakeRedis:
            return redis

    monkeypatch.setattr(
        preopen_router,
        "get_container",
        lambda: SimpleNamespace(redis=FakeRedisFactory()),
    )

    async def fail_async_get_trends(_session: object) -> TrendOverview:
        raise AssertionError("page aggregate endpoint must not record live market snapshot")

    monkeypatch.setattr(preopen_router.service, "async_get_trends", fail_async_get_trends)

    response = await preopen_router.preopen_all(session=object())  # type: ignore[arg-type]

    assert response.data.hot_news[0].title == "缓存快讯"
    assert response.data.trends.window_days == 15


@pytest.mark.asyncio
async def test_ai_digest_endpoint_reads_stored_digest_without_calling_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = PreopenAiDigest(
        id="digest_20260525_test",
        trade_date=date(2026, 5, 25),
        generated_at=datetime(2026, 5, 25, 8, 30, tzinfo=UTC),
        main_thesis_md="## 一、今日核心矛盾\n科技线继续扩散。\n\n## 二、主线判断\n关注半导体。",
        skill_outputs={
            "main_thesis": {
                "structured": {
                    "core_thesis": "科技线继续扩散",
                    "intraday_checkpoints": ["观察半导体涨停扩散"],
                    "operation_discipline": ["不追高"],
                }
            }
        },
        falsification_signals=["半导体龙头炸板"],
        bias="bullish",
        tokens_used=123,
    )

    async def fail_llm() -> AiDigest:
        raise AssertionError("read path must not call LLM")

    monkeypatch.setattr(preopen_router.service, "generate_ai_digest_with_llm", fail_llm)

    response = await preopen_router.ai_digest(session=_FakeSession(stored))  # type: ignore[arg-type]

    assert response.data.digest_id == "digest_20260525_test"
    assert response.data.headline == "科技线继续扩散"
    assert response.data.sentiment == "bullish"
    assert response.data.opportunity_sectors == ["科技线继续扩散"]
    assert response.data.risk_sectors == ["半导体龙头炸板"]


@pytest.mark.asyncio
async def test_run_preopen_chain_reuses_existing_digest_without_running_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = PreopenAiDigest(
        id="digest_existing",
        trade_date=date(2026, 5, 25),
        generated_at=datetime(2026, 5, 25, 8, 30, tzinfo=UTC),
        main_thesis_md="已生成的盘前报告",
        skill_outputs={},
        falsification_signals=[],
        bias="mixed",
        tokens_used=77,
    )

    def fail_build_orchestrator() -> object:
        raise AssertionError("existing digest should skip orchestrator")

    monkeypatch.setattr(preopen_skill_service, "_build_orchestrator", fail_build_orchestrator)

    session = _FakeSession(stored)
    data = await preopen_skill_service.run_preopen_chain(
        session,  # type: ignore[arg-type]
        trade_date=date(2026, 5, 25),
    )

    assert data["digest_id"] == "digest_existing"
    assert data["main_thesis_md"] == "已生成的盘前报告"
    assert data["reused"] is True
    assert "openclaw_digest_pushes" not in session.info


@pytest.mark.asyncio
async def test_run_preopen_chain_queues_digest_push_for_new_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOrchestrator:
        async def run(self, ctx: SkillContext) -> dict[str, SkillResult]:
            return {
                "main_thesis": SkillResult(
                    skill_name="main_thesis",
                    success=True,
                    narrative="## 一、今日核心矛盾\n半导体扩散确认。",
                    structured={
                        "bias": "bullish",
                        "falsification_signals": ["半导体龙头炸板"],
                    },
                    duration_ms=1,
                )
            }

    async def fake_persist_thesis_logs(*_args: object, **_kwargs: object) -> int:
        return 0

    monkeypatch.setattr(
        preopen_skill_service, "_build_orchestrator", lambda: FakeOrchestrator()
    )
    monkeypatch.setattr(
        preopen_skill_service,
        "_persist_thesis_logs_for_active_researchers",
        fake_persist_thesis_logs,
    )

    session = _FakeSession(None)
    data = await preopen_skill_service.run_preopen_chain(
        session,  # type: ignore[arg-type]
        trade_date=date(2026, 5, 26),
    )

    assert data["reused"] is False
    messages = session.info["openclaw_digest_pushes"]
    assert len(messages) == 1
    assert "【极睿智投｜盘前摘要】" in messages[0]
    assert "半导体扩散确认" in messages[0]


@pytest.mark.asyncio
async def test_stream_preopen_chain_reuses_existing_digest_without_running_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = PreopenAiDigest(
        id="digest_stream_existing",
        trade_date=date(2026, 5, 25),
        generated_at=datetime(2026, 5, 25, 8, 30, tzinfo=UTC),
        main_thesis_md="已生成的盘前流式报告",
        skill_outputs={},
        falsification_signals=[],
        bias="bullish",
        tokens_used=88,
    )

    def fail_build_orchestrator() -> object:
        raise AssertionError("existing digest should skip streaming orchestrator")

    monkeypatch.setattr(preopen_skill_service, "_build_orchestrator", fail_build_orchestrator)

    chunks = [
        chunk
        async for chunk in preopen_skill_service.stream_preopen_chain(
            _FakeSession(stored),  # type: ignore[arg-type]
            trade_date=date(2026, 5, 25),
        )
    ]

    payload = "".join(chunks)
    assert "digest_stream_existing" in payload
    assert '"reused": true' in payload
