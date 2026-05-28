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
        result["error"] = "symbol is required"
        return result

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
