from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import json
import sqlite3
from time import perf_counter
from typing import Any, Callable, Iterable
from urllib.parse import urlencode
from urllib.request import Request

from data.fundamentals import FundamentalCache
from data.macro_regime import MACRO_FORCE_OFFICIAL_REFRESH, refresh_macro_indicators
from data.prices import CACHE_PATH
from data.us_market_session import USMarketSession, get_us_market_session_status


class RefreshMode(str, Enum):
    PRICE_ONLY = "PRICE_ONLY"
    DAILY_TECHNICAL = "DAILY_TECHNICAL"
    FUNDAMENTALS_IF_EVENT = "FUNDAMENTALS_IF_EVENT"
    MACRO_ONLY = "MACRO_ONLY"
    FULL_REFRESH = "FULL_REFRESH"


@dataclass(frozen=True)
class RefreshTickerResult:
    ticker: str
    status: str
    message: str
    duration_seconds: float
    source: str = ""


@dataclass(frozen=True)
class RefreshResult:
    mode: str
    status: str
    refreshed_count: int
    skipped_count: int
    failed_count: int
    duration_seconds: float
    fetchedAt: str
    ticker_results: list[dict[str, Any]]
    summary: str
    macro_result: dict[str, Any] | None = None
    live_success_count: int | None = None
    cache_fallback_count: int | None = None
    quote_source: str | None = None
    provider_notes: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = self.__dict__.copy()
        return payload


@dataclass(frozen=True)
class QuoteFetchResult:
    rows: dict[str, dict]
    live_symbols: set[str]
    failed_symbols: set[str]
    source: str
    provider_notes: list[str]


QUOTE_SINGLE_WORKERS = 8
QUOTE_SINGLE_TIMEOUT_SECONDS = 4
QUOTE_BATCH_SYMBOLS_PER_REQUEST = 25
QUOTE_MULTI_SYMBOLS_PER_REQUEST = 25
QUOTE_CAPABILITY_DISABLE_HOURS = 6
_QUOTE_PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {}
BATCH_QUOTE_CAPABILITY = "batch_quote"
MULTI_SYMBOL_QUOTE_CAPABILITY = "multi_symbol_quote"


