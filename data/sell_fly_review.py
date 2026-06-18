from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from data.decision_log import TradeJournalStore
from data.market_context import build_market_history
from data.prices import CACHE_PATH
from data.trade_safety_gate import has_concrete_reentry_plan


SELL_FLY_HORIZONS = {"5d": 5, "10d": 10, "20d": 20}
PRIMARY_SELL_FLY_HORIZON = "10d"
SELL_FLY_THRESHOLD_PCT = 8.0
SELL_TRIM_ACTIONS = {"sell", "trim"}


@dataclass(frozen=True)
class SellFlyReviewResult:
    symbol: str
    tradeDate: str
    actionType: str
    sellPrice: float | None
    quantity: float | None
    horizon: str
    maxPriceAfterSell: float | None
    maxReturnAfterSellPct: float | None
    endPrice: float | None
    endReturnPct: float | None
    suspectedSellFly: bool
    reason: str
    disciplineSnapshot: dict[str, Any]
    violatedDiscipline: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_sell_fly_review_results(
    path: Path = CACHE_PATH,
    current_date: date | str | None = None,
) -> list[dict[str, Any]]:
    current = _parse_date(current_date) or date.today()
    store = TradeJournalStore(path)
    results: list[dict[str, Any]] = []
    for entry in store.list_entries():
        if str(entry.get("action_type") or "").lower() not in SELL_TRIM_ACTIONS:
            continue
        trade_date = _parse_date(entry.get("trade_date"))
        if trade_date is None or trade_date > current:
            continue
        history = build_market_history(str(entry.get("symbol") or ""), path=path)
        for horizon, days in SELL_FLY_HORIZONS.items():
            results.append(_review_entry(entry, history, current, horizon, days).to_dict())
    return results


def build_sell_fly_reviews(
    path: Path = CACHE_PATH,
    current_date: date | str | None = None,
) -> list[dict[str, Any]]:
    return build_sell_fly_review_results(path, current_date)


def _review_entry(
    entry: dict,
    history: pd.DataFrame,
    current_date: date,
    horizon: str,
    horizon_days: int,
) -> SellFlyReviewResult:
    symbol = str(entry.get("symbol") or "").upper()
    trade_date = _parse_date(entry.get("trade_date"))
    sell_price = _number(entry.get("price"))
    base = {
        "symbol": symbol,
        "tradeDate": trade_date.isoformat() if trade_date else "",
        "actionType": str(entry.get("action_type") or ""),
        "sellPrice": sell_price,
        "quantity": _number(entry.get("quantity")),
        "horizon": horizon,
        "disciplineSnapshot": _discipline_snapshot(entry),
    }
    if trade_date is None:
        return _empty_result(base, "missing_trade_date")
    if sell_price is None or sell_price <= 0:
        return _empty_result(base, "missing_sell_price")
    if history.empty or "date" not in history or "close" not in history:
        return _empty_result(base, "missing_price_history")

    horizon_end = min(trade_date + timedelta(days=horizon_days), current_date)
    window = _history_window(history, trade_date, horizon_end)
    if window.empty:
        return _empty_result(base, "missing_forward_prices")

    close = pd.to_numeric(window["close"], errors="coerce").dropna()
    if close.empty:
        return _empty_result(base, "missing_forward_prices")

    max_price = float(close.max())
    end_price = float(close.iloc[-1])
    max_return = (max_price - sell_price) / sell_price * 100
    end_return = (end_price - sell_price) / sell_price * 100
    suspected = horizon == PRIMARY_SELL_FLY_HORIZON and max_return > SELL_FLY_THRESHOLD_PCT
    blockers = base["disciplineSnapshot"].get("blockers") or []
    violated = bool(blockers and max_return > 0)
    reason = _reason(horizon, max_return, suspected, violated)
    return SellFlyReviewResult(
        **base,
        maxPriceAfterSell=round(max_price, 4),
        maxReturnAfterSellPct=round(max_return, 4),
        endPrice=round(end_price, 4),
        endReturnPct=round(end_return, 4),
        suspectedSellFly=suspected,
        reason=reason,
        violatedDiscipline=violated,
    )


def _empty_result(base: dict[str, Any], reason: str) -> SellFlyReviewResult:
    return SellFlyReviewResult(
        **base,
        maxPriceAfterSell=None,
        maxReturnAfterSellPct=None,
        endPrice=None,
        endReturnPct=None,
        suspectedSellFly=False,
        reason=reason,
        violatedDiscipline=False,
    )


def _history_window(history: pd.DataFrame, trade_date: date, horizon_end: date) -> pd.DataFrame:
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    dates = frame["date"].dt.date
    return frame[(dates > trade_date) & (dates <= horizon_end)].sort_values("date")


def _discipline_snapshot(entry: dict) -> dict[str, Any]:
    return {
        "positionClass": entry.get("position_class"),
        "plannedSellPct": entry.get("planned_sell_pct"),
        "sellReasonType": entry.get("sell_reason_type"),
        "sellLevel": entry.get("sell_level"),
        "thesisBroken": _bool_or_none(entry.get("thesis_broken")),
        "positionOverLimit": _bool_or_none(entry.get("position_over_limit")),
        "hasReentryPlan": has_concrete_reentry_plan(entry),
        "reentryPlanText": entry.get("reentry_plan_text"),
        "reentryPullbackPrice": entry.get("reentry_pullback_price"),
        "reentryBreakoutPrice": entry.get("reentry_breakout_price"),
        "reentryTimeStopDays": entry.get("reentry_time_stop_days"),
        "reentryBuyBackPctOnPullback": entry.get("reentry_buy_back_pct_on_pullback"),
        "reentryBuyBackPctOnBreakout": entry.get("reentry_buy_back_pct_on_breakout"),
        "reentryThesisInvalidation": entry.get("reentry_thesis_invalidation"),
        "maxAllowedSellPct": entry.get("max_allowed_sell_pct"),
        "canSellCore": _bool_or_none(entry.get("can_sell_core")),
        "requiresReentryPlan": _bool_or_none(entry.get("requires_reentry_plan")),
        "disciplineStatus": entry.get("discipline_status"),
        "blockers": _discipline_blockers(entry),
        "warnings": _list(entry.get("warnings")),
        "reminderText": entry.get("reminder_text"),
    }


def _reason(horizon: str, max_return: float, suspected: bool, violated: bool) -> str:
    if suspected:
        if violated:
            return "suspected_sell_fly_with_discipline_blocker"
        return "suspected_sell_fly_10d_gt_8pct"
    if horizon == PRIMARY_SELL_FLY_HORIZON:
        return "no_sell_fly_10d"
    return "calculated_reference_horizon"


def _parse_date(value: date | str | object) -> date | None:
    if isinstance(value, date):
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _discipline_blockers(entry: dict) -> list:
    explicit = _list(entry.get("blockers"))
    if explicit:
        return explicit
    if str(entry.get("sell_warning_level") or "").strip().upper() != "HIGH_RISK":
        return []
    return _list(entry.get("sell_warning_reasons"))
