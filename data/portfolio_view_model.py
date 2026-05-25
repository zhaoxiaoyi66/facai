from __future__ import annotations

from pathlib import Path
from typing import Any

from data.portfolio import (
    PortfolioPositionStore,
    PortfolioSettingsStore,
    calculate_portfolio_positions,
)


ACTION_GROUPS = (
    ("addable", "可加仓"),
    ("hold", "持有观察"),
    ("nearTrim", "接近减仓价"),
    ("overweight", "超仓位"),
    ("review", "需复核"),
)


def build_portfolio_view_model(
    db_path: Path | None = None,
    current_prices: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    position_store = PortfolioPositionStore(db_path) if db_path is not None else PortfolioPositionStore()
    settings_store = PortfolioSettingsStore(db_path) if db_path is not None else PortfolioSettingsStore()
    settings = settings_store.get_settings()
    positions = position_store.list_active_positions()
    calculated = calculate_portfolio_positions(positions, _normalize_prices(current_prices), settings=settings)
    rows = [_row_view(row) for row in calculated]
    return {
        "summary": _summary(rows),
        "actionGroups": _action_groups(rows),
        "rows": rows,
        "settings": settings,
    }


def _row_view(row: dict) -> dict[str, Any]:
    return {
        "symbol": row.get("symbol"),
        "quantity": row.get("quantity"),
        "averageCost": row.get("average_cost"),
        "currentPrice": row.get("currentPrice"),
        "marketValue": row.get("marketValue"),
        "costBasis": row.get("costBasis"),
        "unrealizedPnl": row.get("unrealizedPnl"),
        "unrealizedPnlPct": row.get("unrealizedPnlPct"),
        "positionPct": row.get("positionPct"),
        "targetPositionPct": row.get("target_position_pct"),
        "maxAcceptablePositionPct": row.get("max_acceptable_position_pct"),
        "plannedSellPrice": row.get("planned_sell_price"),
        "firstTrimPrice": row.get("first_trim_price"),
        "secondTrimPrice": row.get("second_trim_price"),
        "reviewPrice": row.get("review_price"),
        "notes": row.get("notes") or "",
        "overweightSystem": bool(row.get("overweightSystem")),
        "overweightPersonal": bool(row.get("overweightPersonal")),
        "nearTrimPrice": bool(row.get("nearTrimPrice")),
        "needsReview": bool(row.get("needsReview")),
        "missingPrice": bool(row.get("missingPrice")),
        "systemMaxPosition": row.get("systemMaxPosition"),
        "systemStatus": row.get("systemStatus"),
        "actionGroup": _action_group_for_row(row),
    }


def _summary(rows: list[dict]) -> dict[str, Any]:
    market_value = _sum_present(row.get("marketValue") for row in rows)
    cost_basis = _sum_present(row.get("costBasis") for row in rows)
    unrealized_pnl = _sum_present(row.get("unrealizedPnl") for row in rows)
    return {
        "marketValue": market_value,
        "costBasis": cost_basis,
        "unrealizedPnl": unrealized_pnl,
        "unrealizedPnlPct": unrealized_pnl / cost_basis * 100 if cost_basis > 0 else None,
        "positionCount": len(rows),
        "overweightCount": sum(1 for row in rows if row["overweightSystem"] or row["overweightPersonal"]),
        "needsReviewCount": sum(1 for row in rows if row["needsReview"] or row["missingPrice"]),
    }


def _action_groups(rows: list[dict]) -> list[dict[str, Any]]:
    by_key = {key: [] for key, _label in ACTION_GROUPS}
    for row in rows:
        by_key[row["actionGroup"]].append(row["symbol"])
    return [
        {
            "key": key,
            "label": label,
            "count": len(by_key[key]),
            "symbols": by_key[key],
        }
        for key, label in ACTION_GROUPS
    ]


def _action_group_for_row(row: dict) -> str:
    if row.get("needsReview") or row.get("missingPrice"):
        return "review"
    if row.get("overweightSystem") or row.get("overweightPersonal"):
        return "overweight"
    if row.get("nearTrimPrice"):
        return "nearTrim"
    if _can_add(row):
        return "addable"
    return "hold"


def _can_add(row: dict) -> bool:
    position_pct = row.get("positionPct")
    target = row.get("target_position_pct")
    if position_pct is None or target is None:
        return False
    return position_pct < target


def _sum_present(values) -> float:
    return sum(float(value) for value in values if value is not None)


def _normalize_prices(current_prices: dict[str, float | None] | None) -> dict[str, float | None]:
    return {str(symbol).strip().upper(): price for symbol, price in (current_prices or {}).items()}
