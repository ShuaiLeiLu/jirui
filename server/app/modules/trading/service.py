"""
模拟交易引擎

功能：
  - 模拟账户管理（初始 100 万可用资金）
  - 下单撮合（买入扣减资金增加持仓 / 卖出释放资金减少持仓）
  - 持仓盈亏与账户汇总实时计算
  - 成交记录回放（计算已实现盈亏 / 持仓成本 / 历史统计）
"""
from __future__ import annotations

import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.akshare.client import (
    StockQuote,
    get_stock_quote_by_symbols,
    peek_stock_quote_by_symbols,
    run_sync,
)
from app.models.researcher import Researcher as ResearcherModel
from app.models.trading import Position as PositionModel
from app.models.trading import TradeLog as TradeLogModel
from app.models.trading import TradeRecord as RecordModel
from app.models.trading import TradingAccount as AccountModel
from app.modules.trading.reflection_skill import TradingReflectionSkill
from app.modules.trading.rqalpha_adapter import (
    ORDER_STATUS_FILLED,
    MarketSnapshot,
    compute_sellable_quantity,
    execute_stock_order,
)
from app.modules.trading.schemas import (
    DEFAULT_INITIAL_CAPITAL,
    DailyReturn,
    EquityPoint,
    MonthlyReturn,
    PlaceOrderRequest,
    PlaceOrderResponse,
    PositionItem,
    RiskMetrics,
    TradeLogItem,
    TradeRecord,
    TradingAccount,
    TradingAllData,
    TradingStats,
    TradingStreamSnapshot,
)
from app.repositories.trading_repo import PositionRepository, TradingAccountRepository

OPEN_COMMISSION_RATE = 0.0003
CLOSE_COMMISSION_RATE = 0.0003
CLOSE_TAX_RATE = 0.001
MIN_COMMISSION = 5.0
ACCOUNT_CACHE_TTL_SECONDS = 10
POSITIONS_CACHE_TTL_SECONDS = 10
STATS_CACHE_TTL_SECONDS = 60
ACCOUNT_ID_CACHE_TTL_SECONDS = 300


@dataclass
class _TimedCacheEntry:
    data: object
    expires_at: float


_view_cache: dict[str, _TimedCacheEntry] = {}
_reflection_skill = TradingReflectionSkill()


@dataclass
class _Lot:
    quantity: int
    unit_cost: float
    bought_at: datetime


@dataclass
class _ReplaySnapshot:
    record_map: dict[str, TradeRecord]
    daily_equity: dict[str, float]
    sell_pnls: list[float]
    hold_days: list[float]


