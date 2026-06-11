from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from time import perf_counter
from typing import Any, Iterable

from data.fundamentals import FundamentalCache
from data.prices import CACHE_PATH


class RefreshMode(str, Enum):
    PRICE_ONLY = "PRICE_ONLY"
    DAILY_TECHNICAL = "DAILY_TECHNICAL"
    FUNDAMENTALS_IF_EVENT = "FUNDAMENTALS_IF_EVENT"
    FULL_REFRESH = "FULL_REFRESH"


@dataclass(frozen=True)
class RefreshTickerResult:
    ticker: str
    status: str
    message: str
    duration_seconds: float


def refresh_symbols_by_mode(
    symbols: Iterable[str],
    mode: RefreshMode | str,
    *,
    provider: Any | None = None,
    cache: FundamentalCache | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_mode = RefreshMode(mode)
    started = perf_counter()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tickers = _normalize_symbols(symbols)
    market_provider = provider or _market_data_provider(full_fundamentals=normalized_mode == RefreshMode.FULL_REFRESH)
    fundamental_cache = cache or FundamentalCache(CACHE_PATH)

    if normalized_mode == RefreshMode.PRICE_ONLY:
        ticker_results = _refresh_price_only(tickers, provider=market_provider, cache=fundamental_cache, now=timestamp)
    elif normalized_mode == RefreshMode.DAILY_TECHNICAL:
        ticker_results = [_refresh_daily_technical(symbol, provider=market_provider) for symbol in tickers]
    elif normalized_mode == RefreshMode.FUNDAMENTALS_IF_EVENT:
        full_provider = provider or _market_data_provider(full_fundamentals=True)
        ticker_results = [
            _refresh_fundamentals_if_event(symbol, provider=full_provider, cache=fundamental_cache, now=timestamp)
            for symbol in tickers
        ]
    else:
        full_provider = provider or _market_data_provider(full_fundamentals=True)
        ticker_results = [_refresh_full(symbol, provider=full_provider) for symbol in tickers]

    results = [result.__dict__ for result in ticker_results]
    refreshed_count = sum(1 for item in ticker_results if item.status == "success")
    skipped_count = sum(1 for item in ticker_results if item.status == "skipped")
    failed_count = sum(1 for item in ticker_results if item.status == "failed")
    return {
        "mode": normalized_mode.value,
        "status": _overall_status(refreshed_count=refreshed_count, skipped_count=skipped_count, failed_count=failed_count),
        "refreshed_count": refreshed_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "duration_seconds": round(perf_counter() - started, 3),
        "fetchedAt": timestamp.isoformat(),
        "ticker_results": results,
    }


def should_refresh_fundamentals(
    ticker: str,
    snapshot: dict | None,
    *,
    now: datetime | None = None,
    provider_snapshot: dict | None = None,
    force: bool = False,
    event_window_days: int = 7,
) -> bool:
    _ = ticker
    if force:
        return True
    data = dict(snapshot or {})
    current_date = (now or datetime.now(timezone.utc)).date()
    for key in ("next_earnings_date", "nextEarningsDate", "earnings_date", "earningsDate", "last_earnings_date", "lastEarningsDate"):
        event_date = _parse_date(data.get(key))
        if event_date and abs((event_date - current_date).days) <= event_window_days:
            return True

    provider_period = _text((provider_snapshot or {}).get("fiscal_period") or (provider_snapshot or {}).get("fiscalPeriod"))
    cached_period = _text(data.get("fiscal_period") or data.get("fiscalPeriod"))
    if provider_period and cached_period and provider_period != cached_period:
        return True

    updated_at = _parse_datetime(data.get("fundamental_updated_at") or data.get("updated_at") or data.get("fetched_at"))
    for key in ("sec_filing_accepted_at", "filing_accepted_at", "accepted_at", "financial_statement_date", "financialStatementDate"):
        event_time = _parse_datetime(data.get(key))
        if event_time and (updated_at is None or event_time > updated_at):
            return True
    return False


def _refresh_price_only(
    tickers: list[str],
    *,
    provider: Any,
    cache: FundamentalCache,
    now: datetime,
) -> list[RefreshTickerResult]:
    quote_rows = _quote_rows(tickers, provider)
    results: list[RefreshTickerResult] = []
    for symbol in tickers:
        started = perf_counter()
        quote = quote_rows.get(symbol)
        if not quote:
            results.append(_ticker_result(symbol, "failed", "quote 刷新失败或无返回", started))
            continue
        existing = cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {}
        previous_fetched_at = cache.get_snapshot_fetched_at(symbol)
        merged = _merge_quote_snapshot(symbol, existing, quote, previous_fetched_at=previous_fetched_at, now=now)
        cache.set_snapshot(symbol, merged)
        results.append(_ticker_result(symbol, "success", "价格已更新，基本面沿用缓存", started))
    return results


def _refresh_daily_technical(symbol: str, *, provider: Any) -> RefreshTickerResult:
    started = perf_counter()
    try:
        history = provider.get_price_history(symbol, force_refresh=True)
        if _has_history_rows(history):
            return _ticker_result(symbol, "success", "日线和技术指标缓存已更新", started)
        return _ticker_result(symbol, "failed", "日线无有效数据", started)
    except Exception as exc:
        return _ticker_result(symbol, "failed", f"日线刷新失败：{_short_error(exc)}", started)


def _refresh_fundamentals_if_event(symbol: str, *, provider: Any, cache: FundamentalCache, now: datetime) -> RefreshTickerResult:
    started = perf_counter()
    snapshot = cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {}
    if not should_refresh_fundamentals(symbol, snapshot, now=now):
        return _ticker_result(symbol, "skipped", "无财报/披露事件，基本面沿用缓存", started)
    try:
        provider.get_quote(symbol, force_refresh=True)
        return _ticker_result(symbol, "success", "财报/披露事件触发，基本面已刷新", started)
    except Exception as exc:
        return _ticker_result(symbol, "failed", f"基本面刷新失败：{_short_error(exc)}", started)


def _refresh_full(symbol: str, *, provider: Any) -> RefreshTickerResult:
    started = perf_counter()
    errors: list[str] = []
    quote_ok = False
    history_ok = False
    try:
        quote = provider.get_quote(symbol, force_refresh=True)
        quote_ok = isinstance(quote, dict) and bool(quote)
    except Exception as exc:
        errors.append(f"quote: {_short_error(exc)}")
    try:
        history = provider.get_price_history(symbol, force_refresh=True)
        history_ok = _has_history_rows(history)
    except Exception as exc:
        errors.append(f"history: {_short_error(exc)}")
    if quote_ok and history_ok:
        return _ticker_result(symbol, "success", "全量数据已刷新", started)
    if quote_ok or history_ok:
        return _ticker_result(symbol, "success", "部分全量数据已刷新：" + "; ".join(errors), started)
    return _ticker_result(symbol, "failed", "全量刷新失败：" + "; ".join(errors), started)


def _quote_rows(tickers: list[str], provider: Any) -> dict[str, dict]:
    if hasattr(provider, "_get_json"):
        try:
            payload = provider._get_json(  # noqa: SLF001 - this is the existing FMP quote endpoint without fundamentals.
                "quote",
                {"symbol": ",".join(tickers)},
                timeout_seconds=8,
                retries=1,
                force_refresh=True,
            )
            rows = payload if isinstance(payload, list) else [payload]
            return {_quote_symbol(row): row for row in rows if isinstance(row, dict) and _quote_symbol(row)}
        except Exception:
            return {}
    rows: dict[str, dict] = {}
    for symbol in tickers:
        try:
            quote = provider.get_quote(symbol, force_refresh=True)
        except Exception:
            continue
        if isinstance(quote, dict) and quote:
            rows[symbol] = quote
    return rows


def _merge_quote_snapshot(
    symbol: str,
    existing: dict,
    quote: dict,
    *,
    previous_fetched_at: str | None,
    now: datetime,
) -> dict:
    merged = dict(existing or {})
    merged.setdefault("ticker", symbol)
    merged.setdefault("symbol", symbol)
    if previous_fetched_at and not merged.get("fundamental_updated_at"):
        merged["fundamental_updated_at"] = previous_fetched_at
    field_map = {
        "current_price": ("price", "current_price", "currentPrice"),
        "price_change": ("change", "price_change"),
        "price_change_pct": ("changesPercentage", "change_pct", "price_change_pct"),
        "volume": ("volume",),
        "market_cap": ("marketCap", "market_cap"),
        "fifty_two_week_high": ("yearHigh", "fifty_two_week_high"),
        "fifty_two_week_low": ("yearLow", "fifty_two_week_low"),
        "shares_outstanding": ("sharesOutstanding", "shares_outstanding"),
    }
    for target, keys in field_map.items():
        value = _first_present(quote, *keys)
        if value is not None:
            merged[target] = value
    merged["quote_updated_at"] = now.isoformat()
    merged["price_updated_at"] = now.isoformat()
    merged["refresh_mode"] = RefreshMode.PRICE_ONLY.value
    merged["cache_note"] = "仅价格已更新；基本面沿用缓存。"
    return merged


def _quote_symbol(row: dict) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").strip().upper()


def _first_present(row: dict, *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _ticker_result(symbol: str, status: str, message: str, started: float) -> RefreshTickerResult:
    return RefreshTickerResult(
        ticker=symbol,
        status=status,
        message=message,
        duration_seconds=round(perf_counter() - started, 3),
    )


def _overall_status(*, refreshed_count: int, skipped_count: int, failed_count: int) -> str:
    if failed_count == 0:
        return "success"
    if refreshed_count or skipped_count:
        return "partial"
    return "failed"


def _has_history_rows(history: Any) -> bool:
    empty = getattr(history, "empty", None)
    if isinstance(empty, bool):
        return not empty
    try:
        return len(history) > 0
    except TypeError:
        return False


def _parse_date(value: object) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text[:10]), datetime.min.time())
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value: object) -> str:
    return str(value or "").strip()


def _short_error(error: object) -> str:
    return str(error or "").splitlines()[0][:240]


def _market_data_provider(*, full_fundamentals: bool) -> Any:
    from data.providers import get_market_data_provider

    return get_market_data_provider(full_fundamentals=full_fundamentals)
