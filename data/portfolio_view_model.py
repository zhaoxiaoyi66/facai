from __future__ import annotations

from pathlib import Path
from typing import Any

from buy_zone_engine import buy_zone_with_manual_override, generate_buy_zone
from data.cache_read_model import CacheReadModel
from data.market_context import build_market_context, build_market_history
from data.portfolio import (
    PortfolioPositionStore,
    PortfolioSettingsStore,
    calculate_portfolio_positions,
)
from data.portfolio_trade_sync import unsynced_trade_counts_by_symbol
from data.prices import CACHE_PATH
from data.stock_plan import StockPlanStore
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.total_score import calculate_total_score


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
    system_decision_inputs: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    position_store = PortfolioPositionStore(db_path) if db_path is not None else PortfolioPositionStore()
    settings_store = PortfolioSettingsStore(db_path) if db_path is not None else PortfolioSettingsStore()
    settings = settings_store.get_settings()
    positions = position_store.list_active_positions()
    prices, price_statuses = _current_prices_for_positions(positions, db_path, current_prices)
    system_refs = _system_refs_for_positions(positions, db_path, prices, system_decision_inputs)
    unsynced_counts = unsynced_trade_counts_by_symbol(db_path or CACHE_PATH)
    calculated = calculate_portfolio_positions(positions, prices, settings=settings, system_refs=system_refs)
    rows = [
        _row_view(
            row,
            price_statuses.get(str(row.get("symbol") or "").upper(), "missing"),
            system_refs.get(str(row.get("symbol") or "").upper(), {}),
            unsynced_counts.get(str(row.get("symbol") or "").upper(), 0),
        )
        for row in calculated
    ]
    return {
        "summary": _summary(rows, settings),
        "actionGroups": _action_groups(rows),
        "rows": rows,
        "settings": settings,
    }


def _row_view(row: dict, price_status: str, system_ref: dict[str, Any], unsynced_trade_count: int = 0) -> dict[str, Any]:
    deviation_warnings = _deviation_warnings(row, system_ref)
    return {
        "symbol": row.get("symbol"),
        "quantity": row.get("quantity"),
        "averageCost": row.get("average_cost"),
        "positionTier": row.get("position_tier"),
        "currentPrice": row.get("currentPrice"),
        "priceStatus": price_status,
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
        "executionSource": system_ref.get("executionSource"),
        "finalDecision": dict(system_ref.get("finalDecision") or {}),
        "systemAction": system_ref.get("systemAction"),
        "systemCurrentAdd": system_ref.get("systemCurrentAdd"),
        "buyZoneStatus": system_ref.get("buyZoneStatus"),
        "decisionLane": system_ref.get("decisionLane"),
        "blockReasons": list(system_ref.get("blockReasons") or []),
        "reviewReasons": list(system_ref.get("reviewReasons") or []),
        "deviationWarnings": deviation_warnings,
        "unsyncedTradeCount": int(unsynced_trade_count or 0),
        "actionGroup": _action_group_for_row(row),
    }