class TradingService:
    """模拟交易引擎 —— 数据库持久化模式。"""

    @staticmethod
    def _cache_get(key: str) -> object | None:
        entry = _view_cache.get(key)
        if entry is None:
            return None
        if time.monotonic() > entry.expires_at:
            _view_cache.pop(key, None)
            return None
        return entry.data

    @staticmethod
    def _cache_set(key: str, data: object, ttl_seconds: int) -> None:
        _view_cache[key] = _TimedCacheEntry(
            data=data,
            expires_at=time.monotonic() + ttl_seconds,
        )

    @staticmethod
    def _cache_invalidate(prefixes: list[str]) -> None:
        for key in list(_view_cache.keys()):
            if any(key.startswith(prefix) for prefix in prefixes):
                _view_cache.pop(key, None)

    def empty_account(self) -> TradingAccount:
        """返回空账户快照，用于 researcher_id 缺失或 DB 不可用时兜底。"""
        return TradingAccount(
            account_id="acct_empty",
            initial_capital=DEFAULT_INITIAL_CAPITAL,
            total_asset=DEFAULT_INITIAL_CAPITAL,
            available_cash=DEFAULT_INITIAL_CAPITAL,
            holding_value=0.0,
            daily_pnl=0.0,
        )

    @staticmethod
    def _sort_positions(items: list[PositionItem]) -> list[PositionItem]:
        return sorted(items, key=lambda item: (abs(item.pnl), item.pnl, item.symbol), reverse=True)

    @staticmethod
    def _infer_initial_capital(account: AccountModel | object | None) -> float:
        """统一返回模拟盘初始资金口径。"""
        return DEFAULT_INITIAL_CAPITAL

    async def _resolve_account_model(
        self,
        session: AsyncSession,
        user_id: str,
        researcher_id: str,
    ) -> AccountModel:
        repo = TradingAccountRepository(session)
        acc = await repo.get_by_user_researcher(user_id, researcher_id)
        if not acc:
            acc = await repo.get_by_researcher(researcher_id)
        if not acc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模拟账户不存在")
        return acc

    async def async_resolve_account_id(
        self,
        session: AsyncSession,
        user_id: str,
        researcher_id: str,
    ) -> str:
        """解析研究员对应的模拟账户 ID，并做短缓存。

        交易详情页会在短时间内连续请求 account / positions / logs / stats，
        researcher -> account_id 的映射基本不变，没必要每个接口都重复查库。
        """
        cache_key = f"account-id:{user_id}:{researcher_id}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, str) and cached:
            return cached

        account = await self._resolve_account_model(session, user_id, researcher_id)
        self._cache_set(cache_key, account.id, ACCOUNT_ID_CACHE_TTL_SECONDS)
        return account.id

    @staticmethod
    def _apply_quotes_to_positions(
        positions: list[PositionModel],
        quote_map: dict[str, StockQuote],
    ) -> tuple[float, float]:
        """按最新行情盯市持仓，返回持仓市值与当日浮动盈亏。

        说明：
        - `pnl` 口径：持仓浮盈浮亏 = (最新价 - 成本价) * 持仓数量
        - `daily_pnl` 浮动部分口径： (最新价 - 昨收) * 持仓数量
        """
        holding_value = 0.0
        floating_daily_pnl = 0.0

        for position in positions:
            latest_price = float(position.current_price)
            quote = quote_map.get(position.symbol)
            if quote and float(quote.price) > 0:
                latest_price = float(quote.price)

            quantity = int(position.quantity)
            cost_price = float(position.cost_price)
            position.current_price = round(latest_price, 4)
            position.pnl = round((latest_price - cost_price) * quantity, 2)

            holding_value += latest_price * quantity

            if quote and float(quote.prev_close) > 0:
                floating_daily_pnl += (latest_price - float(quote.prev_close)) * quantity

        return round(holding_value, 2), round(floating_daily_pnl, 2)

    async def _load_realtime_quotes(
        self,
        symbols: list[str],
        *,
        cache_only: bool = False,
    ) -> dict[str, StockQuote]:
        """批量获取实时行情。

        - `cache_only=True`：只读本地缓存，不触发外部行情请求
        - `cache_only=False`：按 symbol 补齐缺失行情，适合 SSE 等实时流
        """
        normalized_symbols = sorted({symbol for symbol in symbols if symbol})
        if not normalized_symbols:
            return {}
        try:
            loader = peek_stock_quote_by_symbols if cache_only else get_stock_quote_by_symbols
            return await run_sync(loader, normalized_symbols)
        except Exception:
            return {}

    async def _load_account_records(self, session: AsyncSession, account_id: str) -> list[RecordModel]:
        stmt = (
            select(RecordModel)
            .where(RecordModel.account_id == account_id)
            .order_by(RecordModel.created_at.asc(), RecordModel.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _load_account_records_in_range(
        self,
        session: AsyncSession,
        account_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> list[RecordModel]:
        """按时间范围查询成交记录。

        账户概况的“今日盈亏”只关心当日成交，不应该每次都全量回放历史记录。
        这里先查当天窗口，只有确实存在卖出单时，才退化到全量回放计算真实已实现盈亏。
        """
        stmt = (
            select(RecordModel)
            .where(
                RecordModel.account_id == account_id,
                RecordModel.created_at >= start_at,
                RecordModel.created_at < end_at,
            )
            .order_by(RecordModel.created_at.asc(), RecordModel.id.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def _load_today_buy_quantities(
        self,
        session: AsyncSession,
        account_id: str,
    ) -> dict[str, int]:
        today = datetime.now().date()
        start_at = datetime.combine(today, datetime.min.time())
        end_at = start_at + timedelta(days=1)
        rows = await self._load_account_records_in_range(
            session,
            account_id,
            start_at=start_at,
            end_at=end_at,
        )
        quantities: dict[str, int] = defaultdict(int)
        for row in rows:
            if row.side == "buy":
                quantities[row.symbol] += int(row.quantity)
        return dict(quantities)

    async def _load_researcher_model(
        self,
        session: AsyncSession,
        researcher_id: str,
    ) -> ResearcherModel | None:
        stmt = select(ResearcherModel).where(ResearcherModel.id == researcher_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _append_trade_reflection_log(
        self,
        session: AsyncSession,
        *,
        account_id: str,
        researcher: ResearcherModel | None,
        trade_context: dict[str, object],
    ) -> None:
        """追加成交后的 AI 复盘日志，内容会覆盖交易复盘、执行反思与次日展望。"""
        reflection = await _reflection_skill.build_trade_reflection(
            researcher_name=researcher.name if researcher else "小市值研究员",
            researcher_prompt=researcher.prompt if researcher else "",
            trade_context=trade_context,
        )
        session.add(
            TradeLogModel(
                id=f"tl_{uuid4().hex[:8]}",
                account_id=account_id,
                log_type="analysis",
                trade_record_ids="[]",
                title=_reflection_skill.build_trade_log_title(trade_context),
                content=reflection,
            )
        )

    def _replay_records(
        self,
        records: list[RecordModel],
        *,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    ) -> _ReplaySnapshot:
        """按成交记录回放账户状态，生成可复用的增强数据。

        这一步统一负责：
        - 每笔卖出的真实成本价 / 已实现盈亏 / 盈亏比例
        - 每日权益曲线
        - 平均持仓天数、胜率等统计基础数据
        """
        lots: dict[str, deque[_Lot]] = defaultdict(deque)
        market_price: dict[str, float] = {}
        record_map: dict[str, TradeRecord] = {}
        daily_equity: dict[str, float] = {}
        sell_pnls: list[float] = []
        hold_days: list[float] = []
        cash = float(initial_capital)

        for row in records:
            price = float(row.price)
            quantity = int(row.quantity)
            amount = round(price * quantity, 2)
            commission = round(float(getattr(row, "commission", 0.0) or 0.0), 2)
            symbol = row.symbol
            name = getattr(row, "name", "") or ""
            dt = row.created_at

            cost_price: float | None = None
            realized_pnl: float | None = None
            realized_pnl_pct: float | None = None
            hold_days_val: float | None = None

            if row.side == "buy":
                cash -= amount + commission
                cost_price = round((amount + commission) / quantity, 4)
                lots[symbol].append(_Lot(quantity=quantity, unit_cost=cost_price, bought_at=dt))
                market_price[symbol] = price
            else:
                cash += amount - commission
                remaining = quantity
                cost_basis = 0.0
                weighted_hold_days = 0.0
                consumed = 0

                symbol_lots = lots[symbol]
                while remaining > 0 and symbol_lots:
                    lot = symbol_lots[0]
                    take = min(remaining, lot.quantity)
                    cost_basis += lot.unit_cost * take
                    weighted_hold_days += take * max((dt - lot.bought_at).total_seconds() / 86400, 0)
                    consumed += take
                    lot.quantity -= take
                    remaining -= take
                    if lot.quantity == 0:
                        symbol_lots.popleft()

                if remaining > 0:
                    # 容错：若出现超卖数据，避免把收益算成异常高值。
                    estimated_cost = price * remaining
                    cost_basis += estimated_cost
                    consumed += remaining

                if symbol_lots:
                    market_price[symbol] = price
                else:
                    market_price.pop(symbol, None)

                cost_price = round(cost_basis / quantity, 4) if quantity > 0 else None
                realized_pnl = round(amount - commission - cost_basis, 2)
                realized_pnl_pct = round(realized_pnl / cost_basis, 4) if cost_basis > 0 else None
                hold_days_val = round(weighted_hold_days / consumed, 1) if consumed > 0 else None

                sell_pnls.append(realized_pnl)
                if hold_days_val is not None:
                    hold_days.append(hold_days_val)

            holding_value = 0.0
            for holding_symbol, holding_lots in lots.items():
                total_qty = sum(lot.quantity for lot in holding_lots)
                if total_qty <= 0:
                    continue
                holding_value += market_price.get(holding_symbol, 0.0) * total_qty

            daily_equity[dt.strftime("%Y-%m-%d")] = round(cash + holding_value, 2)

            record_map[row.id] = TradeRecord(
                trade_id=row.id,
                symbol=symbol,
                name=name,
                side=row.side,
                quantity=quantity,
                price=price,
                amount=amount,
                commission=commission,
                cost_price=cost_price,
                realized_pnl=realized_pnl,
                realized_pnl_pct=realized_pnl_pct,
                hold_days=hold_days_val,
                position_ratio=round(amount / initial_capital, 4) if initial_capital > 0 else None,
                created_at=dt,
            )

        return _ReplaySnapshot(
            record_map=record_map,
            daily_equity=daily_equity,
            sell_pnls=sell_pnls,
            hold_days=hold_days,
        )

    async def _refresh_account_snapshot(
        self,
        session: AsyncSession,
        account: AccountModel,
        *,
        cache_only: bool = True,
    ) -> None:
        """根据行情重算账户汇总。

        默认走缓存快路径；SSE 场景可显式要求走实时行情。
        """
        repo = PositionRepository(session)
        positions = await repo.list_by_account(account.id)
        quote_map = await self._load_realtime_quotes(
            [position.symbol for position in positions],
            cache_only=cache_only,
        )
        account.holding_value, floating_daily_pnl = self._apply_quotes_to_positions(positions, quote_map)
        account.total_asset = round(float(account.available_cash) + float(account.holding_value), 2)

        today = datetime.now().date()
        start_at = datetime.combine(today, datetime.min.time())
        end_at = start_at + timedelta(days=1)
        today_records = await self._load_account_records_in_range(
            session,
            account.id,
            start_at=start_at,
            end_at=end_at,
        )

        realized_daily_pnl = 0.0
        if today_records:
            if any(row.side == "sell" for row in today_records):
                # 只有当日存在卖出时，才需要回放全量历史去还原真实成本价。
                records = await self._load_account_records(session, account.id)
                replay = self._replay_records(records)
                for record in replay.record_map.values():
                    if record.created_at.date() != today:
                        continue
                    if record.side == "buy":
                        realized_daily_pnl -= record.commission
                    elif record.realized_pnl is not None:
                        realized_daily_pnl += record.realized_pnl
            else:
                realized_daily_pnl = -sum(float(row.commission or 0.0) for row in today_records)
        account.daily_pnl = round(realized_daily_pnl + floating_daily_pnl, 2)

    async def async_get_stream_snapshot(
        self,
        session: AsyncSession,
        user_id: str,
        researcher_id: str,
        *,
        cache_only: bool = False,
    ) -> TradingStreamSnapshot:
        """构建 SSE 推送用的交易实时快照。"""
        account = await self._resolve_account_model(session, user_id, researcher_id)
        await self._refresh_account_snapshot(session, account, cache_only=cache_only)
        positions = await self.async_list_positions(session, account.id, cache_only=cache_only)
        await session.flush()
        return TradingStreamSnapshot(
            generated_at=datetime.now(),
            account=TradingAccount(
                account_id=account.id,
                initial_capital=self._infer_initial_capital(account),
                total_asset=float(account.total_asset),
                available_cash=float(account.available_cash),
                holding_value=float(account.holding_value),
                daily_pnl=float(account.daily_pnl),
            ),
            positions=positions,
        )

    async def async_get_account(
        self, session: AsyncSession, user_id: str, researcher_id: str
    ) -> TradingAccount:
        """从数据库查询模拟账户。"""
        cache_key = f"account:{user_id}:{researcher_id}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, TradingAccount):
            return cached

        acc = await self._resolve_account_model(session, user_id, researcher_id)

        await self._refresh_account_snapshot(session, acc, cache_only=True)
        await session.flush()

        data = TradingAccount(
            account_id=acc.id,
            initial_capital=self._infer_initial_capital(acc),
            total_asset=float(acc.total_asset),
            available_cash=float(acc.available_cash),
            holding_value=float(acc.holding_value),
            daily_pnl=float(acc.daily_pnl),
        )
        self._cache_set(cache_key, data, ACCOUNT_CACHE_TTL_SECONDS)
        return data

    async def async_list_positions(
        self,
        session: AsyncSession,
        account_id: str,
        *,
        cache_only: bool = True,
    ) -> list[PositionItem]:
        """从数据库查询持仓，并尽量用实时行情更新 current_price / pnl。

        默认走缓存快路径，避免普通 REST 接口被实时行情外部请求拖慢。
        """
        if cache_only:
            cache_key = f"positions:{account_id}"
            cached = self._cache_get(cache_key)
            if isinstance(cached, list):
                return cached

        repo = PositionRepository(session)
        positions = await repo.list_by_account(account_id)
        today_buy_quantities = await self._load_today_buy_quantities(session, account_id)
        quote_map = await self._load_realtime_quotes(
            [position.symbol for position in positions],
            cache_only=cache_only,
        )
        self._apply_quotes_to_positions(positions, quote_map)
        items = [
            PositionItem(
                symbol=position.symbol,
                name=position.name,
                quantity=position.quantity,
                sellable_quantity=compute_sellable_quantity(
                    int(position.quantity),
                    today_buy_quantities.get(position.symbol, 0),
                ),
                cost_price=float(position.cost_price),
                current_price=float(position.current_price),
                pnl=float(position.pnl),
            )
            for position in positions
        ]
        sorted_items = self._sort_positions(items)
        if cache_only:
            self._cache_set(f"positions:{account_id}", sorted_items, POSITIONS_CACHE_TTL_SECONDS)
        return sorted_items

    async def _load_replay(
        self, session: AsyncSession, account_id: str,
    ) -> tuple[list[RecordModel], _ReplaySnapshot]:
        """加载成交记录并回放（可复用，避免多个方法各自重复加载）。"""
        cache_key = f"replay:{account_id}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, tuple) and len(cached) == 2:
            return cached  # type: ignore[return-value]

        records = await self._load_account_records(session, account_id)
        replay = self._replay_records(records)
        self._cache_set(cache_key, (records, replay), ACCOUNT_CACHE_TTL_SECONDS)
        return records, replay

    async def async_list_records(
        self, session: AsyncSession, account_id: str, *, limit: int = 20,
        _replay: tuple[list[RecordModel], _ReplaySnapshot] | None = None,
    ) -> list[TradeRecord]:
        """从数据库查询成交记录，并补齐成本/已实现盈亏等增强字段。"""
        records, replay = _replay or await self._load_replay(session, account_id)
        desc_items = [replay.record_map[row.id] for row in reversed(records)]
        return desc_items[:limit]

    async def async_list_logs(
        self, session: AsyncSession, account_id: str, *, limit: int = 100,
        _replay: tuple[list[RecordModel], _ReplaySnapshot] | None = None,
    ) -> list[TradeLogItem]:
        """从数据库查询交易日志（trade + analysis 条目），并填充增强后的成交记录。"""
        stmt = (
            select(TradeLogModel)
            .where(TradeLogModel.account_id == account_id)
            .order_by(TradeLogModel.created_at.asc(), TradeLogModel.id.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        logs = list(result.scalars().all())

        _, replay = _replay or await self._load_replay(session, account_id)

        items: list[TradeLogItem] = []
        for log in logs:
            try:
                record_ids = json.loads(log.trade_record_ids or "[]")
            except Exception:
                record_ids = []
            related_records = [replay.record_map[record_id] for record_id in record_ids if record_id in replay.record_map]
            items.append(
                TradeLogItem(
                    log_id=log.id,
                    log_type=log.log_type,
                    trade_records=related_records,
                    title=log.title or "",
                    content=log.content or "",
                    created_at=log.created_at,
                )
            )
        return items

    async def async_get_all(
        self,
        session: AsyncSession,
        user_id: str,
        researcher_id: str,
    ) -> TradingAllData:
        """一次请求返回模拟盘全部页面数据。

        核心优化：只加载一次成交记录、只回放一次，然后复用到
        account / positions / records / logs 各视图。
        """
        acc = await self._resolve_account_model(session, user_id, researcher_id)
        account_id = acc.id

        # 1. 持仓列表 + 实时行情更新（聚合端点是唯一请求，允许触发行情拉取）
        repo = PositionRepository(session)
        positions = await repo.list_by_account(account_id)
        today_buy_quantities = await self._load_today_buy_quantities(session, account_id)
        quote_map = await self._load_realtime_quotes(
            [p.symbol for p in positions], cache_only=False,
        )
        _, floating_daily_pnl = self._apply_quotes_to_positions(positions, quote_map)

        position_items = self._sort_positions([
            PositionItem(
                symbol=p.symbol, name=p.name, quantity=p.quantity,
                sellable_quantity=compute_sellable_quantity(
                    int(p.quantity),
                    today_buy_quantities.get(p.symbol, 0),
                ),
                cost_price=float(p.cost_price),
                current_price=float(p.current_price),
                pnl=float(p.pnl),
            )
            for p in positions
        ])

        # 2. 加载 & 回放成交记录（只做一次）
        replay_data = await self._load_replay(session, account_id)
        raw_records, replay = replay_data

        # 3. 计算 daily_pnl（复用已有 replay，不再独立查+回放）
        today = datetime.now().date()
        realized_daily_pnl = 0.0
        has_sells_today = any(
            r.side == "sell" and r.created_at.date() == today for r in raw_records
        )
        if has_sells_today:
            for record in replay.record_map.values():
                if record.created_at.date() != today:
                    continue
                if record.side == "buy":
                    realized_daily_pnl -= record.commission
                elif record.realized_pnl is not None:
                    realized_daily_pnl += record.realized_pnl
        else:
            today_buys = [r for r in raw_records if r.created_at.date() == today]
            realized_daily_pnl = -sum(float(r.commission or 0.0) for r in today_buys)

        holding_value = sum(float(p.current_price) * p.quantity for p in positions)
        total_asset = round(float(acc.available_cash) + holding_value, 2)
        daily_pnl = round(realized_daily_pnl + floating_daily_pnl, 2)

        account_data = TradingAccount(
            account_id=acc.id,
            initial_capital=self._infer_initial_capital(acc),
            total_asset=total_asset,
            available_cash=float(acc.available_cash),
            holding_value=round(holding_value, 2),
            daily_pnl=daily_pnl,
        )

        # 4. 成交记录（复用 replay）
        record_items = await self.async_list_records(
            session, account_id, limit=20, _replay=replay_data,
        )

        # 5. 交易日志（复用 replay）
        log_items = await self.async_list_logs(
            session, account_id, limit=200, _replay=replay_data,
        )

        return TradingAllData(
            account=account_data,
            positions=position_items,
            records=record_items,
            logs=log_items,
        )

    async def async_get_stats(
        self, session: AsyncSession, account_id: str, initial_capital: float | None = None
    ) -> TradingStats:
        """从成交记录计算历史交易统计数据：收益曲线、月度收益、风控指标、日收益序列。"""
        cache_key = f"stats:{account_id}"
        cached = self._cache_get(cache_key)
        if isinstance(cached, TradingStats):
            return cached

        acct_stmt = select(AccountModel).where(AccountModel.id == account_id)
        acct_result = await session.execute(acct_stmt)
        account = acct_result.scalar_one_or_none()
        initial_capital = initial_capital or self._infer_initial_capital(account)
        total_asset = float(account.total_asset) if account else initial_capital
        if account:
            await self._refresh_account_snapshot(session, account, cache_only=True)
            total_asset = float(account.total_asset)

        records = await self._load_account_records(session, account_id)
        if not records:
            data = TradingStats(
                initial_capital=initial_capital,
                total_asset=total_asset,
                equity_curve=[],
                monthly_returns=[],
                daily_returns=[],
                risk=RiskMetrics(
                    total_return=0,
                    annual_return=0,
                    max_drawdown=0,
                    sharpe=0,
                    win_rate=0,
                    profit_loss_ratio=0,
                    total_trades=0,
                    win_trades=0,
                    lose_trades=0,
                    max_profit=0,
                    max_loss=0,
                    avg_hold_days=0,
                ),
            )
            self._cache_set(cache_key, data, STATS_CACHE_TTL_SECONDS)
            return data

        replay = self._replay_records(records, initial_capital=initial_capital)
        daily_equity = dict(replay.daily_equity)
        today_str = datetime.now().date().strftime("%Y-%m-%d")
        if today_str not in daily_equity:
            daily_equity[today_str] = round(total_asset, 2)

        sorted_dates = sorted(daily_equity.keys())
        equity_curve = [EquityPoint(date=date, equity=round(daily_equity[date], 2)) for date in sorted_dates]

        daily_returns: list[DailyReturn] = []
        previous_equity = initial_capital
        for date in sorted_dates:
            equity = daily_equity[date]
            pnl = equity - previous_equity
            daily_returns.append(DailyReturn(date=date, pnl=round(pnl, 2)))
            previous_equity = equity

        monthly_map: dict[str, float] = defaultdict(float)
        monthly_base: dict[str, float] = {}
        previous_equity = initial_capital
        for date in sorted_dates:
            month = date[:7]
            if month not in monthly_base:
                monthly_base[month] = previous_equity
            monthly_map[month] = daily_equity[date] - monthly_base[month]
            previous_equity = daily_equity[date]

        monthly_returns = [
            MonthlyReturn(
                month=month,
                pnl=round(monthly_map[month], 2),
                pct=round(monthly_map[month] / monthly_base[month], 4) if monthly_base.get(month) else 0,
            )
            for month in sorted(monthly_map.keys())
        ]

        total_return = (total_asset - initial_capital) / initial_capital if initial_capital > 0 else 0
        trading_days = max(len(sorted_dates), 1)
        annual_return = total_return * (252 / trading_days)

        peak = initial_capital
        max_drawdown = 0.0
        for date in sorted_dates:
            equity = daily_equity[date]
            if equity > peak:
                peak = equity
            drawdown = (peak - equity) / peak if peak > 0 else 0
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        daily_ret_series: list[float] = []
        previous_equity = initial_capital
        for date in sorted_dates:
            equity = daily_equity[date]
            daily_ret_series.append((equity - previous_equity) / previous_equity if previous_equity > 0 else 0)
            previous_equity = equity
        avg_ret = sum(daily_ret_series) / len(daily_ret_series) if daily_ret_series else 0
        std_ret = (
            sum((value - avg_ret) ** 2 for value in daily_ret_series) / len(daily_ret_series)
        ) ** 0.5 if daily_ret_series else 0
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0

        sell_pnls = replay.sell_pnls
        win_count = sum(1 for pnl in sell_pnls if pnl > 0)
        lose_count = sum(1 for pnl in sell_pnls if pnl < 0)
        total_trades = len(sell_pnls)
        profits = [pnl for pnl in sell_pnls if pnl > 0]
        losses = [abs(pnl) for pnl in sell_pnls if pnl < 0]
        avg_profit = sum(profits) / len(profits) if profits else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0

        risk = RiskMetrics(
            total_return=round(total_return, 4),
            annual_return=round(annual_return, 4),
            max_drawdown=round(-max_drawdown, 4),
            sharpe=round(sharpe, 2),
            win_rate=round(win_count / total_trades, 4) if total_trades > 0 else 0,
            profit_loss_ratio=round(profit_loss_ratio, 2),
            total_trades=total_trades,
            win_trades=win_count,
            lose_trades=lose_count,
            max_profit=round(max(profits), 2) if profits else 0,
            max_loss=round(max(losses), 2) if losses else 0,
            avg_hold_days=round(sum(replay.hold_days) / len(replay.hold_days), 1) if replay.hold_days else 0,
        )

        data = TradingStats(
            initial_capital=initial_capital,
            total_asset=round(total_asset, 2),
            equity_curve=equity_curve,
            monthly_returns=monthly_returns,
            daily_returns=daily_returns,
            risk=risk,
        )
        self._cache_set(cache_key, data, STATS_CACHE_TTL_SECONDS)
        return data

    async def async_place_order(
        self, session: AsyncSession, user_id: str, payload: PlaceOrderRequest
    ) -> PlaceOrderResponse:
        """数据库模式下单撮合。

        买入：
        - 检查可用资金 >= 成交金额 + 手续费
        - 更新持仓成本
        - 账户当日盈亏扣除买入手续费

        卖出：
        - 检查持仓数量
        - 释放净资金（卖出金额 - 手续费 - 印花税）
        - 账户当日盈亏计入已实现盈亏
        """
        acct_repo = TradingAccountRepository(session)
        pos_repo = PositionRepository(session)
        researcher = await self._load_researcher_model(session, payload.researcher_id)

        acc = await acct_repo.get_by_user_researcher(user_id, payload.researcher_id)
        if not acc:
            acc = await acct_repo.get_by_researcher(payload.researcher_id)
        if not acc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="模拟账户不存在")

        existing = await pos_repo.get_by_account_symbol(acc.id, payload.symbol)
        cost_price_before = float(existing.cost_price) if existing else None
        today_buy_quantities = await self._load_today_buy_quantities(session, acc.id)

        quote_map = await self._load_realtime_quotes([payload.symbol], cache_only=False)
        quote = quote_map.get(payload.symbol)
        resolved_name = (
            payload.name
            or (quote.name if quote else "")
            or (existing.name if existing else "")
            or payload.symbol
        )
        market = MarketSnapshot(
            price=float(quote.price) if quote else None,
            prev_close=float(quote.prev_close) if quote else None,
            volume=float(quote.volume) if quote else None,
        )
        sellable_quantity = None
        if payload.side == "sell":
            sellable_quantity = compute_sellable_quantity(
                int(existing.quantity) if existing else 0,
                today_buy_quantities.get(payload.symbol, 0),
            )

        execution = execute_stock_order(
            account=acc,
            existing_position=existing,
            symbol=payload.symbol,
            name=resolved_name,
            side=payload.side,
            quantity=payload.quantity,
            limit_price=payload.price,
            market=market,
            sellable_quantity=sellable_quantity,
            open_commission_rate=OPEN_COMMISSION_RATE,
            close_commission_rate=CLOSE_COMMISSION_RATE,
            close_tax_rate=CLOSE_TAX_RATE,
            min_commission=MIN_COMMISSION,
        )
        if execution.status != ORDER_STATUS_FILLED:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=execution.message,
            )

        trade_id = f"trd_{uuid4().hex[:8]}"
        amount = round(execution.amount, 2)
        executed_price = round(float(execution.fill_price or payload.price), 4)
        total_fee = execution.total_fee
        realized_pnl = execution.realized_pnl if payload.side == "sell" else None
        reason = "用户在模拟盘中执行手动委托，需复盘本次决策与次日观察点"

        if payload.side == "buy":
            if existing is None and execution.created_position:
                session.add(
                    PositionModel(
                        id=f"pos_{uuid4().hex[:8]}",
                        account_id=acc.id,
                        symbol=payload.symbol,
                        name=resolved_name,
                        quantity=int(execution.created_position["quantity"]),
                        cost_price=float(execution.created_position["cost_price"]),
                        current_price=float(execution.created_position["current_price"]),
                        pnl=float(execution.created_position["pnl"]),
                    )
                )
                cost_price = float(execution.created_position["cost_price"])
            else:
                cost_price = float(existing.cost_price) if existing else None
        else:
            cost_price = cost_price_before
            if execution.remove_position and existing is not None:
                await session.delete(existing)

        realized_pnl_pct = (
            round(realized_pnl / (cost_price_before * payload.quantity), 4)
            if payload.side == "sell"
            and realized_pnl is not None
            and cost_price_before
            and payload.quantity > 0
            else None
        )

        session.add(
            RecordModel(
                id=trade_id,
                account_id=acc.id,
                symbol=payload.symbol,
                name=resolved_name,
                side=payload.side,
                quantity=execution.filled_quantity,
                price=executed_price,
                commission=total_fee,
            )
        )
        session.add(
            TradeLogModel(
                id=f"tl_{uuid4().hex[:8]}",
                account_id=acc.id,
                log_type="trade",
                trade_record_ids=json.dumps([trade_id]),
                title="",
                content="",
            )
        )
        await session.flush()

        await self._refresh_account_snapshot(session, acc)
        positions = await pos_repo.list_by_account(acc.id)
        await self._append_trade_reflection_log(
            session,
            account_id=acc.id,
            researcher=researcher,
            trade_context={
                "mode": "manual_order",
                "side": payload.side,
                "symbol": payload.symbol,
                "name": resolved_name,
                "price": executed_price,
                "quantity": execution.filled_quantity,
                "amount": amount,
                "commission": total_fee,
                "reason": reason,
                "cost_price": cost_price,
                "realized_pnl": realized_pnl,
                "realized_pnl_pct": realized_pnl_pct,
                "position_ratio": round(amount / DEFAULT_INITIAL_CAPITAL, 4) if DEFAULT_INITIAL_CAPITAL > 0 else 0.0,
                "total_asset": float(acc.total_asset),
                "available_cash": float(acc.available_cash),
                "holding_names": [position.name for position in positions],
            },
        )
        await session.commit()
        self._cache_invalidate(
            [
                f"account:{user_id}:{payload.researcher_id}",
                f"positions:{acc.id}",
                f"replay:{acc.id}",
                f"stats:{acc.id}",
            ]
        )

        return PlaceOrderResponse(
            trade_id=trade_id,
            symbol=payload.symbol,
            side=payload.side,
            quantity=payload.quantity,
            filled_quantity=execution.filled_quantity,
            price=executed_price,
            amount=amount,
            commission=execution.commission,
            tax=execution.tax,
            realized_pnl=realized_pnl,
            status=execution.status,
            engine=execution.engine,
            message=execution.message,
        )