def refresh_symbols_by_mode(
    symbols: Iterable[str],
    mode: RefreshMode | str,
    *,
    provider: Any | None = None,
    cache: FundamentalCache | None = None,
    macro_refresher: Any | None = None,
    now: datetime | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    normalized_mode = RefreshMode(mode)
    started = perf_counter()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tickers = _normalize_symbols(symbols)
    fundamental_cache = cache or FundamentalCache(CACHE_PATH)

    if normalized_mode == RefreshMode.PRICE_ONLY:
        market_provider = provider or _market_data_provider(full_fundamentals=False)
        _emit_refresh_progress(
            progress_callback,
            mode=normalized_mode,
            symbol="",
            index=0,
            total=len(tickers),
            status="running",
            message="正在请求报价",
        )
        ticker_results = _refresh_price_only(
            tickers,
            provider=market_provider,
            cache=fundamental_cache,
            now=timestamp,
            progress_callback=progress_callback,
            mode=normalized_mode,
        )
        live_success_count = sum(1 for item in ticker_results if item.source == "live_quote")
        cache_fallback_count = sum(1 for item in ticker_results if item.source == "cache_fallback")
        quote_source = getattr(market_provider, "_last_quote_source", None)
        provider_notes = list(getattr(market_provider, "_last_quote_provider_notes", []) or [])
    elif normalized_mode == RefreshMode.DAILY_TECHNICAL:
        market_provider = provider or _market_data_provider(full_fundamentals=False)
        ticker_results = []
        for index, symbol in enumerate(tickers, start=1):
            _emit_refresh_progress(
                progress_callback,
                mode=normalized_mode,
                symbol=symbol,
                index=index,
                total=len(tickers),
                status="running",
                message="正在刷新日线与技术指标",
            )
            ticker_result = _refresh_daily_technical(symbol, provider=market_provider, cache=fundamental_cache, now=timestamp)
            ticker_results.append(ticker_result)
            _emit_refresh_progress(
                progress_callback,
                mode=normalized_mode,
                symbol=symbol,
                index=index,
                total=len(tickers),
                status=ticker_result.status,
                message=ticker_result.message,
            )
        live_success_count = None
        cache_fallback_count = None
        quote_source = None
        provider_notes = None
    elif normalized_mode == RefreshMode.FUNDAMENTALS_IF_EVENT:
        full_provider = provider or _market_data_provider(full_fundamentals=True)
        ticker_results = []
        for index, symbol in enumerate(tickers, start=1):
            _emit_refresh_progress(
                progress_callback,
                mode=normalized_mode,
                symbol=symbol,
                index=index,
                total=len(tickers),
                status="running",
                message="正在检查财报与披露事件",
            )
            ticker_result = _refresh_fundamentals_if_event(symbol, provider=full_provider, cache=fundamental_cache, now=timestamp)
            ticker_results.append(ticker_result)
            _emit_refresh_progress(
                progress_callback,
                mode=normalized_mode,
                symbol=symbol,
                index=index,
                total=len(tickers),
                status=ticker_result.status,
                message=ticker_result.message,
            )
        live_success_count = None
        cache_fallback_count = None
        quote_source = None
        provider_notes = None
    elif normalized_mode == RefreshMode.MACRO_ONLY:
        _emit_refresh_progress(
            progress_callback,
            mode=normalized_mode,
            symbol="大盘环境",
            index=0,
            total=1,
            status="running",
            message="正在刷新 VIX、利率和信用利差",
        )
        macro_result = (
            macro_refresher()
            if macro_refresher is not None
            else refresh_macro_indicators(mode=MACRO_FORCE_OFFICIAL_REFRESH)
        )
        status = str(macro_result.get("status") or macro_result.get("overall_status") or "failed")
        _emit_refresh_progress(
            progress_callback,
            mode=normalized_mode,
            symbol="大盘环境",
            index=1,
            total=1,
            status=status,
            message="大盘环境刷新完成",
        )
        result = RefreshResult(
            mode=normalized_mode.value,
            status=status,
            refreshed_count=1 if status in {"success", "partial"} else 0,
            skipped_count=0,
            failed_count=0 if status in {"success", "partial"} else 1,
            duration_seconds=round(perf_counter() - started, 3),
            fetchedAt=timestamp.isoformat(),
            ticker_results=[],
            summary="刷新大盘环境完成",
            macro_result=macro_result,
        )
        return result.to_dict()
    else:
        full_provider = provider or _market_data_provider(full_fundamentals=True)
        ticker_results = []
        for index, symbol in enumerate(tickers, start=1):
            _emit_refresh_progress(
                progress_callback,
                mode=normalized_mode,
                symbol=symbol,
                index=index,
                total=len(tickers),
                status="running",
                message="正在强制刷新 quote、日线和基本面",
            )
            ticker_result = _refresh_full(symbol, provider=full_provider)
            ticker_results.append(ticker_result)
            _emit_refresh_progress(
                progress_callback,
                mode=normalized_mode,
                symbol=symbol,
                index=index,
                total=len(tickers),
                status=ticker_result.status,
                message=ticker_result.message,
            )
        live_success_count = None
        cache_fallback_count = None
        quote_source = None
        provider_notes = None

    results = [result.__dict__ for result in ticker_results]
    refreshed_count = sum(1 for item in ticker_results if item.status == "success")
    skipped_count = sum(1 for item in ticker_results if item.status == "skipped")
    failed_count = sum(1 for item in ticker_results if item.status == "failed")
    result = RefreshResult(
        mode=normalized_mode.value,
        status=_overall_status(refreshed_count=refreshed_count, skipped_count=skipped_count, failed_count=failed_count),
        refreshed_count=refreshed_count,
        skipped_count=skipped_count,
        failed_count=failed_count,
        duration_seconds=round(perf_counter() - started, 3),
        fetchedAt=timestamp.isoformat(),
        ticker_results=results,
        summary=summarize_refresh_result(
            normalized_mode.value,
            refreshed_count=refreshed_count,
            skipped_count=skipped_count,
            failed_count=failed_count,
            duration_seconds=round(perf_counter() - started, 3),
        ),
        live_success_count=live_success_count,
        cache_fallback_count=cache_fallback_count,
        quote_source=quote_source,
        provider_notes=provider_notes,
    )
    return result.to_dict()


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


def should_refresh_technicals(snapshot: dict | None, *, now: datetime | None = None, max_age_hours: int = 24) -> bool:
    data = dict(snapshot or {})
    current = now or datetime.now(timezone.utc)
    if _cached_close_date_is_stale(data, current):
        return True
    updated_at = _parse_datetime(
        data.get("technical_updated_at")
        or data.get("technicalUpdatedAt")
        or data.get("history_updated_at")
        or data.get("price_history_updated_at")
        or data.get("updated_at")
    )
    if updated_at is None:
        return True
    return current.astimezone(timezone.utc) - updated_at > timedelta(hours=max_age_hours)


def _cached_close_date_is_stale(snapshot: dict, now: datetime) -> bool:
    cached_date = _snapshot_close_date(snapshot)
    if cached_date is None:
        return False
    expected_date = _latest_completed_us_regular_date(now)
    return cached_date < expected_date


def _snapshot_close_date(snapshot: dict) -> date | None:
    for key in ("price_as_of", "history_latest_date", "historyLatestDate", "latest_close_date", "latestCloseDate"):
        raw = snapshot.get(key)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(str(raw)[:10])
            except ValueError:
                continue
    return None


def _latest_completed_us_regular_date(now: datetime) -> date:
    try:
        from zoneinfo import ZoneInfo

        eastern = now.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        eastern = now.astimezone(timezone.utc) - timedelta(hours=4)
    candidate = eastern.date()
    if candidate.weekday() >= 5:
        return _previous_weekday(candidate)
    regular_close = time(16, 0)
    if eastern.time() < regular_close:
        return _previous_weekday(candidate)
    return candidate


def _previous_weekday(value: date) -> date:
    current = value - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def summarize_refresh_result(
    mode: RefreshMode | str,
    *,
    refreshed_count: int,
    skipped_count: int,
    failed_count: int,
    duration_seconds: float,
) -> str:
    mode_value = mode.value if isinstance(mode, RefreshMode) else str(mode)
    label = {
        RefreshMode.PRICE_ONLY.value: "更新价格",
        RefreshMode.DAILY_TECHNICAL.value: "更新技术",
        RefreshMode.FUNDAMENTALS_IF_EVENT.value: "财报后刷新基本面",
        RefreshMode.MACRO_ONLY.value: "刷新大盘环境",
        RefreshMode.FULL_REFRESH.value: "强制全量刷新",
    }.get(mode_value, mode_value)
    return f"{label}完成：{refreshed_count}只成功，{skipped_count}只跳过，{failed_count}只失败，用时 {duration_seconds:.1f}s"


def _emit_refresh_progress(
    callback: Callable[[dict[str, Any]], None] | None,
    *,
    mode: RefreshMode | str,
    symbol: str,
    index: int,
    total: int,
    status: str,
    message: str,
) -> None:
    if callback is None:
        return
    mode_value = mode.value if isinstance(mode, RefreshMode) else str(mode)
    callback(
        {
            "mode": mode_value,
            "symbol": symbol,
            "index": max(0, int(index)),
            "total": max(0, int(total)),
            "status": str(status or ""),
            "message": str(message or ""),
        }
    )


def _refresh_price_only(
    tickers: list[str],
    *,
    provider: Any,
    cache: FundamentalCache,
    now: datetime,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    mode: RefreshMode | str = RefreshMode.PRICE_ONLY,
) -> list[RefreshTickerResult]:
    quote_result = _quote_rows(tickers, provider)
    quote_rows = quote_result.rows
    setattr(provider, "_last_quote_source", quote_result.source)
    setattr(provider, "_last_quote_provider_notes", quote_result.provider_notes)
    results: list[RefreshTickerResult] = []
    for index, symbol in enumerate(tickers, start=1):
        _emit_refresh_progress(
            progress_callback,
            mode=mode,
            symbol=symbol,
            index=index,
            total=len(tickers),
            status="running",
            message="正在写入报价缓存",
        )
        started = perf_counter()
        quote = quote_rows.get(symbol)
        if not quote:
            existing = cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {}
            if existing and _has_cached_quote(existing):
                results.append(_ticker_result(symbol, "success", "实时 quote 失败，沿用最近价格缓存", started, source="cache_fallback"))
                continue
            results.append(_ticker_result(symbol, "failed", "quote 刷新失败且无可用缓存", started, source="failed"))
            continue
        existing = cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {}
        previous_fetched_at = cache.get_snapshot_fetched_at(symbol)
        merged = _merge_quote_snapshot(symbol, existing, quote, previous_fetched_at=previous_fetched_at, now=now)
        cache.set_snapshot(symbol, merged)
        results.append(_ticker_result(symbol, "success", "价格已更新，基本面沿用缓存", started, source="live_quote"))
        continue
    return results


def _refresh_daily_technical(symbol: str, *, provider: Any, cache: FundamentalCache, now: datetime) -> RefreshTickerResult:
    started = perf_counter()
    snapshot = cache.get_snapshot(symbol, max_age_hours=24 * 3650) or {}
    if snapshot and not should_refresh_technicals(snapshot, now=now):
        synced = _sync_snapshot_to_latest_close(symbol, snapshot, cache=cache, now=now)
        message = "技术缓存仍新鲜，已同步最新收盘价" if synced else "技术缓存仍新鲜，跳过日线刷新"
        return _ticker_result(symbol, "skipped", message, started, source="latest_close_sync" if synced else "")
    try:
        history = provider.get_price_history(symbol, force_refresh=True)
        if _has_history_rows(history):
            _mark_technical_refreshed(symbol, snapshot, cache=cache, now=now, history=history)
            return _ticker_result(symbol, "success", "日线和技术指标缓存已更新", started)
        return _ticker_result(symbol, "failed", "日线无有效数据", started)
    except Exception as exc:
        return _ticker_result(symbol, "failed", f"日线刷新失败：{_short_error(exc)}", started)


def _mark_technical_refreshed(
    symbol: str,
    snapshot: dict,
    *,
    cache: FundamentalCache,
    now: datetime,
    history: Any | None = None,
) -> None:
    merged = dict(snapshot or {})
    merged.setdefault("ticker", symbol)
    merged.setdefault("symbol", symbol)
    refreshed_at = now.isoformat()
    merged["technical_updated_at"] = refreshed_at
    merged["history_updated_at"] = refreshed_at
    merged["price_history_updated_at"] = refreshed_at
    merged["refresh_mode"] = RefreshMode.DAILY_TECHNICAL.value
    _apply_latest_close_to_snapshot(symbol, merged, cache=cache, now=now, history=history)
    cache.set_snapshot(symbol, merged)


def _sync_snapshot_to_latest_close(symbol: str, snapshot: dict, *, cache: FundamentalCache, now: datetime) -> bool:
    merged = dict(snapshot or {})
    merged.setdefault("ticker", symbol)
    merged.setdefault("symbol", symbol)
    if not _apply_latest_close_to_snapshot(symbol, merged, cache=cache, now=now):
        return False
    merged["refresh_mode"] = RefreshMode.DAILY_TECHNICAL.value
    cache.set_snapshot(symbol, merged)
    return True


def _apply_latest_close_to_snapshot(
    symbol: str,
    snapshot: dict,
    *,
    cache: FundamentalCache,
    now: datetime,
    history: Any | None = None,
) -> bool:
    latest = _latest_close_from_history(history) or _latest_cached_history_close(cache.path, symbol)
    latest_close = _number(latest.get("close") if latest else None)
    if latest_close is None or latest_close <= 0:
        return False
    latest_date = str(latest.get("date") or "").strip()
    synced_at = now.isoformat()
    snapshot["current_price"] = latest_close
    snapshot["price"] = latest_close
    snapshot["latest_close"] = latest_close
    snapshot["latestClose"] = latest_close
    snapshot["current_price_source"] = "LAST_CLOSE"
    snapshot["price_session"] = "LAST_CLOSE"
    snapshot["last_close_synced_at"] = synced_at
    snapshot["price_updated_at"] = synced_at
    if latest_date:
        snapshot["price_as_of"] = latest_date
        snapshot["history_latest_date"] = latest_date
    return True


def _latest_close_from_history(history: Any | None) -> dict[str, Any] | None:
    if not _has_history_rows(history):
        return None
    try:
        frame = history.sort_values("date")
        row = frame.iloc[-1]
        return {"date": row.get("date"), "close": row.get("close")}
    except Exception:
        try:
            row = history[-1]
        except Exception:
            return None
        return row if isinstance(row, dict) else None


def _latest_cached_history_close(path: Any, symbol: str) -> dict[str, Any] | None:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                SELECT date, close
                FROM price_history
                WHERE ticker IN (?, ?)
                  AND close IS NOT NULL
                ORDER BY date DESC
                LIMIT 1
                """,
                (normalized, f"FMP:{normalized}"),
            ).fetchone()
    except Exception:
        return None
    if not row:
        return None
    return {"date": row[0], "close": row[1]}


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


def _quote_rows(tickers: list[str], provider: Any) -> QuoteFetchResult:
    rows: dict[str, dict] = {}
    live_symbols: set[str] = set()
    failed_symbols: set[str] = set()
    provider_notes: list[str] = []
    source = "provider_quote"
    if hasattr(provider, "_get_json"):
        now = datetime.now(timezone.utc)
        if not _quote_capability_disabled(BATCH_QUOTE_CAPABILITY, now):
            for chunk in _symbol_chunks(tickers, QUOTE_BATCH_SYMBOLS_PER_REQUEST):
                try:
                    payload = provider._get_json(  # noqa: SLF001 - quote-only endpoint, no fundamentals.
                        "batch-quote",
                        {"symbols": ",".join(chunk)},
                        timeout_seconds=QUOTE_SINGLE_TIMEOUT_SECONDS,
                        retries=0,
                        force_refresh=True,
                    )
                    batch_rows = _quote_payload_rows(payload)
                    rows.update({_quote_symbol(row): row for row in batch_rows if isinstance(row, dict) and _quote_symbol(row)})
                    if rows:
                        source = "batch_quote"
                except Exception as exc:
                    reason = _short_error(exc)
                    if "402" in reason:
                        _disable_quote_capability(BATCH_QUOTE_CAPABILITY, reason, now)
                        provider_notes.append(f"batch quote disabled: {reason}")
                        break
                    provider_notes.append(f"batch quote failed for {len(chunk)} symbols: {reason}")
        else:
            reason = _quote_capability_reason(BATCH_QUOTE_CAPABILITY, now)
            provider_notes.append(f"batch quote disabled: {reason}")

        missing_after_batch = [symbol for symbol in tickers if symbol not in rows]
        if missing_after_batch and len(missing_after_batch) > 1 and not _quote_capability_disabled(MULTI_SYMBOL_QUOTE_CAPABILITY, now):
            any_multi_rows = False
            for chunk in _symbol_chunks(missing_after_batch, QUOTE_MULTI_SYMBOLS_PER_REQUEST):
                try:
                    payload = provider._get_json(  # noqa: SLF001 - existing FMP multi-symbol quote endpoint.
                        "quote",
                        {"symbol": ",".join(chunk)},
                        timeout_seconds=QUOTE_SINGLE_TIMEOUT_SECONDS,
                        retries=0,
                        force_refresh=True,
                    )
                    batch_rows = _quote_payload_rows(payload)
                    before_count = len(rows)
                    rows.update({_quote_symbol(row): row for row in batch_rows if isinstance(row, dict) and _quote_symbol(row)})
                    any_multi_rows = any_multi_rows or len(rows) > before_count
                    if len(rows) > before_count and source not in {"batch_quote"}:
                        source = "multi_symbol_quote"
                except Exception as exc:
                    provider_notes.append(f"multi-symbol quote failed for {len(chunk)} symbols: {_short_error(exc)}")
            if not any_multi_rows and len(missing_after_batch) > 1:
                reason = "empty response"
                _disable_quote_capability(MULTI_SYMBOL_QUOTE_CAPABILITY, reason, now)
                provider_notes.append(f"multi-symbol quote disabled: {reason}")
        elif missing_after_batch and len(missing_after_batch) > 1:
            reason = _quote_capability_reason(MULTI_SYMBOL_QUOTE_CAPABILITY, now)
            provider_notes.append(f"multi-symbol quote disabled: {reason}")

        missing = [symbol for symbol in tickers if symbol not in rows]
        if rows:
            live_symbols.update(rows)
        if missing:
            source = source if source in {"batch_quote", "multi_symbol_quote"} else "concurrent_single_quote"
            provider_notes.append(f"using concurrent single quote fallback: {len(missing)} symbols")
        with ThreadPoolExecutor(max_workers=min(QUOTE_SINGLE_WORKERS, max(1, len(missing)))) as executor:
            futures = {executor.submit(_fetch_single_quote_row, provider, symbol): symbol for symbol in missing}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    row = future.result()
                except Exception:
                    failed_symbols.add(symbol)
                    continue
                if row is not None:
                    rows[symbol] = row
                    live_symbols.add(symbol)
                else:
                    failed_symbols.add(symbol)
        return QuoteFetchResult(rows=rows, live_symbols=live_symbols, failed_symbols=failed_symbols, source=source, provider_notes=provider_notes)
    source = "concurrent_provider_quote"
    with ThreadPoolExecutor(max_workers=min(QUOTE_SINGLE_WORKERS, max(1, len(tickers)))) as executor:
        futures = {executor.submit(_fetch_provider_quote_row, provider, symbol): symbol for symbol in tickers}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                quote = future.result()
            except Exception:
                failed_symbols.add(symbol)
                continue
            if isinstance(quote, dict) and quote:
                rows[symbol] = quote
                live_symbols.add(symbol)
            else:
                failed_symbols.add(symbol)
    return QuoteFetchResult(rows=rows, live_symbols=live_symbols, failed_symbols=failed_symbols, source=source, provider_notes=provider_notes)


def _symbol_chunks(symbols: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [symbols]
    return [symbols[index : index + size] for index in range(0, len(symbols), size)]


def _fetch_single_quote_row(provider: Any, symbol: str) -> dict | None:
    direct_row = _fetch_direct_fmp_quote_short(provider, symbol)
    if direct_row is not None:
        return direct_row
    for endpoint in ("quote-short", "quote"):
        try:
            payload = provider._get_json(  # noqa: SLF001 - quote-only fallback, still avoids fundamentals.
                endpoint,
                {"symbol": symbol},
                timeout_seconds=QUOTE_SINGLE_TIMEOUT_SECONDS,
                retries=0,
                force_refresh=True,
            )
        except Exception:
            continue
        for row in _quote_payload_rows(payload):
            if isinstance(row, dict) and _quote_symbol(row) == symbol:
                return row
    return None


def _fetch_direct_fmp_quote_short(provider: Any, symbol: str) -> dict | None:
    api_key = str(getattr(provider, "api_key", "") or "")
    if not api_key:
        return None
    try:
        from data.providers import _read_url

        query = urlencode({"symbol": symbol, "apikey": api_key})
        request = Request(
            f"https://financialmodelingprep.com/stable/quote-short?{query}",
            headers={"User-Agent": "ZHX-Research/1.0"},
        )
        payload = _read_url(request, timeout_seconds=QUOTE_SINGLE_TIMEOUT_SECONDS)
        for row in _quote_payload_rows(json.loads(payload)):
            if isinstance(row, dict) and _quote_symbol(row) == symbol:
                return row
    except Exception:
        return None
    return None


def _fetch_provider_quote_row(provider: Any, symbol: str) -> dict | None:
    quote = provider.get_quote(symbol, force_refresh=True)
    return quote if isinstance(quote, dict) and quote else None


def _quote_payload_rows(payload: Any) -> list:
    if isinstance(payload, dict):
        for key in ("data", "results", "quotes"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return payload if isinstance(payload, list) else []


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
        "price_change_pct": ("changesPercentage", "changePercentage", "change_pct", "price_change_pct"),
        "volume": ("volume",),
        "market_cap": ("marketCap", "market_cap", "mktCap", "company_market_cap"),
        "fifty_two_week_high": ("yearHigh", "fifty_two_week_high"),
        "fifty_two_week_low": ("yearLow", "fifty_two_week_low"),
        "shares_outstanding": ("sharesOutstanding", "shares_outstanding"),
    }
    for target, keys in field_map.items():
        value = _first_present(quote, *keys)
        if value is not None:
            merged[target] = value
    if merged.get("price_change_pct") in (None, ""):
        price = _number(merged.get("current_price"))
        change = _number(merged.get("price_change"))
        previous_close = price - change if price is not None and change is not None else None
        if previous_close not in (None, 0):
            merged["price_change_pct"] = round((change / previous_close) * 100, 4)
    market_session = _market_session_at_refresh(now)
    if market_session:
        merged["market_session_at_refresh"] = market_session
    quote_session = _quote_price_session(quote)
    if quote_session:
        merged["price_session"] = quote_session
        merged["current_price_source"] = quote_session
    elif market_session == USMarketSession.REGULAR.value:
        merged["price_session"] = USMarketSession.REGULAR.value
        merged["current_price_source"] = USMarketSession.REGULAR.value
    merged["quote_updated_at"] = now.isoformat()
    merged["price_updated_at"] = now.isoformat()
    merged["refresh_mode"] = RefreshMode.PRICE_ONLY.value
    merged["cache_note"] = "仅价格已更新；基本面沿用缓存。"
    return merged


def _market_session_at_refresh(now: datetime) -> str:
    try:
        return get_us_market_session_status(now).status.value
    except Exception:
        return USMarketSession.UNKNOWN.value


def _quote_price_session(quote: dict) -> str:
    raw = _first_present(
        quote,
        "price_session",
        "priceSession",
        "current_price_source",
        "currentPriceSource",
        "market_session",
        "marketSession",
        "session",
        "marketState",
    )
    text = str(raw or "").strip().upper().replace("-", "_").replace(" ", "_")
    mapping = {
        "REGULAR": USMarketSession.REGULAR.value,
        "REGULAR_MARKET": USMarketSession.REGULAR.value,
        "OPEN": USMarketSession.REGULAR.value,
        "PREMARKET": USMarketSession.PRE_MARKET.value,
        "PRE_MARKET": USMarketSession.PRE_MARKET.value,
        "PRE": USMarketSession.PRE_MARKET.value,
        "AFTERHOURS": USMarketSession.AFTER_HOURS.value,
        "AFTER_HOURS": USMarketSession.AFTER_HOURS.value,
        "POSTMARKET": USMarketSession.AFTER_HOURS.value,
        "POST_MARKET": USMarketSession.AFTER_HOURS.value,
        "LAST_CLOSE": "LAST_CLOSE",
        "CLOSE": "LAST_CLOSE",
        "CLOSED": "LAST_CLOSE",
    }
    return mapping.get(text, "")


def _quote_symbol(row: dict) -> str:
    return str(row.get("symbol") or row.get("ticker") or "").strip().upper()


def _first_present(row: dict, *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _has_cached_quote(snapshot: dict) -> bool:
    return _number(snapshot.get("current_price") or snapshot.get("price")) is not None


def _quote_capability_disabled(capability: str, now: datetime) -> bool:
    state = _QUOTE_PROVIDER_CAPABILITIES.get(capability) or {}
    disabled_until = state.get("disabled_until")
    return isinstance(disabled_until, datetime) and disabled_until > now


def _quote_capability_reason(capability: str, now: datetime) -> str:
    state = _QUOTE_PROVIDER_CAPABILITIES.get(capability) or {}
    disabled_until = state.get("disabled_until")
    reason = str(state.get("reason") or "unavailable")
    if isinstance(disabled_until, datetime) and disabled_until > now:
        return f"{reason}; disabled until {disabled_until.isoformat()}"
    return reason


def _disable_quote_capability(capability: str, reason: str, now: datetime) -> None:
    _QUOTE_PROVIDER_CAPABILITIES[capability] = {
        "reason": reason,
        "failure_count": int((_QUOTE_PROVIDER_CAPABILITIES.get(capability) or {}).get("failure_count") or 0) + 1,
        "last_failure_at": now,
        "disabled_until": now + timedelta(hours=QUOTE_CAPABILITY_DISABLE_HOURS),
    }


def _reset_quote_provider_capabilities() -> None:
    _QUOTE_PROVIDER_CAPABILITIES.clear()


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _ticker_result(symbol: str, status: str, message: str, started: float, *, source: str = "") -> RefreshTickerResult:
    return RefreshTickerResult(
        ticker=symbol,
        status=status,
        message=message,
        duration_seconds=round(perf_counter() - started, 3),
        source=source,
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
