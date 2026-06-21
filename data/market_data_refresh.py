from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def refresh_symbol_market_data(symbol: str, *, provider: Any | None = None, now: datetime | None = None) -> dict[str, Any]:
    normalized = str(symbol or "").strip().upper()
    fetched_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    result: dict[str, Any] = {
        "symbol": normalized,
        "status": "failed",
        "quoteStatus": "failed",
        "historyStatus": "failed",
        "fetchedAt": fetched_at,
        "error": None,
    }
    if not normalized:
        result["error"] = "缺少股票代码"
        return result

    result["before"] = _cache_summary(normalized)
    market_data_provider = provider or _market_data_provider()
    errors: list[str] = []

    try:
        quote = market_data_provider.get_quote(normalized, force_refresh=True)
        result["quoteStatus"] = "refreshed" if isinstance(quote, dict) and quote else "empty"
    except Exception as exc:
        result["quoteStatus"] = "failed"
        errors.append(f"quote: {exc}")

    try:
        history = market_data_provider.get_price_history(normalized, force_refresh=True)
        result["historyStatus"] = "refreshed" if _has_history_rows(history) else "empty"
    except Exception as exc:
        result["historyStatus"] = "failed"
        errors.append(f"history: {exc}")

    refreshed_count = sum(1 for key in ("quoteStatus", "historyStatus") if result[key] == "refreshed")
    if refreshed_count == 2:
        result["status"] = "success"
    elif refreshed_count == 1:
        result["status"] = "partial"
    else:
        result["status"] = "failed"
    result["error"] = "; ".join(errors) if errors else None
    result["after"] = _cache_summary(normalized)
    return result


def _market_data_provider() -> Any:
    from data.providers import get_market_data_provider

    return get_market_data_provider(full_fundamentals=True)


def _has_history_rows(history: Any) -> bool:
    empty = getattr(history, "empty", None)
    if isinstance(empty, bool):
        return not empty
    try:
        return len(history) > 0
    except TypeError:
        return False


def _cache_summary(symbol: str) -> dict[str, Any]:
    try:
        from data.fundamentals import FundamentalCache
        from data.market_context import build_market_history

        snapshot = FundamentalCache().get_snapshot(symbol, max_age_hours=24 * 3650) or {}
        history = build_market_history(symbol)
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}

    if history is None or getattr(history, "empty", True):
        latest_date = None
        latest_volume = None
        bars_count = 0
    else:
        latest = history.iloc[-1]
        latest_date = str(latest.get("date"))
        latest_volume = latest.get("volume")
        bars_count = len(history)

    return {
        "symbol": symbol,
        "profile_exists": bool(snapshot),
        "company_name": _first_value(snapshot, "company_name", "companyName", "name", "company"),
        "sector": _first_value(snapshot, "sector"),
        "industry": _first_value(snapshot, "industry", "industry_group", "business_model", "model"),
        "quote_price": _first_value(snapshot, "current_price", "currentPrice", "price"),
        "market_cap": _first_value(snapshot, "market_cap", "marketCap", "mktCap", "company_market_cap"),
        "daily_bars_count": bars_count,
        "latest_daily_bar_date": latest_date,
        "latest_daily_bar_volume": latest_volume,
    }


def _first_value(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None
