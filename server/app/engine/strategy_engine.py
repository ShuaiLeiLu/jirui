"""策略调度引擎 —— 多因子小市值轮动策略（对标聚宽策略，AKShare 适配）

选股体系（三因子融合 → 取并集）：
  1. SG 池：年初至今涨幅前 10%（营收增长代理），过滤 PE≤0，按流通市值升序取前 5
  2. MS 池：复合成长评分前 10%（60日涨幅×0.35 + 年涨幅×0.40 + 盈利指标×0.15 + 量比×0.10），
     过滤 PE≤0，按流通市值升序取前 5
  3. PEG 池：伪 PEG = PE / max(年涨幅, 1) 升序前 20%，再按换手率升序取前 50%，
     按流通市值升序取前 5
  4. 三池取并集 → 按流通市值升序 → 截取 stock_count 只

调仓频率：每周一全量调仓，其他交易日仅执行风控检查

风控规则：
  - 涨停不卖：昨日涨停持仓不在调仓日卖出
  - 涨停打开即卖：14:00 检查，打开则卖出
  - 近 20 日涨停黑名单：持仓过且涨停过的股票一段时间不再买入
  - 个股止损：亏损达 -10% 立即卖出

数据源：AKShare stock_zh_a_spot_em（东方财富 A 股实时行情，含流通市值/PE/换手率）
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.akshare.client import call_akshare_api
from app.models.researcher import Researcher
from app.models.trading import Position, TradeLog, TradeRecord, TradingAccount
from app.modules.trading.reflection_skill import TradingReflectionSkill
from app.modules.trading.rqalpha_adapter import (
    ORDER_STATUS_FILLED,
    MarketSnapshot,
    compute_sellable_quantity,
    execute_stock_order,
)

logger = logging.getLogger(__name__)
_trading_reflection_skill = TradingReflectionSkill()

# ════════════════════════════════════════════════════════════
# 模块级状态（进程内，重启后重置）
# ════════════════════════════════════════════════════════════

_BLACKLIST_DAYS = 20  # 涨停黑名单天数

# researcher_id -> 昨日涨停的持仓代码列表
_limit_up_symbols: dict[str, list[str]] = defaultdict(list)
# researcher_id -> 最近 N 天的持仓代码列表（二维）
_hold_history: dict[str, list[list[str]]] = defaultdict(list)
# researcher_id -> 近期买过且涨停过的股票，不再买入
_not_buy_again: dict[str, set[str]] = defaultdict(set)
# researcher_id -> 上次全量调仓的日期字符串 "2026-04-21"
_last_rotation_date: dict[str, str] = {}


# ════════════════════════════════════════════════════════════
# 真实行情选股
# ════════════════════════════════════════════════════════════

def _fetch_realtime_quotes() -> list[dict]:
    """通过 AKShare 东方财富接口获取 A 股全市场实时行情快照。

    使用 stock_zh_a_spot_em，包含流通市值、市盈率、换手率、60日涨幅、年涨幅等。
    """
    try:
        df = call_akshare_api("stock_zh_a_spot_em")
    except Exception:
        logger.exception("[选股] AKShare(em) 获取行情失败，回退空列表")
        return []

    quotes: list[dict] = []
    for _, row in df.iterrows():
        symbol = str(row.get("代码", "")).strip()
        if not symbol:
            continue

        price = _safe_float(row.get("最新价"))
        if price <= 0:
            continue

        quotes.append({
            "symbol": symbol,
            "name": str(row.get("名称", "")),
            "price": price,
            "change_pct": _safe_float(row.get("涨跌幅")),
            "amount": _safe_float(row.get("成交额")),
            "open": _safe_float(row.get("今开")),
            "prev_close": _safe_float(row.get("昨收")),
            "volume": _safe_float(row.get("成交量")),
            # ── 东方财富扩展字段 ──
            "circulating_market_cap": _safe_float(row.get("流通市值")),
            "pe_ratio": _safe_float(row.get("市盈率-动态")),
            "pb_ratio": _safe_float(row.get("市净率")),
            "turnover_ratio": _safe_float(row.get("换手率")),
            "volume_ratio": _safe_float(row.get("量比")),
            "change_pct_60d": _safe_float(row.get("60日涨跌幅")),
            "change_pct_ytd": _safe_float(row.get("年初至今涨跌幅")),
        })
    logger.info("[选股] 获取 A 股行情(em) %d 条", len(quotes))
    return quotes


def _safe_float(val, default: float = 0.0) -> float:
    """安全转换为 float。"""
    try:
        if pd.isna(val):
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


def _filter_basic(all_quotes: list[dict], filters: dict) -> list[dict]:
    """基础过滤：ST、科创板、北交所、涨跌停、停牌、仙股、流动性不足。"""
    candidates: list[dict] = []
    for q in all_quotes:
        symbol = q["symbol"]
        name = q["name"]
        price = q["price"]
        change_pct = q["change_pct"]
        amount = q["amount"]

        # 排除北交所（8/4 开头的 6 位码）
        if symbol.startswith(("8", "4")) and len(symbol) == 6:
            continue
        # 排除科创板（688 开头）
        if filters.get("exclude_kcb", True) and symbol.startswith("688"):
            continue
        # 排除 ST / *ST / 退市股
        if filters.get("exclude_st", True):
            if "ST" in name or "st" in name or "退" in name or "*" in name:
                continue
        # 排除涨停股（涨幅 >= 9.8%）
        if filters.get("exclude_limit_up", True) and change_pct >= 9.8:
            continue
        # 排除跌停股（跌幅 <= -9.8%）
        if filters.get("exclude_limit_down", True) and change_pct <= -9.8:
            continue
        # 价格过滤：排除仙股和高价股
        if price < 1.0 or price > 100.0:
            continue
        # 流动性过滤：成交额 < 500 万排除
        if amount < 5_000_000:
            continue
        candidates.append(q)
    return candidates


def _pool_sg(candidates: list[dict], take: int = 5) -> list[dict]:
    """SG 池 —— 5年营收增长率代理：年初至今涨幅前 10%，过滤 PE≤0，按流通市值升序。

    聚宽原版使用 sales_growth（5年营收增长率）因子，AKShare 无此因子，
    用「年初至今涨跌幅」作为长期增长趋势的代理。
    """
    # 过滤 PE > 0（盈利公司）且有 YTD 数据
    pool = [q for q in candidates if q["pe_ratio"] > 0 and q["change_pct_ytd"] != 0]
    if not pool:
        return []

    # 按年初至今涨幅降序
    pool.sort(key=lambda x: x["change_pct_ytd"], reverse=True)
    top10pct = pool[:max(1, len(pool) // 10)]

    # 按流通市值升序（偏好小市值）
    top10pct.sort(key=lambda x: x.get("circulating_market_cap", 1e18))

    selected = top10pct[:take]
    logger.info("[SG池] 候选 %d → 前10%% %d → 选中 %d", len(pool), len(top10pct), len(selected))
    return selected


def _pool_ms(candidates: list[dict], take: int = 5) -> list[dict]:
    """MS 池 —— 复合成长因子：加权评分前 10%，过滤 PE≤0，按流通市值升序。

    聚宽原版权重：营收增长率×0.10 + 利润总额增长率×0.35 + 净利润增长率×0.15 + 5年盈利增长率×0.40
    AKShare 适配权重（使用可用的批量数据）：
      - 60日涨跌幅 × 0.35（中期动量 ≈ 利润增长代理）
      - 年初至今涨跌幅 × 0.40（长期趋势 ≈ 5年盈利增长代理）
      - 1/PE × 0.15（盈利收益率 ≈ 净利润质量代理）
      - 量比 × 0.10（资金活跃度）
    """
    pool = [q for q in candidates if q["pe_ratio"] > 0]
    if not pool:
        return []

    # 计算百分位排名用于归一化
    df = pd.DataFrame(pool)
    df["rank_60d"] = df["change_pct_60d"].rank(pct=True, na_option="bottom")
    df["rank_ytd"] = df["change_pct_ytd"].rank(pct=True, na_option="bottom")
    df["rank_ep"] = (1.0 / df["pe_ratio"]).rank(pct=True, na_option="bottom")  # 盈利收益率
    df["rank_vr"] = df["volume_ratio"].rank(pct=True, na_option="bottom")

    df["total_score"] = (
        0.35 * df["rank_60d"]
        + 0.40 * df["rank_ytd"]
        + 0.15 * df["rank_ep"]
        + 0.10 * df["rank_vr"]
    )

    # 取前 10%
    df = df.sort_values("total_score", ascending=False)
    top10pct = df.head(max(1, len(df) // 10))

    # 按流通市值升序
    top10pct = top10pct.sort_values("circulating_market_cap", ascending=True)

    selected_symbols = set(top10pct["symbol"].tolist()[:take])
    selected = [q for q in pool if q["symbol"] in selected_symbols]
    # 保持流通市值排序
    selected.sort(key=lambda x: x.get("circulating_market_cap", 1e18))
    selected = selected[:take]

    logger.info("[MS池] 候选 %d → 前10%% %d → 选中 %d", len(pool), len(top10pct), len(selected))
    return selected


def _pool_peg(candidates: list[dict], take: int = 5) -> list[dict]:
    """PEG 池 —— 低 PEG + 低换手波动，按流通市值升序。

    聚宽原版：PEG 因子升序前 20% → turnover_volatility 升序前 50%
    AKShare 适配：伪 PEG = PE / max(年涨幅, 1)，换手率升序代替换手波动率。
    """
    # 过滤：PE > 0 且年涨幅 > 5%（有正增长才有 PEG 含义）
    pool = [q for q in candidates if q["pe_ratio"] > 0 and q["change_pct_ytd"] > 5]
    if not pool:
        return []

    # 计算伪 PEG
    for q in pool:
        q["_peg"] = q["pe_ratio"] / max(q["change_pct_ytd"], 1.0)

    # PEG 升序取前 20%
    pool.sort(key=lambda x: x["_peg"])
    top20pct = pool[:max(1, len(pool) // 5)]

    # 换手率升序取前 50%（低换手 = 筹码稳定）
    top20pct.sort(key=lambda x: x["turnover_ratio"])
    top50pct = top20pct[:max(1, len(top20pct) // 2)]

    # 按流通市值升序
    top50pct.sort(key=lambda x: x.get("circulating_market_cap", 1e18))

    selected = top50pct[:take]
    logger.info("[PEG池] 候选 %d → 前20%% %d → 低换手50%% %d → 选中 %d",
                len(pool), len(top20pct), len(top50pct), len(selected))

    # 清理临时字段
    for q in pool:
        q.pop("_peg", None)
    return selected


def _generate_target_pool_from_quotes(
    strategy_config: dict, all_quotes: list[dict], count: int = 10,
    blacklist: set[str] | None = None,
) -> list[dict]:
    """三因子选股：SG + MS + PEG 取并集 → 按流通市值升序 → 截取 stock_count。

    对标聚宽策略的 get_stock_list + weekly_adjustment 选股逻辑。
    """
    pool_size = strategy_config.get("stock_count", count)
    filters = strategy_config.get("filters", {})
    blacklist = blacklist or set()

    if not all_quotes:
        logger.warning("[选股] 行情数据为空，返回空池")
        return []

    # 1. 基础过滤
    candidates = _filter_basic(all_quotes, filters)
    logger.info("[选股] 基础过滤：全市场 %d → 候选 %d", len(all_quotes), len(candidates))

    # 2. 三池选股
    sg_list = _pool_sg(candidates, take=5)
    ms_list = _pool_ms(candidates, take=5)
    peg_list = _pool_peg(candidates, take=5)

    # 3. 取并集
    seen: set[str] = set()
    union_list: list[dict] = []
    for q in sg_list + ms_list + peg_list:
        if q["symbol"] not in seen:
            seen.add(q["symbol"])
            union_list.append(q)

    # 4. 按流通市值升序排列
    union_list.sort(key=lambda x: x.get("circulating_market_cap", 1e18))

    # 5. 过滤黑名单
    if blacklist:
        union_list = [q for q in union_list if q["symbol"] not in blacklist]

    # 6. 截取不超过 stock_count
    selected = union_list[:pool_size]

    logger.info(
        "[选股] 三池并集 %d → 去黑名单后 %d → 最终选中 %d",
        len(seen), len(union_list), len(selected),
    )
    for s in selected:
        cap_wan = s.get("circulating_market_cap", 0) / 10000
        logger.info(
            "  [目标] %s %s 现价 %.2f 涨跌 %.2f%% 流通市值 %.0f万",
            s["symbol"], s["name"], s["price"], s["change_pct"], cap_wan,
        )

    return [
        {
            "symbol": s["symbol"],
            "name": s["name"],
            "price": s["price"],
            "prev_close": s.get("prev_close", 0.0),
            "volume": s.get("volume", 0.0),
        }
        for s in selected
    ]


def _generate_target_pool(strategy_config: dict, count: int = 10) -> list[dict]:
    """拉取行情 + 选股（便捷入口）。"""
    all_quotes = _fetch_realtime_quotes()
    return _generate_target_pool_from_quotes(strategy_config, all_quotes, count)


async def execute_daily_rotation(session: AsyncSession) -> dict:
    """执行每日轮动调仓（核心入口）。

    流程：
      1. 查询所有 active + 有 strategy_config 的研究员
      2. 对每个研究员：
         a. 生成今日目标持仓池
         b. 卖出不在目标池中的持仓
         c. 买入新目标（等权分配可用资金）
      3. 更新研究员 today_pnl
    """
    # 查询需要执行策略的研究员
    stmt = select(Researcher).where(
        Researcher.status == "active",
        Researcher.strategy_config.isnot(None),
    )
    result = await session.execute(stmt)
    researchers = list(result.scalars().all())

    if not researchers:
        logger.info("[策略引擎] 没有需要执行策略的研究员")
        return {"status": "skip", "reason": "no_active_researchers"}

    total_trades = 0
    details = []

    for r in researchers:
        try:
            trades = await _execute_for_researcher(session, r)
            total_trades += trades
            details.append({"researcher": r.name, "trades": trades})
            logger.info("[策略引擎] %s 执行完成，成交 %d 笔", r.name, trades)
        except Exception as e:
            logger.error("[策略引擎] %s 执行失败: %s", r.name, e)
            details.append({"researcher": r.name, "error": str(e)})

    return {
        "status": "ok",
        "total_trades": total_trades,
        "details": details,
        "executed_at": datetime.now(tz=UTC).isoformat(),
    }


def _gen_daily_summary(sell_count: int, buy_count: int, total_pnl: float,
                       total_asset: float, available_cash: float,
                       hold_names: list[str]) -> str:
    """生成每日操作总结"""
    lines = ["## 当前操作情况总结\n"]

    if sell_count + buy_count == 0:
        lines.append("今日无调仓操作，当前持仓符合目标池，继续持有。\n")
    else:
        lines.append(
            f"本次按照交易纪律完成了调仓操作：卖出 {sell_count} 笔，买入 {buy_count} 笔。\n"
        )

    if total_pnl >= 0:
        lines.append(f"今日策略盈亏 **+{total_pnl:,.2f} 元**，整体运行正常。\n")
    else:
        lines.append(f"今日策略盈亏 **{total_pnl:,.2f} 元**，在风控容忍范围内。\n")

    lines.append(
        f"当前账户总资产 {total_asset:,.2f} 元，可用资金 {available_cash:,.2f} 元。"
    )

    if hold_names:
        lines.append(
            f"\n\n当前持仓 {len(hold_names)} 只：{'、'.join(hold_names)}，"
            f"均符合小市值轮动策略选股条件，继续持有观察。"
        )

    return "\n".join(lines)


def _invalidate_trading_cache(account: TradingAccount, researcher_id: str) -> None:
    from app.modules.trading.service import TradingService

    TradingService._cache_invalidate(
        [
            f"account:{account.user_id}:{researcher_id}",
            f"positions:{account.id}",
            f"replay:{account.id}",
            f"stats:{account.id}",
        ]
    )


async def _load_today_buy_quantities(session: AsyncSession, account_id: str) -> dict[str, int]:
    today = datetime.now().date()
    start_at = datetime.combine(today, datetime.min.time())
    end_at = start_at + timedelta(days=1)
    stmt = (
        select(TradeRecord)
        .where(
            TradeRecord.account_id == account_id,
            TradeRecord.side == "buy",
            TradeRecord.created_at >= start_at,
            TradeRecord.created_at < end_at,
        )
        .order_by(TradeRecord.created_at.asc(), TradeRecord.id.asc())
    )
    result = await session.execute(stmt)
    quantities: dict[str, int] = defaultdict(int)
    for record in result.scalars().all():
        quantities[record.symbol] += int(record.quantity)
    return dict(quantities)


async def _execute_for_researcher(session: AsyncSession, researcher: Researcher) -> int:
    """为单个研究员执行调仓，返回成交笔数。同时写入交易日志（TradeLog）。

    调仓逻辑（对标聚宽策略）：
      - 每周一：全量调仓（三因子选股 + 卖出 + 买入）
      - 其他交易日：仅执行止损检查 + 更新持仓现价
      - 每日：更新涨停追踪状态与黑名单
    """
    config = researcher.strategy_config or {}
    cost_config = config.get("cost", {})
    open_commission_rate = cost_config.get("open_commission", 0.0003)
    close_commission_rate = cost_config.get("close_commission", 0.0003)
    close_tax_rate = cost_config.get("close_tax", 0.001)
    min_commission = cost_config.get("min_commission", 5)
    stop_loss = config.get("risk_control", {}).get("stop_loss", -0.10)
    rid = researcher.id

    # ── 判断今天是否为周一（全量调仓日） ──
    from zoneinfo import ZoneInfo
    now_shanghai = datetime.now(tz=ZoneInfo("Asia/Shanghai"))
    today_str = now_shanghai.strftime("%Y-%m-%d")
    is_monday = now_shanghai.weekday() == 0
    is_rotation_day = is_monday and _last_rotation_date.get(rid) != today_str

    # 查找模拟账户（若不存在则自动创建）
    acct_stmt = select(TradingAccount).where(
        TradingAccount.researcher_id == researcher.id
    )
    acct_result = await session.execute(acct_stmt)
    account = acct_result.scalar_one_or_none()
    if not account:
        logger.info("[策略引擎] %s 没有模拟账户，自动创建（初始资金 100 万）", researcher.name)
        initial_cash = 1_000_000.0
        account = TradingAccount(
            id=f"acct_{uuid4().hex[:10]}",
            user_id=researcher.owner_id,
            researcher_id=researcher.id,
            total_asset=initial_cash,
            available_cash=initial_cash,
            holding_value=0.0,
            daily_pnl=0.0,
        )
        session.add(account)
        await session.flush()

    # 查找当前持仓
    pos_stmt = select(Position).where(Position.account_id == account.id)
    pos_result = await session.execute(pos_stmt)
    current_positions = {p.symbol: p for p in pos_result.scalars().all()}
    hold_symbols = list(current_positions.keys())

    # ── 更新持仓历史 & 涨停黑名单 ──
    _hold_history[rid].append(hold_symbols)
    if len(_hold_history[rid]) > _BLACKLIST_DAYS:
        _hold_history[rid] = _hold_history[rid][-_BLACKLIST_DAYS:]
    temp_set: set[str] = set()
    for hl in _hold_history[rid]:
        temp_set.update(hl)
    _not_buy_again[rid] = temp_set

    # 拉取全市场实时行情（用于选股 + 卖出/持仓现价更新）
    all_quotes = _fetch_realtime_quotes()
    realtime_quote_map: dict[str, dict] = {q["symbol"]: q for q in all_quotes}
    realtime_price_map: dict[str, float] = {q["symbol"]: q["price"] for q in all_quotes}
    realtime_change_map: dict[str, float] = {q["symbol"]: q["change_pct"] for q in all_quotes}

    # ── 识别昨日涨停持仓（涨跌幅 >= 9.8%，用当日开盘时行情近似） ──
    high_limit_list: list[str] = []
    for sym in hold_symbols:
        chg = realtime_change_map.get(sym, 0)
        if chg >= 9.8:
            high_limit_list.append(sym)
    _limit_up_symbols[rid] = high_limit_list
    if high_limit_list:
        logger.info("[策略引擎] %s 涨停持仓: %s", researcher.name, high_limit_list)

    trade_count = 0
    daily_pnl = 0.0
    sell_count = 0
    buy_count = 0

    if is_rotation_day:
        # ══════ 周一全量调仓 ══════
        logger.info("[策略引擎] %s 执行周一全量调仓", researcher.name)
        _last_rotation_date[rid] = today_str

        # 生成今日目标池（含黑名单过滤）
        target_pool = _generate_target_pool_from_quotes(
            config, all_quotes, blacklist=_not_buy_again[rid],
        )
        target_symbols = {t["symbol"] for t in target_pool}

        # ── 卖出：不在目标池 且 非涨停的持仓 ──
        for symbol, pos in list(current_positions.items()):
            if symbol not in target_symbols and symbol not in high_limit_list:
                quote = realtime_quote_map.get(symbol)
                sell_price = realtime_price_map.get(symbol, pos.current_price)
                sc, pnl = await _do_sell(
                    session,
                    researcher,
                    account,
                    pos,
                    sell_price,
                    quote,
                    close_commission_rate,
                    close_tax_rate,
                    min_commission,
                    "轮动调出目标池，执行卖出",
                )
                trade_count += sc
                sell_count += sc
                daily_pnl += pnl
                if sc > 0:
                    del current_positions[symbol]

        # ── 买入新目标 ──
        new_targets = [t for t in target_pool if t["symbol"] not in current_positions]
        if new_targets and account.available_cash > 1000:
            stock_count = config.get("stock_count", 10)
            position_count = len(current_positions)
            if stock_count > position_count:
                per_stock_budget = account.available_cash / (stock_count - position_count)
                for target in new_targets:
                    bc, pnl = await _do_buy(
                        session,
                        researcher,
                        account,
                        target,
                        per_stock_budget,
                        open_commission_rate,
                        min_commission,
                    )
                    trade_count += bc
                    buy_count += bc
                    daily_pnl += pnl
                    if len(current_positions) + buy_count >= stock_count:
                        break
    else:
        # ══════ 非调仓日：仅执行止损检查 ══════
        logger.info("[策略引擎] %s 非调仓日，执行止损检查", researcher.name)
        for symbol, pos in list(current_positions.items()):
            cur_price = realtime_price_map.get(symbol, pos.current_price)
            if pos.cost_price > 0:
                pnl_pct = (cur_price - pos.cost_price) / pos.cost_price
                if pnl_pct <= stop_loss:
                    reason = f"触发止损线（当前亏损 {pnl_pct:.1%}，止损阈值 {stop_loss:.0%}）"
                    sc, pnl = await _do_sell(
                        session,
                        researcher,
                        account,
                        pos,
                        cur_price,
                        realtime_quote_map.get(symbol),
                        close_commission_rate,
                        close_tax_rate,
                        min_commission,
                        reason,
                    )
                    trade_count += sc
                    sell_count += sc
                    daily_pnl += pnl
                    if sc > 0:
                        del current_positions[symbol]

    # ── 更新现有持仓的现价（使用真实行情） ──
    for symbol, pos in current_positions.items():
        new_price = realtime_price_map.get(symbol, pos.current_price)
        old_pnl = pos.pnl
        pos.current_price = new_price
        pos.pnl = round((new_price - pos.cost_price) * pos.quantity, 2)
        daily_pnl += pos.pnl - old_pnl

    # ── 更新账户汇总 ──
    all_pos_stmt = select(Position).where(Position.account_id == account.id)
    all_pos_result = await session.execute(all_pos_stmt)
    all_positions = list(all_pos_result.scalars().all())
    account.holding_value = sum(p.current_price * p.quantity for p in all_positions)
    account.total_asset = account.available_cash + account.holding_value
    account.daily_pnl = round(daily_pnl, 2)
    researcher.today_pnl = round(daily_pnl, 2)

    # ── 写每日操作总结日志 ──
    hold_names = [p.name for p in all_positions]
    session.add(TradeLog(
        id=f"tl_{uuid4().hex[:8]}",
        account_id=account.id,
        log_type="analysis",
        trade_record_ids="[]",
        title="当前操作情况总结",
        content=_gen_daily_summary(
            sell_count, buy_count, daily_pnl,
            account.total_asset, account.available_cash, hold_names,
        ),
    ))

    await session.commit()
    _invalidate_trading_cache(account, researcher.id)
    return trade_count


# ════════════════════════════════════════════════════════════
# 交易执行辅助函数
# ════════════════════════════════════════════════════════════

async def _do_sell(
    session: AsyncSession,
    researcher: Researcher,
    account: TradingAccount,
    pos: Position,
    sell_price: float,
    market_quote: dict | None,
    comm_rate: float,
    tax_rate: float,
    min_comm: float,
    reason: str,
) -> tuple[int, float]:
    """执行卖出，返回 (成交笔数, 盈亏金额)。"""
    today_buy_quantities = await _load_today_buy_quantities(session, account.id)
    sellable_quantity = compute_sellable_quantity(
        int(pos.quantity),
        today_buy_quantities.get(pos.symbol, 0),
    )
    execution = execute_stock_order(
        account=account,
        existing_position=pos,
        symbol=pos.symbol,
        name=pos.name,
        side="sell",
        quantity=int(pos.quantity),
        limit_price=float(sell_price),
        market=MarketSnapshot(
            price=float(sell_price),
            prev_close=float(market_quote.get("prev_close", 0.0)) if market_quote else None,
            volume=float(market_quote.get("volume", 0.0)) if market_quote else None,
        ),
        sellable_quantity=sellable_quantity,
        open_commission_rate=comm_rate,
        close_commission_rate=comm_rate,
        close_tax_rate=tax_rate,
        min_commission=min_comm,
    )
    if execution.status != ORDER_STATUS_FILLED:
        logger.warning(
            "  [卖出跳过] %s %s: %s",
            pos.symbol,
            pos.name,
            execution.message,
        )
        return 0, 0.0

    amount = round(execution.amount, 2)
    fill_price = round(float(execution.fill_price or sell_price), 4)
    total_fee = execution.total_fee
    pnl = round(float(execution.realized_pnl or 0.0), 2)

    record_id = f"trd_{uuid4().hex[:8]}"
    session.add(
        TradeRecord(
            id=record_id,
            account_id=account.id,
            symbol=pos.symbol,
            name=pos.name,
            side="sell",
            quantity=execution.filled_quantity,
            price=fill_price,
            commission=total_fee,
        )
    )
    session.add(
        TradeLog(
            id=f"tl_{uuid4().hex[:8]}",
            account_id=account.id,
            log_type="trade",
            trade_record_ids=json.dumps([record_id]),
            title="",
            content="",
        )
    )
    reflection = await _trading_reflection_skill.build_trade_reflection(
        researcher_name=researcher.name,
        researcher_prompt=researcher.prompt,
        trade_context={
            "mode": "strategy",
            "strategy_type": "smallcap_rotation",
            "side": "sell",
            "symbol": pos.symbol,
            "name": pos.name,
            "price": fill_price,
            "quantity": execution.filled_quantity,
            "amount": amount,
            "commission": total_fee,
            "reason": reason,
            "cost_price": float(pos.cost_price),
            "realized_pnl": round(pnl, 2),
            "realized_pnl_pct": round(pnl / (float(pos.cost_price) * execution.filled_quantity), 4)
            if pos.cost_price > 0 and execution.filled_quantity > 0
            else None,
            "position_ratio": round(amount / 1_000_000.0, 4),
            "available_cash": float(account.available_cash),
            "total_asset": float(account.available_cash + account.holding_value),
        },
    )
    session.add(
        TradeLog(
            id=f"tl_{uuid4().hex[:8]}",
            account_id=account.id,
            log_type="analysis",
            trade_record_ids="[]",
            title=_trading_reflection_skill.build_trade_log_title(
                {"side": "sell", "name": pos.name, "symbol": pos.symbol}
            ),
            content=reflection,
        )
    )

    if execution.remove_position:
        await session.delete(pos)

    logger.info("  [卖出] %s %s %d股 @ %.2f (%s)", pos.symbol, pos.name, execution.filled_quantity, fill_price, reason)
    return 1, pnl


async def _do_buy(
    session: AsyncSession,
    researcher: Researcher,
    account: TradingAccount,
    target: dict,
    budget: float,
    comm_rate: float,
    min_comm: float,
) -> tuple[int, float]:
    """执行买入，返回 (成交笔数, 佣金负值)。"""
    buy_price = target["price"]
    max_quantity = int(budget / buy_price / 100) * 100
    if max_quantity < 100:
        return 0, 0.0

    execution = execute_stock_order(
        account=account,
        existing_position=None,
        symbol=target["symbol"],
        name=target["name"],
        side="buy",
        quantity=max_quantity,
        limit_price=float(buy_price),
        market=MarketSnapshot(
            price=float(target.get("price", buy_price)),
            prev_close=float(target.get("prev_close", 0.0)) or None,
            volume=float(target.get("volume", 0.0)) or None,
        ),
        sellable_quantity=None,
        open_commission_rate=comm_rate,
        close_commission_rate=comm_rate,
        close_tax_rate=0.001,
        min_commission=min_comm,
    )
    if execution.status != ORDER_STATUS_FILLED:
        logger.warning(
            "  [买入跳过] %s %s: %s",
            target["symbol"],
            target["name"],
            execution.message,
        )
        return 0, 0.0

    amount = round(execution.amount, 2)
    fill_price = round(float(execution.fill_price or buy_price), 4)
    if not execution.created_position:
        return 0, 0.0

    session.add(
        Position(
            id=f"pos_{uuid4().hex[:8]}",
            account_id=account.id,
            symbol=target["symbol"],
            name=target["name"],
            quantity=int(execution.created_position["quantity"]),
            cost_price=float(execution.created_position["cost_price"]),
            current_price=float(execution.created_position["current_price"]),
            pnl=float(execution.created_position["pnl"]),
        )
    )

    record_id = f"trd_{uuid4().hex[:8]}"
    session.add(
        TradeRecord(
            id=record_id,
            account_id=account.id,
            symbol=target["symbol"],
            name=target["name"],
            side="buy",
            quantity=execution.filled_quantity,
            price=fill_price,
            commission=execution.total_fee,
        )
    )
    session.add(
        TradeLog(
            id=f"tl_{uuid4().hex[:8]}",
            account_id=account.id,
            log_type="trade",
            trade_record_ids=json.dumps([record_id]),
            title="",
            content="",
        )
    )
    position_ratio = round(amount / 1_000_000.0, 4)
    reflection = await _trading_reflection_skill.build_trade_reflection(
        researcher_name=researcher.name,
        researcher_prompt=researcher.prompt,
        trade_context={
            "mode": "strategy",
            "strategy_type": "smallcap_rotation",
            "side": "buy",
            "symbol": target["symbol"],
            "name": target["name"],
            "price": fill_price,
            "quantity": execution.filled_quantity,
            "amount": amount,
            "commission": execution.total_fee,
            "reason": "符合小市值轮动目标池，按策略纪律执行调入",
            "position_ratio": position_ratio,
            "available_cash": float(account.available_cash),
            "total_asset": float(account.available_cash + account.holding_value),
        },
    )
    session.add(
        TradeLog(
            id=f"tl_{uuid4().hex[:8]}",
            account_id=account.id,
            log_type="analysis",
            trade_record_ids="[]",
            title=_trading_reflection_skill.build_trade_log_title(
                {"side": "buy", "name": target["name"], "symbol": target["symbol"]}
            ),
            content=reflection,
        )
    )

    logger.info("  [买入] %s %s %d股 @ %.2f", target["symbol"], target["name"], execution.filled_quantity, fill_price)
    return 1, -execution.total_fee


async def check_limit_up(session: AsyncSession) -> dict:
    """14:00 涨停检查入口 —— 对所有研究员的涨停持仓进行检查。

    对标聚宽 check_limit_up：如果昨日涨停股票当前不再涨停，立即卖出。
    """
    stmt = select(Researcher).where(
        Researcher.status == "active",
        Researcher.strategy_config.isnot(None),
    )
    result = await session.execute(stmt)
    researchers = list(result.scalars().all())
    total_sold = 0
    affected: list[tuple[TradingAccount, str]] = []

    for r in researchers:
        rid = r.id
        limit_symbols = _limit_up_symbols.get(rid, [])
        if not limit_symbols:
            continue

        config = r.strategy_config or {}
        cost_config = config.get("cost", {})
        close_commission_rate = cost_config.get("close_commission", 0.0003)
        close_tax_rate = cost_config.get("close_tax", 0.001)
        min_commission = cost_config.get("min_commission", 5)

        acct_stmt = select(TradingAccount).where(TradingAccount.researcher_id == rid)
        acct_result = await session.execute(acct_stmt)
        account = acct_result.scalar_one_or_none()
        if not account:
            continue

        all_quotes = _fetch_realtime_quotes()
        realtime_map = {q["symbol"]: q for q in all_quotes}

        pos_stmt = select(Position).where(Position.account_id == account.id)
        pos_result = await session.execute(pos_stmt)
        positions = {p.symbol: p for p in pos_result.scalars().all()}

        for sym in limit_symbols:
            pos = positions.get(sym)
            if not pos:
                continue
            q = realtime_map.get(sym)
            if not q:
                continue
            cur_price = q["price"]
            # 如果当前不再涨停（涨幅 < 9.8%），卖出
            if q["change_pct"] < 9.8:
                logger.info("[涨停检查] %s(%s) 涨停打开，卖出", pos.name, sym)
                sold_count, _ = await _do_sell(
                    session,
                    r,
                    account,
                    pos,
                    cur_price,
                    q,
                    close_commission_rate,
                    close_tax_rate,
                    min_commission,
                    "涨停打开，执行卖出",
                )
                total_sold += sold_count
                if sold_count > 0:
                    affected.append((account, r.id))
            else:
                logger.info("[涨停检查] %s(%s) 继续涨停，持有", pos.name, sym)

    if total_sold > 0:
        await session.commit()
        for account, researcher_id in affected:
            _invalidate_trading_cache(account, researcher_id)

    return {"status": "ok", "sold_count": total_sold}
