"""RQAlpha adapter for the local paper-trading engine.

This module does not spin up the full RQAlpha runtime on every request.
Instead it aligns the app's execution rules with RQAlpha's paper-trading
concepts so the current FastAPI APIs can keep working while we migrate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from rqalpha.const import ORDER_STATUS, RUN_TYPE

    ORDER_STATUS_ACTIVE = ORDER_STATUS.ACTIVE.value
    ORDER_STATUS_CANCELLED = ORDER_STATUS.CANCELLED.value
    ORDER_STATUS_FILLED = ORDER_STATUS.FILLED.value
    ORDER_STATUS_REJECTED = ORDER_STATUS.REJECTED.value
    RUN_TYPE_PAPER = RUN_TYPE.PAPER_TRADING.value
except Exception:  # pragma: no cover - fallback only used when rqalpha is unavailable
    ORDER_STATUS_ACTIVE = "ACTIVE"
    ORDER_STATUS_CANCELLED = "CANCELLED"
    ORDER_STATUS_FILLED = "FILLED"
    ORDER_STATUS_REJECTED = "REJECTED"
    RUN_TYPE_PAPER = "PAPER_TRADING"

RQALPHA_ENGINE_NAME = f"rqalpha-adapter/{RUN_TYPE_PAPER.lower()}"


@dataclass(slots=True)
class MarketSnapshot:
    price: float | None = None
    prev_close: float | None = None
    volume: float | None = None


@dataclass(slots=True)
class ExecutionResult:
    status: str
    message: str
    filled_quantity: int
    fill_price: float | None
    amount: float
    commission: float
    tax: float
    realized_pnl: float | None
    engine: str = RQALPHA_ENGINE_NAME
    created_position: dict[str, Any] | None = None
    remove_position: bool = False

    @property
    def total_fee(self) -> float:
        return round(self.commission + self.tax, 2)


def compute_sellable_quantity(position_quantity: int, today_bought_quantity: int) -> int:
    """A-share T+1: same-day bought shares cannot be sold."""
    return max(int(position_quantity) - int(today_bought_quantity), 0)


def _limit_ratio(symbol: str, name: str) -> float:
    stock_name = name.upper()
    if "ST" in stock_name:
        return 0.05
    if symbol.startswith(("300", "301", "688", "689")):
        return 0.20
    if len(symbol) == 6 and symbol.startswith(("4", "8")):
        return 0.30
    return 0.10


def _price_limits(symbol: str, name: str, prev_close: float | None) -> tuple[float, float] | None:
    if prev_close is None or prev_close <= 0:
        return None
    ratio = _limit_ratio(symbol, name)
    return round(prev_close * (1 + ratio), 2), round(prev_close * (1 - ratio), 2)


def _validate_lot_size(side: str, quantity: int, position_quantity: int, sellable_quantity: int | None) -> str | None:
    if quantity <= 0:
        return "委托数量必须大于 0"

    if side == "buy":
        if quantity % 100 != 0:
            return "A 股买入数量必须是 100 股的整数倍"
        return None

    if position_quantity <= 0:
        return "当前无可卖持仓"

    if sellable_quantity is not None and quantity > sellable_quantity:
        return f"T+1 限制：当前可卖 {sellable_quantity} 股"

    if quantity > position_quantity:
        return f"持仓不足：当前持仓 {position_quantity} 股"

    if quantity % 100 == 0:
        return None

    odd_lot = position_quantity % 100
    if quantity == position_quantity:
        return None
    if odd_lot > 0 and quantity == odd_lot:
        return None
    return "A 股卖出零股时，只允许卖出剩余不足 100 股的部分"


def _resolve_limit_fill(
    side: str,
    symbol: str,
    name: str,
    limit_price: float,
    market: MarketSnapshot | None,
) -> tuple[str, float | None, str]:
    if market is None or market.price is None or market.price <= 0:
        return ORDER_STATUS_FILLED, limit_price, "实时行情缺失，按委托价模拟成交"

    market_price = float(market.price)
    limits = _price_limits(symbol, name, market.prev_close)
    if limits:
        limit_up, limit_down = limits
        if side == "buy":
            if limit_price > limit_up:
                return ORDER_STATUS_REJECTED, None, f"委托价 {limit_price:.2f} 高于涨停价 {limit_up:.2f}"
            if market_price >= limit_up and limit_price >= limit_up:
                return ORDER_STATUS_REJECTED, None, f"{symbol} 当前处于涨停状态，无法按限价买入"
        else:
            if limit_price < limit_down:
                return ORDER_STATUS_REJECTED, None, f"委托价 {limit_price:.2f} 低于跌停价 {limit_down:.2f}"
            if market_price <= limit_down and limit_price <= limit_down:
                return ORDER_STATUS_REJECTED, None, f"{symbol} 当前处于跌停状态，无法按限价卖出"

    if market.volume is not None and market.volume <= 0:
        return ORDER_STATUS_CANCELLED, None, f"{symbol} 当前无成交量，模拟撮合已取消"

    if side == "buy":
        if limit_price < market_price:
            return ORDER_STATUS_ACTIVE, None, "当前仅支持可立即成交的限价单，买入价低于最新价"
    else:
        if limit_price > market_price:
            return ORDER_STATUS_ACTIVE, None, "当前仅支持可立即成交的限价单，卖出价高于最新价"

    return ORDER_STATUS_FILLED, market_price, "按最新价完成模拟成交"


def execute_stock_order(
    *,
    account: Any,
    existing_position: Any | None,
    symbol: str,
    name: str,
    side: str,
    quantity: int,
    limit_price: float,
    market: MarketSnapshot | None,
    sellable_quantity: int | None = None,
    open_commission_rate: float,
    close_commission_rate: float,
    close_tax_rate: float,
    min_commission: float,
) -> ExecutionResult:
    """Mutate account/position using RQAlpha-like paper-trading rules."""
    position_quantity = int(getattr(existing_position, "quantity", 0) or 0)
    validation_error = _validate_lot_size(side, quantity, position_quantity, sellable_quantity)
    if validation_error:
        return ExecutionResult(
            status=ORDER_STATUS_REJECTED,
            message=validation_error,
            filled_quantity=0,
            fill_price=None,
            amount=0.0,
            commission=0.0,
            tax=0.0,
            realized_pnl=None,
        )

    status, fill_price, fill_message = _resolve_limit_fill(side, symbol, name, limit_price, market)
    if status != ORDER_STATUS_FILLED or fill_price is None:
        return ExecutionResult(
            status=status,
            message=fill_message,
            filled_quantity=0,
            fill_price=None,
            amount=0.0,
            commission=0.0,
            tax=0.0,
            realized_pnl=None,
        )

    amount = round(fill_price * quantity, 2)
    current_price = round(float(market.price if market and market.price else fill_price), 4)

    if side == "buy":
        commission = round(max(amount * open_commission_rate, min_commission), 2)
        total_cost = round(amount + commission, 2)
        available_cash = round(float(account.available_cash), 2)
        if available_cash < total_cost:
            return ExecutionResult(
                status=ORDER_STATUS_REJECTED,
                message=f"可用资金不足：需要 {total_cost:.2f}，当前 {available_cash:.2f}",
                filled_quantity=0,
                fill_price=None,
                amount=0.0,
                commission=0.0,
                tax=0.0,
                realized_pnl=None,
            )

        account.available_cash = round(available_cash - total_cost, 2)
        account.holding_value = round(float(account.holding_value) + amount, 2)
        account.daily_pnl = round(float(account.daily_pnl) - commission, 2)

        if existing_position is not None:
            new_quantity = int(existing_position.quantity) + quantity
            new_cost = (
                (float(existing_position.cost_price) * int(existing_position.quantity)) + total_cost
            ) / new_quantity
            existing_position.quantity = new_quantity
            existing_position.cost_price = round(new_cost, 4)
            existing_position.current_price = current_price
            existing_position.pnl = round((current_price - new_cost) * new_quantity, 2)
            position_payload = None
        else:
            unit_cost = round(total_cost / quantity, 4)
            position_payload = {
                "symbol": symbol,
                "name": name or symbol,
                "quantity": quantity,
                "cost_price": unit_cost,
                "current_price": current_price,
                "pnl": round((current_price - unit_cost) * quantity, 2),
            }

        return ExecutionResult(
            status=ORDER_STATUS_FILLED,
            message=f"买入成功：{symbol} {quantity}股 @ {fill_price:.2f}（{fill_message}）",
            filled_quantity=quantity,
            fill_price=round(fill_price, 4),
            amount=amount,
            commission=commission,
            tax=0.0,
            realized_pnl=-commission,
            created_position=position_payload,
        )

    commission = round(max(amount * close_commission_rate, min_commission), 2)
    tax = round(amount * close_tax_rate, 2)
    realized_pnl = round(amount - commission - tax - float(existing_position.cost_price) * quantity, 2)

    account.available_cash = round(float(account.available_cash) + amount - commission - tax, 2)
    account.holding_value = round(float(account.holding_value) - amount, 2)
    account.daily_pnl = round(float(account.daily_pnl) + realized_pnl, 2)

    remaining_quantity = int(existing_position.quantity) - quantity
    if remaining_quantity <= 0:
        remove_position = True
    else:
        remove_position = False
        existing_position.quantity = remaining_quantity
        existing_position.current_price = current_price
        existing_position.pnl = round(
            (current_price - float(existing_position.cost_price)) * remaining_quantity,
            2,
        )

    return ExecutionResult(
        status=ORDER_STATUS_FILLED,
        message=f"卖出成功：{symbol} {quantity}股 @ {fill_price:.2f}（{fill_message}）",
        filled_quantity=quantity,
        fill_price=round(fill_price, 4),
        amount=amount,
        commission=commission,
        tax=tax,
        realized_pnl=realized_pnl,
        remove_position=remove_position,
    )
