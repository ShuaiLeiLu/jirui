"""
模拟交易 Schema

包含：
  - TradingAccount: 模拟账户概况
  - PositionItem: 持仓明细
  - TradeRecord: 成交记录
  - PlaceOrderRequest: 下单请求
  - PlaceOrderResponse: 下单结果
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.common import SchemaModel

TradeSide = Literal["buy", "sell"]  # 买入 / 卖出
DEFAULT_INITIAL_CAPITAL = 1_000_000.0


class TradingAccount(SchemaModel):
    """模拟账户概况"""
    account_id: str
    initial_capital: float = DEFAULT_INITIAL_CAPITAL
    total_asset: float       # 总资产
    available_cash: float    # 可用资金
    holding_value: float     # 持仓市值
    daily_pnl: float         # 今日盈亏


class PositionItem(SchemaModel):
    """持仓明细"""
    symbol: str              # 股票代码
    name: str                # 股票名称
    quantity: int            # 持有数量
    sellable_quantity: int | None = None  # T+1 可卖数量
    cost_price: float        # 成本价
    current_price: float     # 当前价
    pnl: float               # 持仓盈亏


class TradeRecord(SchemaModel):
    """成交记录"""
    trade_id: str
    symbol: str
    name: str = ""           # 股票名称
    side: TradeSide
    quantity: int
    price: float
    amount: float = 0.0      # 成交金额 = price * quantity
    commission: float = 0.0  # 手续费
    cost_price: float | None = None
    realized_pnl: float | None = None
    realized_pnl_pct: float | None = None
    hold_days: float | None = None
    position_ratio: float | None = None
    created_at: datetime


class TradeLogItem(SchemaModel):
    """交易日志条目"""
    log_id: str
    log_type: str              # trade / analysis
    trade_records: list[TradeRecord] = []  # log_type=trade 时关联的成交记录
    title: str = ""
    content: str = ""          # Markdown 富文本
    created_at: datetime


class EquityPoint(SchemaModel):
    """收益曲线数据点"""
    date: str                  # YYYY-MM-DD
    equity: float              # 当日权益
    benchmark: float = 0.0     # 基准（沪深300）收益率，暂未接入

class MonthlyReturn(SchemaModel):
    """月度收益"""
    month: str                 # YYYY-MM
    pnl: float                 # 月度盈亏金额
    pct: float                 # 月度收益率

class DailyReturn(SchemaModel):
    """每日收益（投资日历用）"""
    date: str                  # YYYY-MM-DD
    pnl: float                 # 当日盈亏

class RiskMetrics(SchemaModel):
    """风控指标"""
    total_return: float        # 累计收益率
    annual_return: float       # 年化收益率
    max_drawdown: float        # 最大回撤
    sharpe: float              # 夏普比率
    win_rate: float            # 交易胜率
    profit_loss_ratio: float   # 盈亏比
    total_trades: int          # 总交易笔数
    win_trades: int            # 盈利笔数
    lose_trades: int           # 亏损笔数
    max_profit: float          # 最大单笔盈利
    max_loss: float            # 最大单笔亏损
    avg_hold_days: float       # 平均持仓天数

class TradingStats(SchemaModel):
    """历史交易统计（聚合数据，供前端图表使用）"""
    initial_capital: float             # 初始资金
    total_asset: float                 # 当前总资产
    equity_curve: list[EquityPoint]    # 收益曲线
    monthly_returns: list[MonthlyReturn]  # 月度收益
    daily_returns: list[DailyReturn]   # 日收益序列
    risk: RiskMetrics                  # 风控指标


class TradingAllData(SchemaModel):
    """模拟盘聚合数据 —— 一次请求返回全部页面所需内容。"""
    account: TradingAccount
    positions: list[PositionItem]
    records: list[TradeRecord]
    logs: list[TradeLogItem]


class TradingStreamSnapshot(SchemaModel):
    """交易实时快照（SSE 推送）。"""
    generated_at: datetime
    account: TradingAccount
    positions: list[PositionItem]


class PlaceOrderRequest(SchemaModel):
    """下单请求"""
    researcher_id: str = Field(default="", description="研究员ID，指定哪个模拟盘下单")
    symbol: str = Field(min_length=1, max_length=10, description="股票代码")
    name: str = Field(default="", max_length=20, description="股票名称")
    side: TradeSide = Field(description="买入/卖出")
    quantity: int = Field(gt=0, description="下单数量（股）")
    price: float = Field(gt=0, description="委托价格")


class PlaceOrderResponse(SchemaModel):
    """下单结果"""
    trade_id: str
    symbol: str
    side: TradeSide
    quantity: int
    filled_quantity: int
    price: float
    amount: float            # 成交金额
    commission: float = 0.0
    tax: float = 0.0
    realized_pnl: float | None = None
    status: str = "FILLED"
    engine: str = ""
    message: str             # 执行结果描述
