from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from data.binance_provider import BinanceHTTPPriceProvider, CachedBinancePriceProvider, normalize_market_type
from data.weekend_spread import load_binance_symbol_mapping


ET = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_FEE_PCT = 0.10
DEFAULT_SLIPPAGE_PCT = 0.10
DEFAULT_FUNDING_PCT = 0.00


@dataclass(frozen=True)
class WeekendWindow:
    week_id: str
    start_et: datetime
    end_et: datetime
    end_shanghai: datetime


@dataclass(frozen=True)
class NormalizedKline:
    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


def recent_weekend_windows(*, weeks: int = 4, now: datetime | None = None) -> list[WeekendWindow]:
    current_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    sunday = current_et.date() - timedelta(days=(current_et.weekday() - 6) % 7)
    sunday_close = datetime.combine(sunday, time(20, 0), ET)
    if sunday_close > current_et:
        sunday_close -= timedelta(days=7)
    windows: list[WeekendWindow] = []
    for index in range(max(1, int(weeks or 1))):
        end_et = sunday_close - timedelta(days=index * 7)
        start_et = end_et - timedelta(days=2)
        iso = end_et.date().isocalendar()
        windows.append(
            WeekendWindow(
                week_id=f"{iso.year}-W{iso.week:02d}",
                start_et=start_et,
                end_et=end_et,
                end_shanghai=end_et.astimezone(SHANGHAI),
            )
        )
    return windows


def run_weekend_peak_short_backtest(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    provider: Any | None = None,
    weeks: int = 4,
    fee_pct: float = DEFAULT_FEE_PCT,
    slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
    funding_pct: float = DEFAULT_FUNDING_PCT,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized_tickers = _normalize_tickers(tickers)
    ticker_set = set(normalized_tickers)
    effective_mapping = _normalize_mapping(load_binance_symbol_mapping() if mapping is None else mapping)
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    windows = recent_weekend_windows(weeks=weeks, now=now)
    rows: list[dict[str, Any]] = []
    for ticker in normalized_tickers:
        config = effective_mapping.get(ticker)
        if not config or not config.get("enabled", True) or not config.get("binance_symbol") or ticker not in ticker_set:
            continue
        for window in windows:
            rows.append(
                _backtest_one_window(
                    ticker,
                    config,
                    window,
                    provider=price_provider,
                    fee_pct=fee_pct,
                    slippage_pct=slippage_pct,
                    funding_pct=funding_pct,
                )
            )
    return rows


def summarize_backtest_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if _number(row.get("net_return_at_open_pct")) is not None]
    returns = [_number(row.get("net_return_at_open_pct")) for row in valid]
    returns = [value for value in returns if value is not None]
    positive = [value for value in returns if value > 0]
    return {
        "sample_weeks": len(valid),
        "avg_net_return_pct": _average(returns),
        "max_return_pct": max(returns) if returns else None,
        "max_loss_pct": min(returns) if returns else None,
        "positive_weeks": len(positive),
        "win_rate": len(positive) / len(valid) if valid else None,
    }


def normalize_klines(payload: Iterable[Any]) -> list[NormalizedKline]:
    bars: list[NormalizedKline] = []
    for item in payload:
        bar = _normalize_kline(item)
        if bar is not None:
            bars.append(bar)
    deduped: dict[datetime, NormalizedKline] = {bar.open_time: bar for bar in bars}
    return [deduped[key] for key in sorted(deduped)]


