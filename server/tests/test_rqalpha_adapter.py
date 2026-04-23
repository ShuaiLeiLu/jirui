from __future__ import annotations

from types import SimpleNamespace

from app.modules.trading.rqalpha_adapter import (
    ORDER_STATUS_FILLED,
    ORDER_STATUS_REJECTED,
    MarketSnapshot,
    execute_stock_order,
)


def _account(*, available_cash: float = 1_000_000.0) -> SimpleNamespace:
    return SimpleNamespace(
        available_cash=available_cash,
        holding_value=0.0,
        daily_pnl=0.0,
    )


def _position(
    *,
    quantity: int = 100,
    cost_price: float = 10.0,
    current_price: float = 10.0,
) -> SimpleNamespace:
    return SimpleNamespace(
        quantity=quantity,
        cost_price=cost_price,
        current_price=current_price,
        pnl=0.0,
    )


def test_buy_order_uses_market_price_and_updates_account() -> None:
    account = _account()

    result = execute_stock_order(
        account=account,
        existing_position=None,
        symbol="600000",
        name="浦发银行",
        side="buy",
        quantity=100,
        limit_price=10.50,
        market=MarketSnapshot(price=10.00, prev_close=9.80, volume=20_000),
        sellable_quantity=None,
        open_commission_rate=0.0003,
        close_commission_rate=0.0003,
        close_tax_rate=0.001,
        min_commission=5.0,
    )

    assert result.status == ORDER_STATUS_FILLED
    assert result.fill_price == 10.00
    assert result.commission == 5.0
    assert account.available_cash == 998_995.0
    assert account.holding_value == 1_000.0
    assert result.created_position == {
        "symbol": "600000",
        "name": "浦发银行",
        "quantity": 100,
        "cost_price": 10.05,
        "current_price": 10.0,
        "pnl": -5.0,
    }


def test_sell_order_respects_t_plus_one_restriction() -> None:
    account = _account(available_cash=100_000.0)
    position = _position(quantity=200, cost_price=10.0, current_price=10.0)

    result = execute_stock_order(
        account=account,
        existing_position=position,
        symbol="600000",
        name="浦发银行",
        side="sell",
        quantity=200,
        limit_price=10.00,
        market=MarketSnapshot(price=10.00, prev_close=9.90, volume=20_000),
        sellable_quantity=100,
        open_commission_rate=0.0003,
        close_commission_rate=0.0003,
        close_tax_rate=0.001,
        min_commission=5.0,
    )

    assert result.status == ORDER_STATUS_REJECTED
    assert "T+1" in result.message


def test_buy_order_rejects_limit_up_board() -> None:
    account = _account()

    result = execute_stock_order(
        account=account,
        existing_position=None,
        symbol="600000",
        name="浦发银行",
        side="buy",
        quantity=100,
        limit_price=11.00,
        market=MarketSnapshot(price=11.00, prev_close=10.00, volume=20_000),
        sellable_quantity=None,
        open_commission_rate=0.0003,
        close_commission_rate=0.0003,
        close_tax_rate=0.001,
        min_commission=5.0,
    )

    assert result.status == ORDER_STATUS_REJECTED
    assert "涨停" in result.message


def test_sell_order_updates_cash_and_marks_position_for_removal() -> None:
    account = _account(available_cash=100_000.0)
    position = _position(quantity=100, cost_price=10.0, current_price=10.0)

    result = execute_stock_order(
        account=account,
        existing_position=position,
        symbol="600000",
        name="浦发银行",
        side="sell",
        quantity=100,
        limit_price=10.00,
        market=MarketSnapshot(price=10.20, prev_close=10.00, volume=20_000),
        sellable_quantity=100,
        open_commission_rate=0.0003,
        close_commission_rate=0.0003,
        close_tax_rate=0.001,
        min_commission=5.0,
    )

    assert result.status == ORDER_STATUS_FILLED
    assert result.fill_price == 10.20
    assert result.remove_position is True
    assert result.realized_pnl == 13.98
    assert account.available_cash == 101_013.98
