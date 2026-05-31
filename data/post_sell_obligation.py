from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from data.cache_read_model import CacheReadModel
from data.decision_log import TradeJournalStore
from data.prices import CACHE_PATH


SELL_TRIM_ACTIONS = {"sell", "trim"}


def build_post_sell_obligations(
    path: Path = CACHE_PATH,
    current_date: date | str | None = None,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    current = _parse_date(current_date) or date.today()
    store = TradeJournalStore(path)
    cache = CacheReadModel(path)
    obligations: list[dict[str, Any]] = []
    for entry in store.list_entries(symbol=symbol):
        if str(entry.get("action_type") or "").lower() not in SELL_TRIM_ACTIONS:
            continue
        obligations.append(_build_obligation(entry, cache.get_current_price(str(entry.get("symbol") or "")), current))
    return obligations


def _build_obligation(entry: dict[str, Any], current_price: float | None, current_date: date) -> dict[str, Any]:
    trade_date = _parse_date(entry.get("trade_date"))
    time_stop_days = _optional_int(entry.get("reentry_time_stop_days"))
    time_stop_due = trade_date + timedelta(days=time_stop_days) if trade_date and time_stop_days is not None else None
    pullback_price = _number(entry.get("reentry_pullback_price"))
    breakout_price = _number(entry.get("reentry_breakout_price"))
    has_plan = _has_reentry_plan(entry)
    triggers = _triggers(
        current_price=current_price,
        pullback_price=pullback_price,
        breakout_price=breakout_price,
        time_stop_due=time_stop_due,
        current_date=current_date,
    )
    status = _status(has_plan, triggers, time_stop_due, current_date)
    return {
        "tradeEntryId": entry.get("id"),
        "symbol": str(entry.get("symbol") or "").upper(),
        "tradeDate": trade_date.isoformat() if trade_date else "",
        "actionType": entry.get("action_type"),
        "sellPrice": _number(entry.get("price")),
        "quantity": _number(entry.get("quantity")),
        "hasReentryPlan": has_plan,
        "status": status,
        "triggers": triggers,
        "currentPrice": current_price,
        "pullbackPrice": pullback_price,
        "pullbackBuyBackPct": _ratio(entry.get("reentry_buy_back_pct_on_pullback")),
        "breakoutPrice": breakout_price,
        "breakoutBuyBackPct": _ratio(entry.get("reentry_buy_back_pct_on_breakout")),
        "timeStopDays": time_stop_days,
        "timeStopDueDate": time_stop_due.isoformat() if time_stop_due else None,
        "thesisInvalidation": str(entry.get("reentry_thesis_invalidation") or "").strip(),
        "planText": str(entry.get("reentry_plan_text") or "").strip(),
        "disciplineStatus": entry.get("discipline_status"),
        "syncRequired": status in {"missing_plan", "triggered", "overdue"},
    }


def _triggers(
    *,
    current_price: float | None,
    pullback_price: float | None,
    breakout_price: float | None,
    time_stop_due: date | None,
    current_date: date,
) -> list[str]:
    result: list[str] = []
    if current_price is not None and pullback_price is not None and current_price <= pullback_price:
        result.append("pullback_reentry")
    if current_price is not None and breakout_price is not None and current_price >= breakout_price:
        result.append("breakout_reentry")
    if time_stop_due is not None and current_date >= time_stop_due:
        result.append("time_stop_due")
    return result


def _status(has_plan: bool, triggers: list[str], time_stop_due: date | None, current_date: date) -> str:
    if not has_plan:
        return "missing_plan"
    if triggers:
        if triggers == ["time_stop_due"] or "time_stop_due" in triggers and len(triggers) == 1:
            return "overdue"
        return "triggered"
    if time_stop_due is not None and current_date < time_stop_due:
        return "watching"
    return "planned"


def _has_reentry_plan(entry: dict[str, Any]) -> bool:
    if _bool(entry.get("has_reentry_plan")):
        return True
    return bool(
        str(entry.get("reentry_plan_text") or "").strip()
        or str(entry.get("reentry_thesis_invalidation") or "").strip()
        or _number(entry.get("reentry_pullback_price")) is not None
        or _number(entry.get("reentry_breakout_price")) is not None
    )


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


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ratio(value: object) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 1 else number


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}
