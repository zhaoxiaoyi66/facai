from __future__ import annotations

from typing import Any, Iterable

from data.trade_performance import calculate_realized_pnl, match_realized_trades


CASH_SOURCE_MANUAL = "manual"
CASH_SOURCE_BASIS_REALIZED = "basis_realized"
CASH_SOURCE_UNAVAILABLE = "unavailable"


def realized_pnl_from_entries(entries: Iterable[dict[str, Any]]) -> float:
    matched = match_realized_trades(entries)
    stats = calculate_realized_pnl(matched.get("realized_trades") or [])
    value = _number(stats.get("total_realized_pnl"))
    return round(value or 0.0, 2)


def derive_cash_and_account_nav(
    *,
    portfolio_basis_value: object,
    open_cost_basis: object,
    market_value: object,
    realized_pnl: object = 0,
    manual_cash: object = None,
) -> dict[str, Any]:
    cash = _number(manual_cash)
    source = CASH_SOURCE_MANUAL if cash is not None else CASH_SOURCE_UNAVAILABLE
    basis = _number(portfolio_basis_value)
    cost = _number(open_cost_basis)
    realized = _number(realized_pnl) or 0.0
    if cash is None and basis is not None and cost is not None:
        cash = round(basis - cost + realized, 2)
        source = CASH_SOURCE_BASIS_REALIZED
    market = _number(market_value)
    if cash is not None and market is not None:
        account_nav = round(cash + market, 2)
    elif cash is not None:
        account_nav = round(cash, 2)
    elif market is not None:
        account_nav = round(market, 2)
    else:
        account_nav = None
    return {
        "cash": round(cash, 2) if cash is not None else None,
        "cash_source": source,
        "account_nav": account_nav,
        "realized_pnl": round(realized, 2),
    }


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
