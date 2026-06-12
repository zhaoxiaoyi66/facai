from __future__ import annotations

from typing import Any, Iterable


QUANTITY_TOLERANCE = 1e-9
POSITION_AFFECTING_ACTIONS = {"buy", "add", "sell", "trim"}
BUY_ACTIONS = {"buy", "add"}
SELL_ACTIONS = {"sell", "trim"}


def project_trade_effect(
    *,
    current_quantity: float,
    current_average_cost: float,
    action_type: str,
    quantity: float | None,
    price: float | None,
) -> dict[str, Any]:
    action = str(action_type or "").strip().lower()
    base = {
        "status": "ready",
        "quantityDelta": 0.0,
        "afterQuantity": round(float(current_quantity or 0.0), 8),
        "afterAverageCost": round(float(current_average_cost or 0.0), 8),
        "error": None,
    }
    if action not in POSITION_AFFECTING_ACTIONS:
        return {**base, "status": "failed", "error": "该交易类型暂不支持入账到组合持仓"}
    if quantity is None or quantity <= 0:
        return {**base, "status": "failed", "error": "入账需要有效成交数量"}
    if price is None or price <= 0:
        return {**base, "status": "failed", "error": "入账需要有效成交价格"}

    if action in BUY_ACTIONS:
        after_quantity = current_quantity + quantity
        after_average_cost = (
            (current_quantity * current_average_cost + quantity * price) / after_quantity
            if after_quantity > 0
            else 0.0
        )
        quantity_delta = quantity
    else:
        if quantity > current_quantity + QUANTITY_TOLERANCE:
            return {**base, "status": "failed", "error": "卖出数量超过当前组合持仓，不能入账"}
        after_quantity = max(0.0, current_quantity - quantity)
        after_average_cost = current_average_cost if after_quantity > 0 else 0.0
        quantity_delta = -quantity

    return {
        **base,
        "quantityDelta": quantity_delta,
        "afterQuantity": round(after_quantity, 8),
        "afterAverageCost": round(after_average_cost, 8),
    }


def project_positions_from_trade_rows(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, float]]:
    projected: dict[str, dict[str, float]] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        action = str(row.get("action_type") or row.get("actionType") or "").strip().lower()
        quantity = _number(row.get("quantity"))
        price = _number(row.get("price"))
        if not symbol or action not in POSITION_AFFECTING_ACTIONS:
            continue
        current = projected.setdefault(symbol, {"quantity": 0.0, "average_cost": 0.0})
        effect = project_trade_effect(
            current_quantity=current["quantity"],
            current_average_cost=current["average_cost"],
            action_type=action,
            quantity=quantity,
            price=price,
        )
        if effect["status"] != "ready":
            continue
        current["quantity"] = effect["afterQuantity"]
        current["average_cost"] = effect["afterAverageCost"]
    return {symbol: _rounded_position(value) for symbol, value in projected.items()}


def _rounded_position(position: dict[str, float]) -> dict[str, float]:
    return {
        "quantity": round(position["quantity"], 8),
        "average_cost": round(position["average_cost"], 8),
    }


def _number(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