def _summary(rows: list[dict], settings: dict | None = None) -> dict[str, Any]:
    market_value = _sum_present(row.get("marketValue") for row in rows)
    cost_basis = _sum_present(row.get("costBasis") for row in rows)
    unrealized_pnl = _sum_present(row.get("unrealizedPnl") for row in rows)
    total_value = _number((settings or {}).get("total_portfolio_value"))
    cash_balance = total_value - market_value if total_value is not None and total_value > 0 else None
    return {
        "marketValue": market_value,
        "costBasis": cost_basis,
        "unrealizedPnl": unrealized_pnl,
        "unrealizedPnlPct": unrealized_pnl / cost_basis * 100 if cost_basis > 0 else None,
        "totalPortfolioValue": total_value,
        "cashBalance": cash_balance,
        "cashBalanceSource": "derived" if cash_balance is not None else "unavailable",
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


def _current_prices_for_positions(
    positions: list[dict],
    db_path: Path | None,
    current_prices: dict[str, float | None] | None,
) -> tuple[dict[str, float | None], dict[str, str]]:
    symbols = [str(position.get("symbol") or "").strip().upper() for position in positions]
    symbols = [symbol for symbol in symbols if symbol]
    path = db_path or CACHE_PATH
    provided_prices = _normalize_prices(current_prices)
    prices: dict[str, float | None] = {}
    statuses: dict[str, str] = {}
    for symbol in symbols:
        market = build_market_context(symbol, path=path)
        market_price = _number(market.get("currentPrice"))
        market_status = str(market.get("priceSource") or "missing")
        provided_price = _number(provided_prices.get(symbol))
        if market_price is not None:
            prices[symbol] = market_price
            statuses[symbol] = market_status
        elif provided_price is not None:
            prices[symbol] = provided_price
            statuses[symbol] = "provided"
        else:
            prices[symbol] = None
            statuses[symbol] = "missing"
    return prices, statuses


def _system_refs_for_positions(
    positions: list[dict],
    db_path: Path | None,
    current_prices: dict[str, float | None],
    system_decision_inputs: dict[str, dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    symbols = [str(position.get("symbol") or "").strip().upper() for position in positions]
    symbols = [symbol for symbol in symbols if symbol]
    refs: dict[str, dict[str, Any]] = {}
    input_refs = _system_refs_from_inputs(system_decision_inputs)
    path = db_path or CACHE_PATH
    cache = CacheReadModel(path)
    plan_store = StockPlanStore(path)
    for symbol in symbols:
        if symbol in input_refs:
            refs[symbol] = input_refs[symbol]
            continue
        snapshot = cache.get_quote_payload(symbol)
        if not snapshot:
            refs[symbol] = _empty_system_ref()
            continue
        refs[symbol] = _system_ref_from_local_cache(symbol, snapshot, path, current_prices.get(symbol), plan_store)
    return refs


def _system_refs_from_inputs(system_decision_inputs: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for symbol, inputs in (system_decision_inputs or {}).items():
        normalized = str(symbol).strip().upper()
        if not normalized or not inputs.get("score"):
            continue
        buy_zone = inputs.get("buy_zone")
        manual_plan = inputs.get("manual_plan_override") or inputs.get("manual_plan")
        bundle = build_final_decision_bundle(
            inputs["score"],
            buy_zone,
            inputs.get("position_plan"),
            manual_plan_override=manual_plan,
            symbol=normalized,
        )
        refs[normalized] = _system_ref_from_bundle(bundle, _buy_zone_status(buy_zone))
    return refs


def _system_ref_from_local_cache(
    symbol: str,
    snapshot: dict,
    path: Path,
    current_price: float | None,
    plan_store: StockPlanStore,
) -> dict[str, Any]:
    try:
        history = build_market_history(symbol, path=path)
        technicals = latest_technical_snapshot(add_technical_indicators(history))
        if current_price is not None:
            snapshot = dict(snapshot)
            snapshot["current_price"] = current_price
            snapshot["price"] = current_price
            technicals["price"] = current_price
        score = calculate_total_score(snapshot, technicals)
        stock_data = {**snapshot, **technicals}
        buy_zone = generate_buy_zone(symbol, stock_data, score, getattr(score, "scoring_model", None))
        plan = plan_store.get_plan(symbol)
        effective_buy_zone = buy_zone_with_manual_override(buy_zone, plan)
        bundle = build_final_decision_bundle(score, buy_zone, manual_plan_override=plan, symbol=symbol)
        return _system_ref_from_bundle(bundle, _buy_zone_status(effective_buy_zone))
    except Exception:
        return _empty_system_ref()


def _system_ref_from_bundle(bundle, buy_zone_status: str | None) -> dict[str, Any]:
    return {
        "executionSource": bundle.executionSource,
        "finalDecision": bundle.as_dict(),
        "systemAction": bundle.finalAction,
        "systemMaxPosition": bundle.maxPortfolioWeightPercent,
        "systemCurrentAdd": bundle.currentAddLimitPercent,
        "buyZoneStatus": bundle.buyZoneStatus or buy_zone_status,
        "decisionLane": bundle.decisionLane,
        "blockReasons": list(bundle.blockReasons),
        "reviewReasons": list(bundle.reviewReasons),
        "maxPortfolioWeightPercent": bundle.maxPortfolioWeightPercent,
        "systemStatus": bundle.decisionLane,
    }


def _empty_system_ref() -> dict[str, Any]:
    return {
        "systemAction": None,
        "systemMaxPosition": None,
        "systemCurrentAdd": None,
        "executionSource": None,
        "finalDecision": {},
        "buyZoneStatus": None,
        "decisionLane": None,
        "blockReasons": [],
        "reviewReasons": [],
    }


def _deviation_warnings(row: dict, system_ref: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if row.get("overweightSystem"):
        warnings.append("overweight_system")
    if row.get("overweightPersonal"):
        warnings.append("overweight_personal")
    if _has_position(row) and _system_says_do_not_add(system_ref):
        warnings.append("system_not_addable")
    if row.get("nearTrimPrice"):
        warnings.append("near_trim_price")
    return warnings


def _has_position(row: dict) -> bool:
    quantity = _number(row.get("quantity"))
    return quantity is not None and quantity > 0


def _system_says_do_not_add(system_ref: dict[str, Any]) -> bool:
    lane = str(system_ref.get("decisionLane") or "").lower()
    current_add = _number(system_ref.get("systemCurrentAdd"))
    return lane in {"review", "blocked", "wait"} or current_add == 0


def _buy_zone_status(buy_zone: Any) -> str | None:
    if buy_zone is None:
        return None
    if isinstance(buy_zone, dict):
        return buy_zone.get("currentZone") or buy_zone.get("current_zone")
    return getattr(buy_zone, "currentZone", None) or getattr(buy_zone, "current_zone", None)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
