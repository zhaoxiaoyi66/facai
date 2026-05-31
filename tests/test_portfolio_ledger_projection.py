from __future__ import annotations

from data.portfolio_ledger_projection import project_positions_from_trade_rows
from data.portfolio_ledger_projection import project_trade_effect


def test_ledger_projection_applies_weighted_buy_and_sell_without_cost_drift() -> None:
    first = project_trade_effect(
        current_quantity=10,
        current_average_cost=100,
        action_type="add",
        quantity=10,
        price=200,
    )
    second = project_trade_effect(
        current_quantity=first["afterQuantity"],
        current_average_cost=first["afterAverageCost"],
        action_type="sell",
        quantity=5,
        price=250,
    )

    assert first["afterQuantity"] == 20
    assert first["afterAverageCost"] == 150
    assert second["afterQuantity"] == 15
    assert second["afterAverageCost"] == 150


def test_ledger_projection_rejects_sell_above_current_position() -> None:
    result = project_trade_effect(
        current_quantity=3,
        current_average_cost=400,
        action_type="trim",
        quantity=5,
        price=450,
    )

    assert result["status"] == "failed"
    assert "超过当前组合持仓" in result["error"]


def test_project_positions_from_synced_trade_rows_uses_same_ledger_math() -> None:
    rows = [
        {"symbol": "NVDA", "action_type": "buy", "quantity": 10, "price": 100},
        {"symbol": "NVDA", "action_type": "add", "quantity": 10, "price": 200},
        {"symbol": "NVDA", "action_type": "sell", "quantity": 4, "price": 250},
    ]

    projected = project_positions_from_trade_rows(rows)

    assert projected["NVDA"]["quantity"] == 16
    assert projected["NVDA"]["average_cost"] == 150