def _backtest_one_window(
    ticker: str,
    config: dict[str, Any],
    window: WeekendWindow,
    *,
    provider: Any,
    fee_pct: float,
    slippage_pct: float,
    funding_pct: float,
) -> dict[str, Any]:
    symbol = str(config.get("binance_symbol") or "").strip().upper()
    market_type = normalize_market_type(str(config.get("market_type") or "usdm_futures"))
    mapping_confidence = str(config.get("mapping_confidence") or "").strip().lower()
    base = _base_result(ticker, symbol, market_type, mapping_confidence, window)
    try:
        bars = _fetch_window_klines(provider, symbol, market_type=market_type, window=window)
    except Exception as exc:  # provider errors must not break the page
        base.update(
            {
                "data_quality": "DATA_UNAVAILABLE",
                "error_message": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return base
    weekend_bars = [bar for bar in bars if window.start_et.astimezone(timezone.utc) <= bar.open_time < window.end_et.astimezone(timezone.utc)]
    monday_bar = _monday_open_bar(bars, window)
    if not weekend_bars or monday_bar is None:
        base.update(
            {
                "data_quality": "DATA_UNAVAILABLE",
                "error_message": "missing weekend peak or monday open bar",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return base
    peak_bar = max(weekend_bars, key=lambda bar: bar.high)
    peak = peak_bar.high
    short_open = _return_pct(peak, monday_bar.open)
    short_close = _return_pct(peak, monday_bar.close)
    best_case = _return_pct(peak, monday_bar.low)
    worst_case = _return_pct(peak, monday_bar.high)
    net_return = None
    if short_open is not None:
        net_return = short_open - float(fee_pct or 0.0) - float(slippage_pct or 0.0) - float(funding_pct or 0.0)
    quality = "OK"
    note = "历史观察回测，不构成套利建议。"
    if mapping_confidence != "confirmed":
        quality = "UNCONFIRMED_MAPPING"
        note = "mapping 未 confirmed，结果仅作观察。"
    elif market_type == "spot":
        quality = "SPOT_OBSERVATION_ONLY"
        note = "spot 映射仅作观察收益，不代表合约空单收益。"
    base.update(
        {
            "weekend_peak_price": peak,
            "weekend_peak_time": peak_bar.open_time.isoformat(),
            "monday_bar_open": monday_bar.open,
            "monday_bar_high": monday_bar.high,
            "monday_bar_low": monday_bar.low,
            "monday_bar_close": monday_bar.close,
            "monday_bar_volume": monday_bar.volume,
            "short_return_at_open_pct": short_open,
            "short_return_at_close_pct": short_close,
            "best_case_return_pct": best_case,
            "worst_case_return_pct": worst_case,
            "net_return_at_open_pct": net_return,
            "data_quality": quality,
            "result_note": note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return base


def _fetch_window_klines(provider: Any, symbol: str, *, market_type: str, window: WeekendWindow) -> list[NormalizedKline]:
    start_ms = _to_ms(window.start_et)
    end_ms = _to_ms(window.end_et + timedelta(minutes=1))
    cursor = start_ms
    all_payload: list[Any] = []
    for _ in range(10):
        payload = provider.get_klines(
            symbol,
            market_type=market_type,
            interval="1m",
            start_time_ms=cursor,
            end_time_ms=end_ms,
            limit=1000,
        )
        if not payload:
            break
        bars = normalize_klines(payload)
        if not bars:
            break
        all_payload.extend(payload)
        next_cursor = _to_ms(bars[-1].open_time + timedelta(minutes=1))
        if next_cursor <= cursor or next_cursor >= end_ms:
            break
        cursor = next_cursor
    return normalize_klines(all_payload)


def _monday_open_bar(bars: list[NormalizedKline], window: WeekendWindow) -> NormalizedKline | None:
    target = window.end_et.astimezone(timezone.utc)
    for bar in bars:
        if bar.open_time >= target:
            return bar
    return None


def _base_result(ticker: str, symbol: str, market_type: str, mapping_confidence: str, window: WeekendWindow) -> dict[str, Any]:
    return {
        "week_id": window.week_id,
        "ticker": ticker,
        "binance_symbol": symbol,
        "market_type": market_type,
        "mapping_confidence": mapping_confidence,
        "weekend_window_start": window.start_et.isoformat(),
        "weekend_window_end": window.end_et.isoformat(),
        "monday_reference_time_et": window.end_et.isoformat(),
        "monday_reference_time_shanghai": window.end_shanghai.isoformat(),
        "weekend_peak_price": None,
        "weekend_peak_time": "",
        "monday_bar_open": None,
        "monday_bar_high": None,
        "monday_bar_low": None,
        "monday_bar_close": None,
        "monday_bar_volume": None,
        "short_return_at_open_pct": None,
        "short_return_at_close_pct": None,
        "best_case_return_pct": None,
        "worst_case_return_pct": None,
        "net_return_at_open_pct": None,
        "data_quality": "DATA_INSUFFICIENT",
        "result_note": "",
        "error_message": "",
        "updated_at": "",
    }


def _normalize_kline(item: Any) -> NormalizedKline | None:
    if isinstance(item, dict):
        open_time = _datetime_from_ms(item.get("open_time") or item.get("openTime") or item.get("time"))
        open_price = _number(item.get("open"))
        high = _number(item.get("high"))
        low = _number(item.get("low"))
        close = _number(item.get("close"))
        volume = _number(item.get("volume"))
    elif isinstance(item, (list, tuple)) and len(item) >= 6:
        open_time = _datetime_from_ms(item[0])
        open_price = _number(item[1])
        high = _number(item[2])
        low = _number(item[3])
        close = _number(item[4])
        volume = _number(item[5])
    else:
        return None
    if open_time is None or open_price is None or high is None or low is None or close is None:
        return None
    return NormalizedKline(open_time=open_time, open=open_price, high=high, low=low, close=close, volume=volume)


def _datetime_from_ms(value: Any) -> datetime | None:
    number = _number(value)
    if number is None:
        return None
    return datetime.fromtimestamp(number / 1000.0, timezone.utc)


def _to_ms(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _return_pct(peak: float | None, exit_price: float | None) -> float | None:
    if peak is None or exit_price is None or peak <= 0:
        return None
    return (peak - exit_price) / peak * 100.0


def _normalize_tickers(tickers: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in tickers:
        ticker = str(item or "").strip().upper()
        if not ticker or ticker in seen:
            continue
        result.append(ticker)
        seen.add(ticker)
    return result


def _normalize_mapping(mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = mapping.get("mappings") if isinstance(mapping.get("mappings"), dict) else mapping
    return {str(key or "").upper(): value for key, value in raw.items() if isinstance(value, dict)}


def _average(values: Iterable[float | None]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
