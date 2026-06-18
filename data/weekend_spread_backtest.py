from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from data.binance_provider import BinanceHTTPPriceProvider, CachedBinancePriceProvider, normalize_market_type
from data.weekend_basis import (
    BasisQuote,
    BasisStrategyConfig,
    BrokerOvernightBar,
    evaluate_basis_lock_strategy,
    normalize_basis_quotes,
    normalize_broker_overnight_bars,
)
from data.weekend_spread import load_binance_symbol_mapping
from settings import PROJECT_ROOT


ET = ZoneInfo("America/New_York")
SHANGHAI = ZoneInfo("Asia/Shanghai")
DEFAULT_BACKTEST_RESULTS_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_backtest_results.json"
DEFAULT_BACKTEST_KLINE_CACHE_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_backtest_klines.json"
BACKFILL_THRESHOLDS_BPS = (80.0, 100.0, 120.0, 150.0)
BACKFILL_RELATIVE_WINDOWS_HOURS = (6, 12)
STOCK_OPEN_ANCHORS = ("overnight", "premarket", "regular_open")
STOCK_OPEN_ANCHOR_LABELS = {
    "overnight": "券商夜盘",
    "premarket": "盘前",
    "regular_open": "正式开盘",
}


def _is_us_equity_session_date(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    holidays = _us_market_holidays(day.year) | _us_market_holidays(day.year + 1)
    return day not in holidays


def _us_market_holidays(year: int) -> set[date]:
    holidays = {
        _observed_fixed_holiday(year, 1, 1),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _good_friday(year),
        _last_weekday(year, 5, 0),
        _observed_fixed_holiday(year, 6, 19),
        _observed_fixed_holiday(year, 7, 4),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_fixed_holiday(year, 12, 25),
    }
    return holidays


def _observed_fixed_holiday(year: int, month: int, day: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + 7 * (nth - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    next_month = date(year + int(month == 12), 1 if month == 12 else month + 1, 1)
    current = next_month - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _good_friday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day) - timedelta(days=2)


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


def build_weekend_backtest_preflight(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    anchors: dict[str, Any] | None = None,
    include_unconfirmed: bool = False,
    ticker_filter: str = "",
) -> dict[str, Any]:
    normalized_tickers = _normalize_tickers(tickers)
    effective_mapping = _normalize_mapping(load_binance_symbol_mapping() if mapping is None else mapping)
    effective_anchors = _normalize_mapping(anchors or {})
    selected_filter = str(ticker_filter or "").strip().upper()
    if selected_filter and selected_filter not in {"全部已映射", "ALL"}:
        normalized_tickers = [ticker for ticker in normalized_tickers if ticker == selected_filter]
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for ticker in normalized_tickers:
        config = effective_mapping.get(ticker)
        row = {
            "ticker": ticker,
            "mapping_status": "NO_MAPPING",
            "market_type": "usdm_futures",
            "symbol": "",
            "exclusion_reason": "",
            "can_run": False,
        }
        if not config or not config.get("enabled", True) or not config.get("binance_symbol"):
            row["exclusion_reason"] = "NO_MAPPING"
            excluded.append(row)
            continue
        symbol = str(config.get("binance_symbol") or "").strip().upper()
        confidence = str(config.get("mapping_confidence") or "").strip().lower()
        row.update({"mapping_status": confidence or "unverified", "symbol": symbol})
        if confidence == "candidate" and _is_auto_candidate(config):
            if not include_unconfirmed:
                row["exclusion_reason"] = "AUTO_CANDIDATE_NOT_ALLOWED"
                excluded.append(row)
                continue
        elif confidence != "confirmed" and not include_unconfirmed:
            row["exclusion_reason"] = "UNCONFIRMED_EXCLUDED"
            excluded.append(row)
            continue
        if not symbol:
            row["exclusion_reason"] = "NO_MAPPING"
            excluded.append(row)
            continue
        row["can_run"] = True
        eligible.append(row)
    primary_block = ""
    if not eligible:
        reasons = [str(row.get("exclusion_reason") or "") for row in excluded]
        primary_block = reasons[0] if reasons else "NO_MAPPING"
    return {
        "eligible_tickers": [row["ticker"] for row in eligible],
        "eligible": eligible,
        "excluded_tickers": [row["ticker"] for row in excluded],
        "excluded": excluded,
        "excluded_count": len(excluded),
        "eligible_count": len(eligible),
        "can_run": bool(eligible),
        "primary_block_reason": primary_block,
        "include_unconfirmed": bool(include_unconfirmed),
        "mode": "include candidate" if include_unconfirmed else "confirmed only",
    }


def recent_weekend_windows(*, weeks: int = 4, now: datetime | None = None) -> list[WeekendWindow]:
    current_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    sunday = current_et.date() - timedelta(days=(current_et.weekday() - 6) % 7)
    sunday_close = datetime.combine(sunday, time(20, 0), ET)
    if sunday_close > current_et:
        sunday_close -= timedelta(days=7)
    windows: list[WeekendWindow] = []
    for index in range(max(1, int(weeks or 1))):
        end_et = sunday_close - timedelta(days=index * 7)
        start_et = datetime.combine(end_et.date() - timedelta(days=2), time(20, 0), ET)
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
    anchors: dict[str, Any] | None = None,
    provider: Any | None = None,
    weeks: int = 4,
    open_window_minutes: int = 5,
    kline_cache_path: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized_tickers = _normalize_tickers(tickers)
    ticker_set = set(normalized_tickers)
    effective_mapping = _normalize_mapping(load_binance_symbol_mapping() if mapping is None else mapping)
    effective_anchors = _normalize_mapping(anchors or {})
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    effective_kline_cache_path = kline_cache_path
    if effective_kline_cache_path is None and provider is None:
        effective_kline_cache_path = DEFAULT_BACKTEST_KLINE_CACHE_PATH
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
                    anchor=_anchor_for_ticker(ticker, config, effective_anchors),
                    provider=price_provider,
                    open_window_minutes=open_window_minutes,
                    kline_cache_path=effective_kline_cache_path,
                )
            )
    return rows


def run_weekend_basis_backtest(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    anchors: dict[str, Any] | None = None,
    provider: Any | None = None,
    broker_provider: Any | None = None,
    afterhours_provider: Any | None = None,
    overnight_provider: Any | None = None,
    weeks: int = 4,
    open_window_minutes: int = 5,
    opening_anchor: str = "overnight",
    allow_anchor_fallback: bool = True,
    require_exact_broker_open: bool = False,
    kline_cache_path: Path | None = None,
    strategy_config: BasisStrategyConfig | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    normalized_tickers = _normalize_tickers(tickers)
    effective_mapping = _normalize_mapping(load_binance_symbol_mapping() if mapping is None else mapping)
    effective_anchors = _normalize_mapping(anchors or {})
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    effective_kline_cache_path = kline_cache_path
    if effective_kline_cache_path is None and provider is None:
        effective_kline_cache_path = DEFAULT_BACKTEST_KLINE_CACHE_PATH
    windows = recent_weekend_windows(weeks=weeks, now=now)
    rows: list[dict[str, Any]] = []
    for ticker in normalized_tickers:
        config = effective_mapping.get(ticker)
        if not config or not config.get("enabled", True) or not config.get("binance_symbol"):
            continue
        for window in windows:
            rows.append(
                _basis_backtest_one_window(
                    ticker,
                    config,
                    window,
                    anchor=_audit_anchor_for_ticker(ticker, config, effective_anchors, window),
                    anchor_source=dict(effective_anchors.get(ticker) or config),
                    provider=price_provider,
                    broker_provider=overnight_provider or broker_provider,
                    afterhours_provider=afterhours_provider,
                    open_window_minutes=open_window_minutes,
                    opening_anchor=opening_anchor,
                    allow_anchor_fallback=allow_anchor_fallback,
                    require_exact_broker_open=require_exact_broker_open,
                    kline_cache_path=effective_kline_cache_path,
                    strategy_config=strategy_config,
                )
            )
    return rows


def run_weekend_basis_backfill_audit(
    tickers: Iterable[str],
    *,
    mapping: dict[str, Any] | None = None,
    anchors: dict[str, Any] | None = None,
    provider: Any | None = None,
    broker_provider: Any | None = None,
    weeks: int = 8,
    kline_cache_path: Path | None = None,
    strategy_config: BasisStrategyConfig | None = None,
    now: datetime | None = None,
    include_estimated: bool = True,
    include_observation: bool = True,
    trade_grade_only: bool = False,
    low_risk_window_only: bool = False,
) -> list[dict[str, Any]]:
    """Replay complete historical weekends without using current prices.

    This audit keeps oracle weekend highs as observation only. Formal rows require
    confirmed mapping, historical Binance bid/ask quotes, and broker overnight
    ask quotes after Sunday 20:00 ET.
    """

    normalized_tickers = _normalize_tickers(tickers)
    effective_mapping = _normalize_mapping(load_binance_symbol_mapping() if mapping is None else mapping)
    effective_anchors = _normalize_mapping(anchors or {})
    price_provider = provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider())
    effective_kline_cache_path = kline_cache_path
    if effective_kline_cache_path is None and provider is None:
        effective_kline_cache_path = DEFAULT_BACKTEST_KLINE_CACHE_PATH
    windows = recent_weekend_windows(weeks=weeks, now=now)
    cfg = strategy_config or BasisStrategyConfig()
    rows: list[dict[str, Any]] = []
    for ticker in normalized_tickers:
        config = effective_mapping.get(ticker)
        if not config or not config.get("enabled", True) or not config.get("binance_symbol"):
            rows.extend(_backfill_block_rows(ticker, config or {}, windows, reason="NO_MAPPING"))
            continue
        mapping_confidence = str(config.get("mapping_confidence") or "").strip().lower()
        if mapping_confidence != "confirmed" and (trade_grade_only or not include_observation):
            rows.extend(_backfill_block_rows(ticker, config, windows, reason="BLOCK_MAPPING"))
            continue
        observation_only = mapping_confidence != "confirmed"
        price_ratio_warning = _price_ratio_warning(config)
        for window in windows:
            window_rows = _basis_backfill_one_window(
                ticker,
                config,
                window,
                anchor=_audit_anchor_for_ticker(ticker, config, effective_anchors, window),
                anchor_source=dict(effective_anchors.get(ticker) or config),
                provider=price_provider,
                broker_provider=broker_provider,
                kline_cache_path=effective_kline_cache_path,
                config=cfg,
                include_estimated=include_estimated,
                low_risk_window_only=low_risk_window_only,
            )
            rows.extend(
                _finalize_backfill_rows(
                    window_rows,
                    ticker,
                    config,
                    observation_only=observation_only,
                    price_ratio_warning=price_ratio_warning,
                )
            )
    return rows


def summarize_backfill_audit_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trade_grade = [
        row
        for row in rows
        if str(row.get("data_mode") or "") == "STRICT"
        and str(row.get("mapping_status") or "") != "CANDIDATE_OBSERVATION"
        and str(row.get("data_quality") or "") in {"OK", "ANCHOR_REGULAR_CLOSE_ONLY"}
        and _number(row.get("net_locked_bps")) is not None
    ]
    observation = [
        row
        for row in rows
        if str(row.get("data_mode") or "") == "OBSERVATION"
        and _number(row.get("oracle_weekend_high_premium_bps")) is not None
    ]
    estimated = [
        row
        for row in rows
        if str(row.get("execution_data_mode") or row.get("data_mode") or "") == "ESTIMATED"
        or str(row.get("data_quality") or "") == "ESTIMATED_EXECUTION"
    ]
    excluded = [
        row
        for row in rows
        if str(row.get("status") or "") in {"BLOCK_MAPPING", "BLOCK_DATA", "FAILED"}
        or str(row.get("data_quality") or "") in {"NO_MAPPING", "BINANCE_KLINE_UNAVAILABLE", "NO_PRICE_ANCHOR"}
    ]
    net_values = [_number(row.get("net_locked_bps")) for row in trade_grade]
    net_values = [value for value in net_values if value is not None]
    adverse_values = [_number(row.get("max_adverse_bps")) for row in trade_grade]
    adverse_values = [value for value in adverse_values if value is not None]
    hedge_success = [row for row in trade_grade if row.get("hedge_success")]
    oracle_values = [_number(row.get("oracle_weekend_high_premium_bps")) for row in observation]
    residual_values = [_number(row.get("residual_basis_bps")) for row in observation]
    decay_values = [
        oracle - residual
        for oracle, residual in zip(oracle_values, residual_values)
        if oracle is not None and residual is not None
    ]
    observation_adverse = [_number(row.get("max_adverse_bps")) for row in observation]
    return {
        "sample_count": len(rows),
        "strict_sample_count": len(trade_grade),
        "trade_grade_sample_count": len(trade_grade),
        "observation_sample_count": len(observation),
        "estimated_sample_count": len(estimated),
        "excluded_count": len(excluded),
        "avg_sunday_max_premium_bps": _average(oracle_values),
        "avg_open_residual_basis_bps": _average(residual_values),
        "avg_premium_decay_bps": _average(decay_values),
        "avg_max_adverse_bps": _average(observation_adverse),
        "avg_net_locked_bps": _average(net_values),
        "median_net_locked_bps": _median(net_values),
        "worst_net_locked_bps": min(net_values) if net_values else None,
        "max_adverse_bps": max(adverse_values) if adverse_values else None,
        "hedge_success_rate": len(hedge_success) / len(trade_grade) if trade_grade else None,
    }


def summarize_backtest_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [
        row
        for row in rows
        if _number(row.get("net_locked_bps")) is not None
        and str(row.get("data_quality") or "") == "OK"
    ]
    observe = [
        row
        for row in rows
        if str(row.get("data_quality") or "").upper() in {"OBSERVE_ONLY", "UNCONFIRMED_MAPPING", "OBSERVE_ANCHOR_ONLY"}
    ]
    degraded = [
        row
        for row in rows
        if str(row.get("data_quality") or "").upper().startswith("DEGRADED")
    ]
    excluded = [
        row
        for row in rows
        if str(row.get("data_quality") or "").upper()
        not in {"OK", "OBSERVE_ONLY", "UNCONFIRMED_MAPPING", "OBSERVE_ANCHOR_ONLY"}
        and not str(row.get("data_quality") or "").upper().startswith("DEGRADED")
    ]
    returns_bps = [_number(row.get("net_locked_bps")) for row in valid]
    returns_bps = [value for value in returns_bps if value is not None]
    returns = [value / 100.0 for value in returns_bps]
    positive = [value for value in returns if value > 0]
    decay_ratios = [_number(row.get("premium_decay_ratio")) for row in valid]
    decay_ratios = [value for value in decay_ratios if value is not None]
    theoretical = [_number(row.get("theoretical_short_return_pct")) for row in valid]
    theoretical = [value for value in theoretical if value is not None]
    decay = [_number(row.get("premium_decay_pct")) for row in valid]
    decay = [value for value in decay if value is not None]
    remaining = [_number(row.get("open_remaining_premium_pct")) for row in valid]
    remaining = [value for value in remaining if value is not None]
    return {
        "sample_weeks": len(valid),
        "formal_sample_count": len(valid),
        "observe_sample_count": len(observe),
        "degraded_sample_count": len(degraded),
        "excluded_sample_count": len(excluded),
        "avg_premium_decay_ratio": _average(decay_ratios),
        "avg_theoretical_short_return_pct": _average(theoretical),
        "avg_net_return_pct": _average(returns),
        "avg_net_locked_bps": _average(returns_bps),
        "avg_premium_decay_pct": _average(decay),
        "max_premium_decay_pct": max(decay) if decay else None,
        "max_unflattened_risk_pct": max(remaining) if remaining else None,
        "max_return_pct": max(returns) if returns else None,
        "max_loss_pct": min(returns) if returns else None,
        "positive_weeks": len(positive),
        "win_rate": len(positive) / len(valid) if valid else None,
    }


def save_backtest_results(
    rows: list[dict[str, Any]],
    *,
    preflight: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    path: Path = DEFAULT_BACKTEST_RESULTS_PATH,
    ran_at: datetime | None = None,
    error_message: str = "",
) -> dict[str, Any]:
    payload = {
        "version": 1,
        "last_run_at": (ran_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "rows": list(rows or []),
        "preflight": dict(preflight or {}),
        "params": dict(params or {}),
        "summary": summarize_backtest_results(list(rows or [])),
        "error_message": str(error_message or ""),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def load_backtest_results(path: Path = DEFAULT_BACKTEST_RESULTS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {
            "version": 1,
            "last_run_at": "",
            "rows": [],
            "preflight": {},
            "params": {},
            "summary": summarize_backtest_results([]),
            "error_message": "",
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {
            "version": 1,
            "last_run_at": "",
            "rows": [],
            "preflight": {},
            "params": {},
            "summary": summarize_backtest_results([]),
            "error_message": "backtest cache unreadable",
        }
    rows = list(payload.get("rows") or []) if isinstance(payload, dict) else []
    return {
        "version": int(payload.get("version") or 1) if isinstance(payload, dict) else 1,
        "last_run_at": str(payload.get("last_run_at") or "") if isinstance(payload, dict) else "",
        "rows": rows,
        "preflight": dict(payload.get("preflight") or {}) if isinstance(payload, dict) else {},
        "params": dict(payload.get("params") or {}) if isinstance(payload, dict) else {},
        "summary": dict(payload.get("summary") or summarize_backtest_results(rows)) if isinstance(payload, dict) else summarize_backtest_results(rows),
        "error_message": str(payload.get("error_message") or "") if isinstance(payload, dict) else "",
    }


def clear_backtest_view_state() -> dict[str, Any]:
    return {
        "version": 1,
        "last_run_at": "",
        "rows": [],
        "preflight": {},
        "params": {},
        "summary": summarize_backtest_results([]),
        "error_message": "",
    }


def fetch_friday_afterhours_close(
    symbol: str,
    friday_date_et: Any,
    *,
    provider: Any | None = None,
    anchor_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the Friday 19:55-20:00 ET after-hours closing 1m bar close."""

    normalized = str(symbol or "").strip().upper()
    friday_date = _coerce_date(friday_date_et)
    window_start = datetime.combine(friday_date, time(19, 55), ET)
    window_end = datetime.combine(friday_date, time(20, 0), ET)
    rows: list[Any] = []
    provider_name = ""
    venue = "EXTENDED_HOURS"
    if provider is not None and hasattr(provider, "get_afterhours_bars"):
        provider_name = type(provider).__name__
        rows = list(
            provider.get_afterhours_bars(
                normalized,
                start_time_ms=_to_ms(window_start),
                end_time_ms=_to_ms(window_end),
                interval="1m",
            )
            or []
        )
    elif anchor_source:
        provider_name = str(anchor_source.get("afterhours_reference_source") or "anchor_source")
        rows = list(anchor_source.get("afterhours_bars") or anchor_source.get("afterhours_quotes") or [])
    bars = [
        bar
        for bar in normalize_broker_overnight_bars(rows)
        if window_start.astimezone(timezone.utc) <= bar.ts < window_end.astimezone(timezone.utc)
        and bar.close is not None
        and bar.close > 0
    ]
    if bars:
        chosen = sorted(bars, key=lambda item: item.ts)[-1]
        return {
            "ok": True,
            "symbol": normalized,
            "friday_afterhours_close": chosen.close,
            "bar_start_et": chosen.ts.astimezone(ET).isoformat(),
            "bar_end_et": (chosen.ts + timedelta(minutes=1)).astimezone(ET).isoformat(),
            "provider": provider_name or "afterhours_provider",
            "venue": venue,
            "interval": "1m",
            "quality": "OFFICIAL_AFTERHOURS_1M",
            "reason": "",
        }
    snapshot_price = _number((anchor_source or {}).get("afterhours_reference_price"))
    snapshot_time = _datetime_from_iso((anchor_source or {}).get("afterhours_reference_time"))
    if snapshot_price is not None and snapshot_price > 0 and snapshot_time is None:
        snapshot_time = datetime.combine(friday_date, time(19, 59), ET)
    if snapshot_price is not None and snapshot_price > 0 and snapshot_time is not None:
        snapshot_et = snapshot_time.astimezone(ET)
        if window_start <= snapshot_et < window_end:
            return {
                "ok": True,
                "symbol": normalized,
                "friday_afterhours_close": snapshot_price,
                "bar_start_et": snapshot_et.isoformat(),
                "bar_end_et": (snapshot_et + timedelta(minutes=1)).isoformat(),
                "provider": provider_name or str((anchor_source or {}).get("afterhours_reference_source") or "afterhours_provider"),
                "venue": venue,
                "interval": "1m",
                "quality": "OFFICIAL_AFTERHOURS_REFERENCE" if (anchor_source or {}).get("afterhours_reference_time") else "LEGACY_AFTERHOURS_REFERENCE",
                "reason": "",
            }
    return {
        "ok": False,
        "symbol": normalized,
        "friday_afterhours_close": None,
        "bar_start_et": "",
        "bar_end_et": "",
        "provider": provider_name or "",
        "venue": venue,
        "interval": "1m",
        "quality": "MISSING_AFTERHOURS_CLOSE",
        "reason": "缺少周五盘后收盘价",
    }


def fetch_binance_weekend_max(
    stock_symbol: str,
    binance_symbol: str,
    window_start_et: datetime,
    window_end_et: datetime,
    multiplier: float = 1.0,
    *,
    provider: Any,
    market_type: str = "usdm_futures",
    cache_path: Path | None = None,
) -> dict[str, Any]:
    end_et = window_end_et.astimezone(ET)
    window = WeekendWindow(
        week_id=f"{end_et.date().isocalendar().year}-W{end_et.date().isocalendar().week:02d}",
        start_et=window_start_et.astimezone(ET),
        end_et=end_et,
        end_shanghai=end_et.astimezone(SHANGHAI),
    )
    try:
        bars, _cache_status = _fetch_window_klines(
            provider,
            binance_symbol,
            market_type=market_type,
            window=window,
            open_window_minutes=1,
            end_time=window.end_et,
            cache_path=cache_path,
        )
    except Exception as exc:
        return {
            "ok": False,
            "stock_symbol": str(stock_symbol or "").strip().upper(),
            "binance_symbol": str(binance_symbol or "").strip().upper(),
            "binance_weekend_max": None,
            "binance_equivalent_max": None,
            "binance_max_time": "",
            "kline_count": 0,
            "provider": "BINANCE_USDT_M",
            "interval": "1m",
            "reason": f"数据源错误: {type(exc).__name__}: {exc}",
        }
    fields = _binance_weekend_max_fields(binance_symbol, market_type, window, bars, multiplier=multiplier)
    price = _number(fields.get("binance_weekend_max"))
    return {
        "ok": price is not None and price > 0,
        "stock_symbol": str(stock_symbol or "").strip().upper(),
        "binance_symbol": str(binance_symbol or "").strip().upper(),
        "binance_weekend_max": fields.get("binance_weekend_max"),
        "binance_equivalent_max": fields.get("binance_equivalent_max"),
        "binance_max_time": fields.get("binance_max_time") or "",
        "kline_count": fields.get("kline_count") or 0,
        "provider": "BINANCE_USDT_M",
        "interval": "1m",
        "reason": "" if price is not None and price > 0 else "缺少 Binance 周末 1m K 线",
    }


def fetch_overnight_first_1m_close(
    symbol: str,
    session_start_et: datetime,
    *,
    provider: Any | None = None,
    anchor_source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start_et = session_start_et.astimezone(ET)
    session_date = (start_et + timedelta(days=1)).date()
    result = get_first_valid_stock_bar_after_weekend(
        symbol,
        session_date,
        "overnight",
        2,
        broker_provider=provider,
        anchor_source=anchor_source or {},
        allow_anchor_fallback=False,
        require_exact_start=True,
    )
    if result.get("ok"):
        timestamp = _datetime_from_iso(result.get("timestamp"))
        return {
            "ok": True,
            "symbol": str(symbol or "").strip().upper(),
            "overnight_first_1m_close": result.get("price"),
            "bar_start_et": timestamp.astimezone(ET).isoformat() if timestamp is not None else "",
            "bar_end_et": (timestamp + timedelta(minutes=1)).astimezone(ET).isoformat() if timestamp is not None else "",
            "provider": str(result.get("provider") or ""),
            "venue": "OVERNIGHT",
            "interval": "1m",
            "quality": "OFFICIAL_OVERNIGHT_1M",
            "reason": "",
        }
    return {
        "ok": False,
        "symbol": str(symbol or "").strip().upper(),
        "overnight_first_1m_close": None,
        "bar_start_et": "",
        "bar_end_et": "",
        "provider": str(result.get("provider") or ""),
        "venue": "OVERNIGHT",
        "interval": "1m",
        "quality": str(result.get("quality") or "MISSING_OVERNIGHT_FIRST_1M"),
        "reason": "缺少美股夜盘首分钟 1m K 线",
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
    anchor: dict[str, Any],
    provider: Any,
    open_window_minutes: int,
    kline_cache_path: Path | None,
) -> dict[str, Any]:
    symbol = str(config.get("binance_symbol") or "").strip().upper()
    market_type = "usdm_futures"
    mapping_confidence = str(config.get("mapping_confidence") or "").strip().lower()
    base = _base_result(ticker, symbol, market_type, mapping_confidence, window)
    anchor_price = _number(anchor.get("anchor_price"))
    if anchor_price is None or anchor_price <= 0:
        base.update(
            {
                "data_quality": "INVALID",
                "error_message": "missing anchor price",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return base
    base.update(
        {
            "anchor_price": anchor_price,
            "anchor_source": str(anchor.get("anchor_source") or ""),
        }
    )
    try:
        bars, kline_cache_status = _fetch_window_klines(
            provider,
            symbol,
            market_type=market_type,
            window=window,
            open_window_minutes=open_window_minutes,
            cache_path=kline_cache_path,
        )
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
    open_reference = _open_reference(bars, window, open_window_minutes=open_window_minutes)
    if not weekend_bars or open_reference is None:
        base.update(
            {
                "data_quality": "INVALID",
                "error_message": "missing weekend peak or open reference window",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return base
    peak_bar = max(weekend_bars, key=lambda bar: bar.high)
    peak = peak_bar.high
    open_reference_price = open_reference["price"]
    first_open_bar = open_reference["bar"]
    short_open = _return_pct(peak, open_reference_price)
    short_close = _return_pct(peak, first_open_bar.close)
    best_case = _return_pct(peak, first_open_bar.low)
    worst_case = _return_pct(peak, first_open_bar.high)
    net_return = short_open
    weekend_peak_premium = _premium_pct(peak, anchor_price)
    open_remaining_premium = _premium_pct(open_reference_price, anchor_price)
    premium_decay = None
    premium_decay_ratio = None
    if weekend_peak_premium is not None and open_remaining_premium is not None:
        premium_decay = weekend_peak_premium - open_remaining_premium
        if weekend_peak_premium != 0:
            premium_decay_ratio = premium_decay / weekend_peak_premium * 100.0
    quality = "OK"
    note = "历史观察回测，不构成套利建议。"
    if mapping_confidence != "confirmed":
        quality = "UNCONFIRMED_MAPPING"
        note = "mapping 未 confirmed，结果仅作观察。"
    if kline_cache_status == "CACHE_FALLBACK":
        note = f"{note} 使用缓存 K 线。"
    base.update(
        {
            "weekend_peak_binance_price": peak,
            "weekend_peak_price": peak,
            "weekend_peak_time": peak_bar.open_time.isoformat(),
            "weekend_peak_premium_pct": weekend_peak_premium,
            "open_reference_time": open_reference["time"],
            "open_reference_price": open_reference_price,
            "open_reference_method": open_reference["method"],
            "open_remaining_premium_pct": open_remaining_premium,
            "premium_decay_pct": premium_decay,
            "premium_decay_ratio": premium_decay_ratio,
            "theoretical_short_return_pct": short_open,
            "net_short_return_pct": net_return,
            "monday_bar_open": open_reference_price,
            "monday_bar_high": first_open_bar.high,
            "monday_bar_low": first_open_bar.low,
            "monday_bar_close": first_open_bar.close,
            "monday_bar_volume": first_open_bar.volume,
            "short_return_at_open_pct": short_open,
            "short_return_at_close_pct": short_close,
            "best_case_return_pct": best_case,
            "worst_case_return_pct": worst_case,
            "net_return_at_open_pct": net_return,
            "kline_cache_status": kline_cache_status,
            "data_quality": quality,
            "result_note": note,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return base


def _has_weekend_anchor_observation(result: dict[str, Any], anchor_price: float | None) -> bool:
    if anchor_price is None or anchor_price <= 0:
        return False
    weekend_price = _number(
        result.get("oracle_weekend_high_bid")
        or result.get("binance_weekend_max_price")
        or result.get("weekend_peak_binance_price")
        or result.get("weekend_peak_binance_mid")
    )
    weekend_premium = _number(
        result.get("oracle_weekend_high_premium_bps")
        or result.get("weekend_peak_premium_bps")
        or result.get("weekend_peak_premium_pct")
    )
    return weekend_price is not None and weekend_price > 0 and weekend_premium is not None


def _basis_backtest_one_window(
    ticker: str,
    config: dict[str, Any],
    window: WeekendWindow,
    *,
    anchor: dict[str, Any],
    anchor_source: dict[str, Any],
    provider: Any,
    broker_provider: Any | None,
    afterhours_provider: Any | None,
    open_window_minutes: int,
    opening_anchor: str,
    allow_anchor_fallback: bool,
    require_exact_broker_open: bool,
    kline_cache_path: Path | None,
    strategy_config: BasisStrategyConfig | None,
) -> dict[str, Any]:
    symbol = str(config.get("binance_symbol") or "").strip().upper()
    market_type = "usdm_futures"
    mapping_confidence = str(config.get("mapping_confidence") or "").strip().lower()
    friday_afterhours = fetch_friday_afterhours_close(
        ticker,
        window.start_et.date(),
        provider=afterhours_provider,
        anchor_source=anchor_source,
    )
    friday_afterhours_close = _number(friday_afterhours.get("friday_afterhours_close"))
    anchor_price = friday_afterhours_close
    legacy_anchor_price = _number(anchor.get("anchor_price"))
    base = _base_result(ticker, symbol, market_type, mapping_confidence, window)
    base.update(
        {
            "broker_anchor_price": anchor_price,
            "anchor_price": anchor_price,
            "legacy_anchor_price": legacy_anchor_price,
            "anchor_source": str(anchor.get("anchor_source") or ""),
            "anchor_ts": str(anchor.get("anchor_ts") or ""),
        }
    )
    stock_bar = get_first_valid_stock_bar_after_weekend(
        ticker,
        window.end_et.date() + timedelta(days=1),
        opening_anchor,
        open_window_minutes,
        broker_provider=broker_provider,
        anchor_source=anchor_source,
        allow_anchor_fallback=allow_anchor_fallback,
        require_exact_start=require_exact_broker_open,
    )
    quote_end = _datetime_from_iso(stock_bar.get("requested_end")) or (
        window.end_et + timedelta(minutes=max(1, int(open_window_minutes or 5)))
    )
    binance_max_fields = _missing_binance_weekend_max_fields(symbol, window, reason="BINANCE_KLINE_REQUIRED")
    multiplier = _number(config.get("unit_multiplier") or config.get("multiplier")) or 1.0
    try:
        if hasattr(provider, "get_klines"):
            bars, kline_cache_status = _fetch_window_klines(
                provider,
                symbol,
                market_type=market_type,
                window=window,
                open_window_minutes=open_window_minutes,
                end_time=quote_end,
                cache_path=kline_cache_path,
            )
            quotes = _basis_quotes_from_klines(bars)
            binance_max_fields = _binance_weekend_max_fields(symbol, market_type, window, bars, multiplier=multiplier)
        else:
            quotes, kline_cache_status = _fetch_window_basis_quotes(
                provider,
                symbol,
                market_type=market_type,
                window=window,
                open_window_minutes=open_window_minutes,
                end_time=quote_end,
                cache_path=kline_cache_path,
            )
    except Exception as exc:
        base.update(
            {
                "status": "FAILED",
                "data_quality": "BINANCE_KLINE_UNAVAILABLE",
                "warning": "Binance K 线不可用",
                "error_message": f"{type(exc).__name__}: {exc}",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return base
    cfg = strategy_config or BasisStrategyConfig()
    broker_bars = [stock_bar["bar"]] if stock_bar.get("ok") and stock_bar.get("bar") is not None else []
    if anchor_price is not None and anchor_price > 0:
        evaluation_quotes = _basis_quotes_for_stock_bar_evaluation(quotes, window, stock_bar, cfg, open_window_minutes)
        result = evaluate_basis_lock_strategy(
            ticker=ticker,
            binance_symbol=symbol,
            mapping_confidence=mapping_confidence,
            broker_anchor_price=anchor_price,
            binance_quotes=evaluation_quotes,
            broker_overnight_bars=broker_bars,
            config=cfg,
        )
    else:
        result = dict(base)
        result.update(
            {
                "status": "OBSERVE",
                "data_quality": "NO_AFTERHOURS_CLOSE",
                "warning": "缺少周五盘后收盘价，不能计算完整传导链",
                "error_message": "MISSING_FRIDAY_AFTERHOURS_CLOSE",
            }
        )
    broker_first_close = None
    broker_first_time = ""
    if stock_bar.get("ok") and str(stock_bar.get("anchor") or "") == "overnight" and str(stock_bar.get("bar_size") or "") == "1m":
        broker_first_close = stock_bar.get("price")
        broker_first_time = str(stock_bar.get("timestamp") or "")
    binance_equivalent_max = _number(binance_max_fields.get("binance_equivalent_max") or binance_max_fields.get("binance_weekend_max"))
    binance_premium_pct = _change_pct(binance_equivalent_max, friday_afterhours_close)
    overnight_vs_binance_pct = _change_pct(broker_first_close, binance_equivalent_max)
    overnight_vs_afterhours_pct = _change_pct(broker_first_close, friday_afterhours_close)
    capture_pct = _capture_pct(friday_afterhours_close, binance_equivalent_max, broker_first_close)
    transmission_quality = _weekend_chain_quality(
        mapping_confidence=mapping_confidence,
        friday_afterhours_close=friday_afterhours_close,
        binance_weekend_max=binance_equivalent_max,
        overnight_first_1m_close=broker_first_close,
    )
    result.update(
        {
            "week_id": window.week_id,
            "market_type": market_type,
            "weekend_window_start": window.start_et.isoformat(),
            "weekend_window_end": window.end_et.isoformat(),
            "monday_reference_time_et": window.end_et.isoformat(),
            "monday_reference_time_shanghai": window.end_shanghai.isoformat(),
            "binance_symbol": symbol,
            "binance_provider": "BINANCE_USDT_M",
            "binance_quote_count": len(quotes),
            "stock_open_anchor": str(stock_bar.get("anchor") or opening_anchor),
            "stock_open_anchor_label": str(stock_bar.get("anchor_label") or ""),
            "stock_bar_price": stock_bar.get("price"),
            "stock_bar_timestamp": str(stock_bar.get("timestamp") or ""),
            "stock_bar_provider": str(stock_bar.get("provider") or ""),
            "stock_bar_size": str(stock_bar.get("bar_size") or ""),
            "stock_bar_quality": str(stock_bar.get("quality") or ""),
            "stock_bar_reason": str(stock_bar.get("reason") or ""),
            "stock_bar_returned_count": int(stock_bar.get("returned_bar_count") or 0),
            "stock_bar_requested_start": str(stock_bar.get("requested_start") or ""),
            "stock_bar_requested_end": str(stock_bar.get("requested_end") or ""),
            "broker_first_1m_close": broker_first_close,
            "broker_first_1m_time": broker_first_time,
            "broker_bar_start_time": broker_first_time,
            "broker_bar_end_time": (
                (_datetime_from_iso(broker_first_time) + timedelta(minutes=1)).isoformat()
                if broker_first_time and _datetime_from_iso(broker_first_time) is not None
                else ""
            ),
            "broker_provider": str(stock_bar.get("provider") or ""),
            "broker_interval": str(stock_bar.get("bar_size") or ""),
            "broker_quality": "OFFICIAL_BROKER_1M" if broker_first_close is not None else str(stock_bar.get("quality") or ""),
            "anchor_price": anchor_price,
            "anchor_source": str(anchor.get("anchor_source") or ""),
            "anchor_ts": str(anchor.get("anchor_ts") or ""),
            "afterhours_reference_price": anchor.get("afterhours_reference_price"),
            "afterhours_reference_time": str(anchor.get("afterhours_reference_time") or ""),
            "afterhours_reference_source": str(anchor.get("afterhours_reference_source") or ""),
            "afterhours_data_quality": str(anchor.get("afterhours_data_quality") or ""),
            "afterhours_cache_status": str(anchor.get("afterhours_cache_status") or ""),
            "afterhours_missing_reason": str(anchor.get("afterhours_missing_reason") or ""),
            "friday_afterhours_close": friday_afterhours_close,
            "friday_afterhours_time": str(friday_afterhours.get("bar_start_et") or ""),
            "friday_afterhours_bar_start_et": str(friday_afterhours.get("bar_start_et") or ""),
            "friday_afterhours_bar_end_et": str(friday_afterhours.get("bar_end_et") or ""),
            "friday_afterhours_source": str(friday_afterhours.get("provider") or ""),
            "friday_afterhours_provider": str(friday_afterhours.get("provider") or ""),
            "friday_afterhours_venue": str(friday_afterhours.get("venue") or ""),
            "friday_afterhours_interval": str(friday_afterhours.get("interval") or "1m"),
            "friday_afterhours_quality": str(friday_afterhours.get("quality") or ""),
            "friday_afterhours_reason": str(friday_afterhours.get("reason") or ""),
            "overnight_first_1m_close": broker_first_close,
            "overnight_first_1m_time": broker_first_time,
            "overnight_bar_start_et": broker_first_time,
            "overnight_bar_end_et": (
                (_datetime_from_iso(broker_first_time) + timedelta(minutes=1)).isoformat()
                if broker_first_time and _datetime_from_iso(broker_first_time) is not None
                else ""
            ),
            "overnight_provider": str(stock_bar.get("provider") or ""),
            "overnight_venue": "OVERNIGHT",
            "overnight_interval": str(stock_bar.get("bar_size") or ""),
            "overnight_quality": "OFFICIAL_OVERNIGHT_1M" if broker_first_close is not None else str(stock_bar.get("quality") or ""),
            "overnight_reason": "" if broker_first_close is not None else str(stock_bar.get("reason") or ""),
            "binance_premium_pct": binance_premium_pct,
            "overnight_vs_binance_pct": overnight_vs_binance_pct,
            "overnight_vs_afterhours_pct": overnight_vs_afterhours_pct,
            "capture_pct": capture_pct,
            "transmission_data_quality": transmission_quality,
            "kline_cache_status": kline_cache_status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **binance_max_fields,
        }
    )
    if not stock_bar.get("ok") and result.get("data_quality") == "NO_BROKER_OVERNIGHT_BAR":
        if _has_weekend_anchor_observation(result, anchor_price):
            result.update(
                {
                    "status": "OBSERVE",
                    "data_quality": "OBSERVE_ANCHOR_ONLY",
                    "warning": "已读取周五盘后/收盘锚点和周末合约价格；缺少美股开盘第一根有效价格，仅作价差观察，不计入正式胜率",
                    "error_message": str(stock_bar.get("reason") or "MISSING_STOCK_FIRST_BAR"),
                }
            )
        else:
            result.update(
                {
                    "data_quality": str(stock_bar.get("quality") or "MISSING_STOCK_FIRST_BAR"),
                    "warning": str(stock_bar.get("reason") or "缺少美股端第一根有效价格"),
                    "error_message": str(stock_bar.get("reason") or "MISSING_STOCK_FIRST_BAR"),
                }
            )
    elif stock_bar.get("quality") == "DEGRADED_5M" and result.get("data_quality") == "OK":
        result.update({"data_quality": "DEGRADED_5M", "warning": "使用 5m bar 替代 1m，样本仅作降级观察"})
    if mapping_confidence != "confirmed" and result.get("data_quality") not in {
        "MISSING_STOCK_FIRST_BAR",
        "BINANCE_KLINE_UNAVAILABLE",
        "NO_PRICE_ANCHOR",
        "STALE_OR_MISALIGNED",
    }:
        result.update(
            {
                "data_quality": "OBSERVE_ONLY",
                "mapping_status": "CANDIDATE_OBSERVATION",
                "warning": _append_warning(result.get("warning"), "当前包含未确认映射，结果仅作观察，不计入正式胜率"),
            }
        )
    _apply_basis_compat_fields(result)
    return result


def _basis_backfill_one_window(
    ticker: str,
    mapping_config: dict[str, Any],
    window: WeekendWindow,
    *,
    anchor: dict[str, Any],
    anchor_source: dict[str, Any],
    provider: Any,
    broker_provider: Any | None,
    kline_cache_path: Path | None,
    config: BasisStrategyConfig,
    include_estimated: bool,
    low_risk_window_only: bool,
) -> list[dict[str, Any]]:
    symbol = str(mapping_config.get("binance_symbol") or "").strip().upper()
    market_type = "usdm_futures"
    mapping_confidence = str(mapping_config.get("mapping_confidence") or "").strip().lower()
    base = _base_backfill_row(ticker, mapping_config, window)
    anchor_price = _number(anchor.get("anchor_price"))
    anchor_source_text = str(anchor.get("anchor_source") or "")
    base.update(
        {
            "anchor_ts": str(anchor.get("anchor_ts") or anchor.get("anchor_time") or ""),
            "anchor_price": anchor_price,
            "anchor_source": anchor_source_text,
            "data_quality": "ANCHOR_REGULAR_CLOSE_ONLY" if anchor_source_text == "ANCHOR_REGULAR_CLOSE_ONLY" else "OK",
        }
    )
    if anchor_price is None or anchor_price <= 0:
        base.update({"status": "FAILED", "data_quality": "NO_PRICE_ANCHOR", "warning": "缺少 Friday anchor"})
        return [base]
    try:
        quotes, kline_cache_status = _fetch_window_basis_quotes(
            provider,
            symbol,
            market_type=market_type,
            window=window,
            open_window_minutes=5,
            cache_path=kline_cache_path,
        )
    except Exception as exc:
        base.update(
            {
                "status": "FAILED",
                "data_quality": "BINANCE_KLINE_UNAVAILABLE",
                "warning": "Binance K 线不可用",
                "error_message": f"{type(exc).__name__}: {exc}",
            }
        )
        return [base]
    if not quotes:
        base.update({"status": "FAILED", "data_quality": "BINANCE_KLINE_UNAVAILABLE", "warning": "Binance K 线不可用"})
        return [base]
    if any(quote.estimated for quote in quotes) and not include_estimated:
        observation = dict(base)
        observation.update(
            {
                "status": "FAILED",
                "data_mode": "ESTIMATED",
                "data_quality": "ESTIMATED_EXECUTION",
                "warning": "Binance 历史没有 bid/ask，估算执行不进入 strict statistics",
                "kline_cache_status": kline_cache_status,
            }
        )
        observation.update(_oracle_fields_for_backfill(quotes, anchor_price))
        return [observation]
    data_mode = "ESTIMATED" if any(quote.estimated for quote in quotes) else "STRICT"
    entry_quotes = _entry_window_quotes(quotes, window, low_risk_only=low_risk_window_only)
    broker_bars = _broker_overnight_bars_for_window(
        ticker,
        broker_provider=broker_provider,
        anchor_source=anchor_source,
        window=window,
        open_window_minutes=5,
    )
    hedge_bar = _first_backfill_broker_bar(broker_bars, window, config)
    candidates = _backfill_entry_candidates(entry_quotes, anchor_price, config)
    oracle_fields = _oracle_fields_for_backfill(entry_quotes or quotes, anchor_price)
    if not candidates:
        row = dict(base)
        row.update(
            {
                "status": "OBSERVE",
                "data_mode": data_mode,
                "warning": "未出现历史入场信号",
                "kline_cache_status": kline_cache_status,
                **oracle_fields,
            }
        )
        return [row]
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(base)
        row.update({"data_mode": data_mode, "kline_cache_status": kline_cache_status, **oracle_fields})
        entry_quote = candidate["quote"]
        entry_price = entry_quote.bid
        entry_premium = (entry_price / anchor_price - 1.0) * 10_000.0
        row.update(
            {
                "status": "SHORT_OPEN",
                "rule_name": candidate["rule_name"],
                "entry_window": "LOW_RISK" if _is_low_risk_entry_window(entry_quote.ts, window) else "FULL_SUNDAY",
                "entry_ts": entry_quote.ts.isoformat(),
                "entry_price": entry_price,
                "binance_entry_bid": entry_price,
                "binance_entry_ask": entry_quote.ask,
                "entry_premium_bps": entry_premium,
                "relative_high_rank": candidate.get("percentile"),
                "pullback_bps": candidate.get("pullback_bps"),
                "rolling_high_premium_bps": candidate.get("rolling_high_premium_bps"),
                "data_quality": "ESTIMATED_EXECUTION" if data_mode == "ESTIMATED" else row.get("data_quality") or "OK",
            }
        )
        if hedge_bar is None:
            row.update(
                {
                    "status": "WAIT_BROKER_OPEN",
                    "data_quality": "NO_BROKER_OVERNIGHT_BAR",
                    "warning": "缺少 Sunday 20:00 ET 后券商 overnight 第一根有效 1m bar",
                    "hedge_success": False,
                }
            )
            rows.append(row)
            continue
        aligned_quote = _nearest_quote_for_backfill(quotes, hedge_bar.ts, max_seconds=config.max_alignment_seconds)
        if aligned_quote is None:
            row.update(
                {
                    "status": "HEDGE_DUE",
                    "data_quality": "STALE_OR_MISALIGNED",
                    "warning": "Binance 与 broker 时间差超过 60 秒",
                    "hedge_success": False,
                }
            )
            rows.append(row)
            continue
        gross_locked_bps = (entry_price / hedge_bar.ask - 1.0) * 10_000.0
        net_locked_bps = gross_locked_bps - config.fees_bps - config.funding_bps - config.slippage_bps
        residual_basis_bps = (aligned_quote.mid / hedge_bar.mid - 1.0) * 10_000.0
        max_adverse_bps = _max_adverse_for_short(quotes, entry_quote.ts, hedge_bar.ts, entry_price)
        row.update(
            {
                "status": "HEDGE_LOCKED",
                "broker_hedge_ts": hedge_bar.ts.isoformat(),
                "broker_hedge_price": hedge_bar.ask,
                "broker_hedge_bid": hedge_bar.bid,
                "broker_hedge_ask": hedge_bar.ask,
                "gross_locked_bps": gross_locked_bps,
                "net_locked_bps": net_locked_bps,
                "residual_basis_bps": residual_basis_bps,
                "max_adverse_bps": max_adverse_bps,
                "time_unhedged_minutes": max(0.0, (hedge_bar.ts - entry_quote.ts).total_seconds() / 60.0),
                "hedge_success": True,
                "data_quality": "OK" if data_mode == "STRICT" and row.get("data_quality") != "ANCHOR_REGULAR_CLOSE_ONLY" else row.get("data_quality"),
                "warning": "" if data_mode == "STRICT" else "ESTIMATED_EXECUTION",
            }
        )
        rows.append(row)
    return rows


def _backfill_block_rows(ticker: str, mapping_config: dict[str, Any], windows: list[WeekendWindow], *, reason: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for window in windows:
        row = _base_backfill_row(ticker, mapping_config, window)
        row.update(
            {
                "status": "BLOCK_MAPPING" if reason == "BLOCK_MAPPING" else "BLOCK_DATA",
                "data_mode": "OBSERVATION",
                "data_quality": reason,
                "warning": "映射未确认，仅观察，不能进入正式收益统计" if reason == "BLOCK_MAPPING" else "暂无 confirmed mapping",
            }
        )
        rows.append(row)
    return rows


def _finalize_backfill_rows(
    rows: list[dict[str, Any]],
    ticker: str,
    mapping_config: dict[str, Any],
    *,
    observation_only: bool,
    price_ratio_warning: str,
) -> list[dict[str, Any]]:
    finalized: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        row.setdefault("broker_symbol", str(mapping_config.get("broker_symbol") or ticker).strip().upper())
        row.setdefault("mapping_status", "CONFIRMED_TRADE_GRADE")
        row.setdefault("trade_grade_eligible", True)
        row.setdefault("friday_anchor_price", row.get("anchor_price"))
        row.setdefault("sunday_max_premium_bps", row.get("oracle_weekend_high_premium_bps"))
        row.setdefault("sunday_max_ts", row.get("oracle_weekend_high_time"))
        row.setdefault("sunday_relative_high_premium_bps", row.get("rolling_high_premium_bps") or row.get("entry_premium_bps"))
        row.setdefault("broker_overnight_open_ts", row.get("broker_hedge_ts"))
        row.setdefault("broker_overnight_open_price", row.get("broker_hedge_price"))
        row.setdefault("open_residual_basis_bps", row.get("residual_basis_bps"))
        row.setdefault("premium_decay_bps", _premium_decay_bps(row))
        if observation_only:
            previous_mode = str(row.get("data_mode") or "")
            if previous_mode and previous_mode != "OBSERVATION":
                row["execution_data_mode"] = previous_mode
            row["data_mode"] = "OBSERVATION"
            row["mapping_status"] = "CANDIDATE_OBSERVATION"
            row["trade_grade_eligible"] = False
            row["warning"] = _append_warning(
                row.get("warning"),
                "映射未人工确认，仅供观察复盘，不作为交易依据",
            )
        if price_ratio_warning:
            row["price_ratio_warning"] = True
            row["warning"] = _append_warning(row.get("warning"), price_ratio_warning)
        finalized.append(row)
    return finalized


def _premium_decay_bps(row: dict[str, Any]) -> float | None:
    oracle = _number(row.get("oracle_weekend_high_premium_bps"))
    residual = _number(row.get("residual_basis_bps"))
    if oracle is None or residual is None:
        return None
    return oracle - residual


def _price_ratio_warning(mapping_config: dict[str, Any]) -> str:
    summary = mapping_config.get("audit_summary") if isinstance(mapping_config.get("audit_summary"), dict) else {}
    ratio = _number(summary.get("median_ratio") if isinstance(summary, dict) else None)
    if ratio is None:
        return ""
    if 0.98 <= ratio <= 1.02:
        return ""
    severity = "黄色警告" if 0.95 <= ratio <= 1.05 else "红色警告"
    return f"PRICE_RATIO_WARNING：{severity}，价格比例异常，可能不是 1:1 映射"


def _append_warning(current: Any, addition: str) -> str:
    parts = [str(item or "").strip() for item in (current, addition)]
    return "；".join(dict.fromkeys([item for item in parts if item]))


def _backfill_entry_candidates(entry_quotes: list[BasisQuote], anchor_price: float, cfg: BasisStrategyConfig) -> list[dict[str, Any]]:
    if not entry_quotes:
        return []
    candidates: list[dict[str, Any]] = []
    seen_rules: set[str] = set()
    premiums = [(quote.bid / anchor_price - 1.0) * 10_000.0 for quote in entry_quotes]
    for threshold in BACKFILL_THRESHOLDS_BPS:
        rule = f"FIRST_THRESHOLD_{int(threshold)}"
        for index, premium in enumerate(premiums[:-1]):
            previous = premiums[index - 1] if index > 0 else -999_999.0
            if previous < threshold <= premium:
                next_quote = entry_quotes[index + 1]
                if _quote_passes_backfill_liquidity(next_quote, cfg):
                    candidates.append(
                        {
                            "rule_name": rule,
                            "quote": next_quote,
                            "percentile": _percentile_rank(premiums[: index + 2], premiums[index + 1]),
                            "pullback_bps": max(0.0, max(premiums[: index + 2]) - premiums[index + 1]),
                            "rolling_high_premium_bps": max(premiums[: index + 2]),
                        }
                    )
                    seen_rules.add(rule)
                break
    for hours in BACKFILL_RELATIVE_WINDOWS_HOURS:
        rule = f"RELATIVE_HIGH_PULLBACK_{hours}H"
        if rule in seen_rules:
            continue
        window_delta = timedelta(hours=hours)
        for quote in entry_quotes:
            history = [item for item in entry_quotes if quote.ts - window_delta <= item.ts <= quote.ts]
            if len(history) < 2:
                continue
            historical_premiums = [(item.bid / anchor_price - 1.0) * 10_000.0 for item in history]
            current = historical_premiums[-1]
            rolling_high = max(historical_premiums)
            pullback = rolling_high - current
            percentile = _percentile_rank(historical_premiums, current)
            if rolling_high < cfg.min_entry_premium_bps:
                continue
            if current < cfg.min_entry_premium_bps - cfg.allowed_pullback_bps:
                continue
            if pullback < cfg.min_pullback_bps or pullback > min(30.0, cfg.max_pullback_bps):
                continue
            if percentile < max(85.0, cfg.min_percentile):
                continue
            if not _quote_passes_backfill_liquidity(quote, cfg):
                continue
            candidates.append(
                {
                    "rule_name": rule,
                    "quote": quote,
                    "percentile": percentile,
                    "pullback_bps": pullback,
                    "rolling_high_premium_bps": rolling_high,
                }
            )
            break
    return sorted(candidates, key=lambda item: (item["quote"].ts, str(item["rule_name"])))


def _entry_window_quotes(quotes: list[BasisQuote], window: WeekendWindow, *, low_risk_only: bool) -> list[BasisQuote]:
    sunday = window.end_et.date()
    start_hour = 16 if low_risk_only else 0
    start = datetime.combine(sunday, time(start_hour, 0), ET).astimezone(timezone.utc)
    end = datetime.combine(sunday, time(19, 55), ET).astimezone(timezone.utc)
    return [quote for quote in quotes if start <= quote.ts <= end]


def _is_low_risk_entry_window(ts: datetime, window: WeekendWindow) -> bool:
    sunday = window.end_et.date()
    start = datetime.combine(sunday, time(16, 0), ET).astimezone(timezone.utc)
    end = datetime.combine(sunday, time(19, 55), ET).astimezone(timezone.utc)
    return start <= _ensure_utc_local(ts) <= end


def _first_backfill_broker_bar(bars: list[BrokerOvernightBar], window: WeekendWindow, cfg: BasisStrategyConfig) -> BrokerOvernightBar | None:
    target = window.end_et.astimezone(timezone.utc)
    latest_allowed = target + timedelta(minutes=60)
    for bar in sorted(bars, key=lambda item: item.ts):
        if bar.ts < target or bar.ts >= latest_allowed:
            continue
        if bar.quote_age_seconds > cfg.max_alignment_seconds:
            continue
        if bar.spread_bps > cfg.max_broker_spread_bps:
            continue
        return bar
    return None


def _nearest_quote_for_backfill(quotes: list[BasisQuote], target: datetime, *, max_seconds: int) -> BasisQuote | None:
    if not quotes:
        return None
    target_utc = _ensure_utc_local(target)
    nearest = min(quotes, key=lambda quote: abs((quote.ts - target_utc).total_seconds()))
    return nearest if abs((nearest.ts - target_utc).total_seconds()) <= max_seconds else None


def _max_adverse_for_short(quotes: list[BasisQuote], entry_ts: datetime, hedge_ts: datetime, entry_price: float) -> float | None:
    if entry_price <= 0:
        return None
    window_quotes = [quote for quote in quotes if entry_ts <= quote.ts <= hedge_ts]
    if not window_quotes:
        return None
    highest_ask = max(quote.ask for quote in window_quotes)
    return max(0.0, (highest_ask / entry_price - 1.0) * 10_000.0)


def _quote_passes_backfill_liquidity(quote: BasisQuote, cfg: BasisStrategyConfig) -> bool:
    if quote.spread_bps > cfg.max_binance_spread_bps:
        return False
    return not (quote.depth_usd is not None and quote.depth_usd < cfg.min_depth_usd)


def _oracle_fields_for_backfill(quotes: list[BasisQuote], anchor_price: float) -> dict[str, Any]:
    if not quotes or anchor_price <= 0:
        return {}
    best = max(quotes, key=lambda quote: (quote.bid / anchor_price - 1.0) * 10_000.0)
    return {
        "oracle_weekend_high_bid": best.bid,
        "oracle_weekend_high_time": best.ts.isoformat(),
        "oracle_weekend_high_premium_bps": (best.bid / anchor_price - 1.0) * 10_000.0,
        "oracle_note": "事后高点，不可交易",
    }


def _base_backfill_row(ticker: str, mapping_config: dict[str, Any], window: WeekendWindow) -> dict[str, Any]:
    symbol = str((mapping_config or {}).get("binance_symbol") or "").strip().upper()
    mapping_confidence = str((mapping_config or {}).get("mapping_confidence") or "").strip().lower()
    broker_symbol = str((mapping_config or {}).get("broker_symbol") or ticker).strip().upper()
    return {
        "week_id": window.week_id,
        "ticker": str(ticker or "").strip().upper(),
        "broker_symbol": broker_symbol,
        "rule_name": "",
        "entry_window": "",
        "binance_symbol": symbol,
        "market_type": "usdm_futures",
        "mapping_confidence": mapping_confidence,
        "mapping_status": "CONFIRMED_TRADE_GRADE" if mapping_confidence == "confirmed" else "CANDIDATE_OBSERVATION",
        "trade_grade_eligible": mapping_confidence == "confirmed",
        "weekend_window_start": window.start_et.astimezone(timezone.utc).isoformat(),
        "weekend_window_end": window.end_et.astimezone(timezone.utc).isoformat(),
        "monday_reference_time_et": window.end_et.isoformat(),
        "monday_reference_time_shanghai": window.end_shanghai.isoformat(),
        "status": "OBSERVE",
        "data_mode": "STRICT",
        "anchor_ts": "",
        "anchor_price": None,
        "entry_ts": "",
        "entry_price": None,
        "binance_entry_bid": None,
        "binance_entry_ask": None,
        "entry_premium_bps": None,
        "broker_hedge_ts": "",
        "broker_hedge_price": None,
        "broker_hedge_bid": None,
        "broker_hedge_ask": None,
        "net_locked_bps": None,
        "residual_basis_bps": None,
        "max_adverse_bps": None,
        "time_unhedged_minutes": None,
        "hedge_success": False,
        "data_quality": "DATA_INSUFFICIENT",
        "warning": "",
        "oracle_weekend_high_bid": None,
        "oracle_weekend_high_time": "",
        "oracle_weekend_high_premium_bps": None,
        "oracle_note": "",
        "friday_anchor_price": None,
        "sunday_max_premium_bps": None,
        "sunday_max_ts": "",
        "sunday_relative_high_premium_bps": None,
        "broker_overnight_open_ts": "",
        "broker_overnight_open_price": None,
        "open_residual_basis_bps": None,
        "premium_decay_bps": None,
        "error_message": "",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_window_basis_quotes(
    provider: Any,
    symbol: str,
    *,
    market_type: str,
    window: WeekendWindow,
    open_window_minutes: int,
    end_time: datetime | None = None,
    cache_path: Path | None = None,
) -> tuple[list[BasisQuote], str]:
    effective_end = end_time or window.end_et + timedelta(minutes=max(1, int(open_window_minutes or 5)))
    if hasattr(provider, "get_basis_quotes"):
        payload = provider.get_basis_quotes(
            symbol,
            market_type=market_type,
            start_time_ms=_to_ms(window.start_et),
            end_time_ms=_to_ms(effective_end),
        )
        return normalize_basis_quotes(payload, estimated=False, source="basis_quotes"), "API_LIVE"
    bars, cache_status = _fetch_window_klines(
        provider,
        symbol,
        market_type=market_type,
        window=window,
        open_window_minutes=open_window_minutes,
        end_time=effective_end,
        cache_path=cache_path,
    )
    return _basis_quotes_from_klines(bars), cache_status


def _basis_quotes_from_klines(bars: list[NormalizedKline]) -> list[BasisQuote]:
    rows = [
        {
            "ts": bar.open_time,
            "bid": bar.close,
            "ask": bar.close,
            "source": "binance_kline_estimated",
            "estimated_execution": True,
        }
        for bar in bars
    ]
    return normalize_basis_quotes(rows, estimated=True, source="binance_kline_estimated")


def _binance_weekend_max_fields(
    symbol: str,
    market_type: str,
    window: WeekendWindow,
    bars: list[NormalizedKline],
    *,
    multiplier: float = 1.0,
) -> dict[str, Any]:
    window_start = window.start_et.astimezone(timezone.utc)
    window_end = window.end_et.astimezone(timezone.utc)
    weekend_bars = [bar for bar in bars if window_start <= bar.open_time < window_end]
    if not weekend_bars:
        return _missing_binance_weekend_max_fields(symbol, window, reason="BINANCE_KLINE_UNAVAILABLE")
    peak = max(weekend_bars, key=lambda bar: bar.high)
    effective_multiplier = float(multiplier or 1.0)
    equivalent = peak.high * effective_multiplier
    return {
        "binance_symbol": str(symbol or "").strip().upper(),
        "binance_provider": "BINANCE_USDT_M",
        "binance_interval": "1m",
        "binance_window_start_et": window.start_et.isoformat(),
        "binance_window_end_et": window.end_et.isoformat(),
        "binance_weekend_max": peak.high,
        "binance_weekend_max_price": peak.high,
        "binance_equivalent_max": equivalent,
        "binance_max_time": peak.open_time.astimezone(ET).isoformat(),
        "binance_weekend_max_time": peak.open_time.astimezone(ET).isoformat(),
        "kline_count": len(weekend_bars),
        "binance_kline_count": len(weekend_bars),
        "binance_multiplier": effective_multiplier,
        "binance_market_type": normalize_market_type(market_type),
        "binance_weekend_max_reason": "",
    }


def _missing_binance_weekend_max_fields(symbol: str, window: WeekendWindow, *, reason: str) -> dict[str, Any]:
    return {
        "binance_symbol": str(symbol or "").strip().upper(),
        "binance_provider": "BINANCE_USDT_M",
        "binance_interval": "1m",
        "binance_window_start_et": window.start_et.isoformat(),
        "binance_window_end_et": window.end_et.isoformat(),
        "binance_weekend_max": None,
        "binance_weekend_max_price": None,
        "binance_equivalent_max": None,
        "binance_max_time": "",
        "binance_weekend_max_time": "",
        "kline_count": 0,
        "binance_kline_count": 0,
        "binance_multiplier": 1.0,
        "binance_market_type": "usdm_futures",
        "binance_weekend_max_reason": reason,
    }


def _broker_overnight_bars_for_window(
    ticker: str,
    *,
    broker_provider: Any | None,
    anchor_source: dict[str, Any],
    window: WeekendWindow,
    open_window_minutes: int,
) -> list[BrokerOvernightBar]:
    start_ms = _to_ms(window.end_et)
    end_ms = _to_ms(window.end_et + timedelta(minutes=max(1, int(open_window_minutes or 5))))
    if broker_provider is not None and hasattr(broker_provider, "get_overnight_bars"):
        return normalize_broker_overnight_bars(
            broker_provider.get_overnight_bars(
                ticker,
                start_time_ms=start_ms,
                end_time_ms=end_ms,
                interval="1m",
            )
        )
    return normalize_broker_overnight_bars(anchor_source.get("broker_overnight_bars") or anchor_source.get("broker_overnight_quotes") or [])


def _apply_basis_compat_fields(row: dict[str, Any]) -> None:
    net_locked = _number(row.get("net_locked_bps"))
    gross_locked = _number(row.get("gross_locked_bps"))
    oracle = _number(row.get("oracle_weekend_high_premium_bps"))
    residual = _number(row.get("residual_basis_bps"))
    row["net_short_return_pct"] = net_locked / 100.0 if net_locked is not None else None
    row["net_return_at_open_pct"] = row["net_short_return_pct"]
    row["theoretical_short_return_pct"] = gross_locked / 100.0 if gross_locked is not None else None
    row["short_return_at_open_pct"] = row["theoretical_short_return_pct"]
    row["weekend_peak_premium_pct"] = oracle / 100.0 if oracle is not None else None
    row["weekend_peak_binance_price"] = row.get("oracle_weekend_high_bid")
    row["weekend_peak_price"] = row.get("oracle_weekend_high_bid")
    row["weekend_peak_time"] = row.get("oracle_weekend_high_time") or ""
    row["open_remaining_premium_pct"] = residual / 100.0 if residual is not None else None
    if oracle is not None and residual is not None:
        premium_decay_bps = oracle - residual
        row["premium_decay_pct"] = premium_decay_bps / 100.0
        row["premium_decay_ratio"] = premium_decay_bps / oracle * 100.0 if oracle else None
    else:
        row["premium_decay_pct"] = None
        row["premium_decay_ratio"] = None


def get_first_valid_stock_bar_after_weekend(
    symbol: str,
    session_date: Any,
    anchor: str,
    window_minutes: int,
    *,
    broker_provider: Any | None = None,
    anchor_source: dict[str, Any] | None = None,
    allow_anchor_fallback: bool = True,
    require_exact_start: bool = False,
) -> dict[str, Any]:
    """Find the first reliable US stock bar after the weekend.

    The function is intentionally diagnostic-heavy because this page must never
    silently reuse current prices or stale cache as historical execution data.
    """

    requested_anchor = _normalize_stock_open_anchor(anchor)
    start_date = _coerce_date(session_date)
    window = max(1, int(window_minutes or 5))
    attempts = _stock_open_attempts(start_date, requested_anchor, allow_fallback=allow_anchor_fallback)
    if not attempts:
        return {
            "ok": False,
            "price": None,
            "timestamp": "",
            "provider": "",
            "bar_size": "1m",
            "quality": "HOLIDAY_OR_NO_SESSION",
            "reason": "HOLIDAY_OR_NO_SESSION",
            "returned_bar_count": 0,
            "requested_start": "",
            "requested_end": "",
            "anchor": requested_anchor,
            "anchor_label": STOCK_OPEN_ANCHOR_LABELS.get(requested_anchor, requested_anchor),
            "bar": None,
        }
    last_attempt: dict[str, Any] | None = None
    intervals = ("1m",) if require_exact_start else ("1m", "5m")
    for interval in intervals:
        for anchor_name, day in attempts:
            start, end = _stock_open_window(day, anchor_name, window)
            payload = _fetch_stock_open_bars(
                symbol,
                broker_provider=broker_provider,
                anchor_source=anchor_source or {},
                start=start,
                end=end,
                interval=interval,
            )
            bars = payload["bars"]
            attempt = {
                "ok": False,
                "price": None,
                "timestamp": "",
                "provider": payload["provider"],
                "bar_size": interval,
                "quality": "MISSING_STOCK_FIRST_BAR",
                "reason": "MISSING_STOCK_FIRST_BAR",
                "returned_bar_count": payload["returned_bar_count"],
                "requested_start": start.astimezone(timezone.utc).isoformat(),
                "requested_end": end.astimezone(timezone.utc).isoformat(),
                "anchor": anchor_name,
                "anchor_label": STOCK_OPEN_ANCHOR_LABELS.get(anchor_name, anchor_name),
                "bar": None,
            }
            if last_attempt is None or int(attempt.get("returned_bar_count") or 0) >= int(last_attempt.get("returned_bar_count") or 0):
                last_attempt = attempt
            first = _first_valid_stock_bar(bars, start, end, require_exact_start=require_exact_start)
            if first is None:
                continue
            quality = "OK" if interval == "1m" else "DEGRADED_5M"
            return {
                "ok": True,
                "price": first.close,
                "timestamp": first.ts.astimezone(timezone.utc).isoformat(),
                "provider": payload["provider"],
                "bar_size": interval,
                "quality": quality,
                "reason": "",
                "returned_bar_count": payload["returned_bar_count"],
                "requested_start": start.astimezone(timezone.utc).isoformat(),
                "requested_end": end.astimezone(timezone.utc).isoformat(),
                "anchor": anchor_name,
                "anchor_label": STOCK_OPEN_ANCHOR_LABELS.get(anchor_name, anchor_name),
                "bar": first,
            }
    return last_attempt or {
        "ok": False,
        "price": None,
        "timestamp": "",
        "provider": "",
        "bar_size": "1m",
        "quality": "MISSING_STOCK_FIRST_BAR",
        "reason": "MISSING_STOCK_FIRST_BAR",
        "returned_bar_count": 0,
        "requested_start": "",
        "requested_end": "",
        "anchor": requested_anchor,
        "anchor_label": STOCK_OPEN_ANCHOR_LABELS.get(requested_anchor, requested_anchor),
        "bar": None,
    }


def _basis_quotes_for_stock_bar_evaluation(
    quotes: list[BasisQuote],
    window: WeekendWindow,
    stock_bar: dict[str, Any],
    cfg: BasisStrategyConfig,
    open_window_minutes: int,
) -> list[BasisQuote]:
    if not quotes:
        return []
    entry_end = window.end_et.astimezone(timezone.utc) + timedelta(minutes=max(1, int(open_window_minutes or 5)))
    selected: list[BasisQuote] = [quote for quote in quotes if quote.ts <= entry_end]
    bar = stock_bar.get("bar")
    if isinstance(bar, BrokerOvernightBar):
        aligned_start = bar.ts - timedelta(seconds=cfg.max_alignment_seconds)
        aligned_end = bar.ts + timedelta(seconds=cfg.max_alignment_seconds)
        selected.extend([quote for quote in quotes if aligned_start <= quote.ts <= aligned_end])
    deduped: dict[datetime, BasisQuote] = {quote.ts: quote for quote in selected}
    return [deduped[key] for key in sorted(deduped)]


def _stock_open_attempts(session_date, requested_anchor: str, *, allow_fallback: bool = True) -> list[tuple[str, Any]]:
    if not allow_fallback:
        for offset in range(0, 6):
            day = session_date + timedelta(days=offset)
            if _is_us_equity_session_date(day):
                return [(requested_anchor, day)]
        return []
    anchor_order = {
        "overnight": ["overnight", "premarket", "regular_open"],
        "premarket": ["premarket", "regular_open"],
        "regular_open": ["regular_open"],
    }.get(requested_anchor, ["premarket", "regular_open"])
    attempts: list[tuple[str, Any]] = []
    for offset in range(0, 6):
        day = session_date + timedelta(days=offset)
        if not _is_us_equity_session_date(day):
            continue
        for anchor_name in anchor_order:
            attempts.append((anchor_name, day))
        if attempts and day != session_date:
            break
    return attempts


def _stock_open_window(session_date, anchor_name: str, window_minutes: int) -> tuple[datetime, datetime]:
    if anchor_name == "overnight":
        start = datetime.combine(session_date - timedelta(days=1), time(20, 0), ET)
    elif anchor_name == "regular_open":
        start = datetime.combine(session_date, time(9, 30), ET)
    else:
        start = datetime.combine(session_date, time(4, 0), ET)
    return start, start + timedelta(minutes=max(1, int(window_minutes or 5)))


def _fetch_stock_open_bars(
    symbol: str,
    *,
    broker_provider: Any | None,
    anchor_source: dict[str, Any],
    start: datetime,
    end: datetime,
    interval: str,
) -> dict[str, Any]:
    rows: list[Any] = []
    provider_name = ""
    if broker_provider is not None and hasattr(broker_provider, "get_overnight_bars"):
        provider_name = "broker"
        rows = list(
            broker_provider.get_overnight_bars(
                symbol,
                start_time_ms=_to_ms(start),
                end_time_ms=_to_ms(end),
                interval=interval,
            )
            or []
        )
    else:
        provider_name = "anchor_source"
        rows = list(anchor_source.get("broker_overnight_bars") or anchor_source.get("broker_overnight_quotes") or [])
    bars = [
        bar
        for bar in normalize_broker_overnight_bars(rows)
        if start.astimezone(timezone.utc) <= bar.ts < end.astimezone(timezone.utc)
    ]
    return {"bars": bars, "provider": provider_name, "returned_bar_count": len(rows)}


def _first_valid_stock_bar(
    bars: list[BrokerOvernightBar],
    start: datetime,
    end: datetime,
    *,
    require_exact_start: bool = False,
) -> BrokerOvernightBar | None:
    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    for bar in sorted(bars, key=lambda item: item.ts):
        if require_exact_start and bar.ts != start_utc:
            continue
        if start_utc <= bar.ts < end_utc and bar.bid > 0 and bar.ask > 0 and bar.ask >= bar.bid and bar.close is not None and bar.close > 0:
            return bar
    return None


def _normalize_stock_open_anchor(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in STOCK_OPEN_ANCHORS else "premarket"


def _coerce_date(value: Any):
    if hasattr(value, "year") and hasattr(value, "month") and hasattr(value, "day") and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.astimezone(ET).date()
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return datetime.now(ET).date()


def _datetime_from_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fetch_window_klines(
    provider: Any,
    symbol: str,
    *,
    market_type: str,
    window: WeekendWindow,
    open_window_minutes: int,
    end_time: datetime | None = None,
    cache_path: Path | None = DEFAULT_BACKTEST_KLINE_CACHE_PATH,
) -> tuple[list[NormalizedKline], str]:
    start_ms = _to_ms(window.start_et)
    effective_end = end_time or window.end_et + timedelta(minutes=max(1, int(open_window_minutes or 5)))
    end_ms = _to_ms(effective_end)
    cursor = start_ms
    all_payload: list[Any] = []
    cache_key = _kline_cache_key(symbol, market_type, start_ms, end_ms)
    try:
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
        bars = normalize_klines(all_payload)
        if bars:
            _write_kline_cache(cache_path, cache_key, symbol=symbol, market_type=market_type, start_ms=start_ms, end_ms=end_ms, bars=bars)
            return bars, "API_LIVE"
        cached = _read_kline_cache(cache_path, cache_key)
        if cached:
            return cached, "CACHE_FALLBACK"
        return bars, "API_LIVE"
    except Exception:
        cached = _read_kline_cache(cache_path, cache_key)
        if cached:
            return cached, "CACHE_FALLBACK"
        raise


def _kline_cache_key(symbol: str, market_type: str, start_ms: int, end_ms: int) -> str:
    return f"{normalize_market_type(market_type)}:{str(symbol or '').strip().upper()}:1m:{start_ms}:{end_ms}"


def _read_kline_cache(path: Path | None, cache_key: str) -> list[NormalizedKline]:
    if path is None or not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return []
    entry = dict((payload.get("entries") or {}).get(cache_key) or {})
    return _deserialize_klines(entry.get("bars") or [])


def _write_kline_cache(
    path: Path | None,
    cache_key: str,
    *,
    symbol: str,
    market_type: str,
    start_ms: int,
    end_ms: int,
    bars: list[NormalizedKline],
) -> None:
    if path is None or not bars:
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        payload = {}
    entries = dict(payload.get("entries") or {})
    entries[cache_key] = {
        "symbol": str(symbol or "").strip().upper(),
        "market_type": normalize_market_type(market_type),
        "interval": "1m",
        "start_ms": start_ms,
        "end_ms": end_ms,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "bars": [_serialize_kline(bar) for bar in bars],
    }
    payload = {"version": 1, "entries": entries}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _serialize_kline(bar: NormalizedKline) -> dict[str, Any]:
    return {
        "open_time": bar.open_time.astimezone(timezone.utc).isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _deserialize_klines(rows: Iterable[Any]) -> list[NormalizedKline]:
    bars: list[NormalizedKline] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            open_time = datetime.fromisoformat(str(row.get("open_time") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if open_time.tzinfo is None:
            open_time = open_time.replace(tzinfo=timezone.utc)
        open_price = _number(row.get("open"))
        high = _number(row.get("high"))
        low = _number(row.get("low"))
        close = _number(row.get("close"))
        if open_price is None or high is None or low is None or close is None:
            continue
        bars.append(
            NormalizedKline(
                open_time=open_time.astimezone(timezone.utc),
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=_number(row.get("volume")),
            )
        )
    deduped: dict[datetime, NormalizedKline] = {bar.open_time: bar for bar in bars}
    return [deduped[key] for key in sorted(deduped)]


def _monday_open_bar(bars: list[NormalizedKline], window: WeekendWindow) -> NormalizedKline | None:
    target = window.end_et.astimezone(timezone.utc)
    for bar in bars:
        if bar.open_time >= target:
            return bar
    return None


def _open_reference(bars: list[NormalizedKline], window: WeekendWindow, *, open_window_minutes: int) -> dict[str, Any] | None:
    target = window.end_et.astimezone(timezone.utc)
    end = target + timedelta(minutes=max(1, int(open_window_minutes or 5)))
    window_bars = [bar for bar in bars if target <= bar.open_time < end]
    if not window_bars:
        return None
    vwap = _vwap(window_bars)
    first = window_bars[0]
    if vwap is not None:
        return {
            "price": vwap,
            "method": f"VWAP_{max(1, int(open_window_minutes or 5))}M",
            "time": first.open_time.isoformat(),
            "bar": first,
        }
    return {
        "price": first.open,
        "method": "FIRST_OPEN",
        "time": first.open_time.isoformat(),
        "bar": first,
    }


def _vwap(bars: list[NormalizedKline]) -> float | None:
    weighted = 0.0
    volume_sum = 0.0
    for bar in bars:
        if bar.volume is None or bar.volume <= 0:
            continue
        weighted += bar.close * bar.volume
        volume_sum += bar.volume
    if volume_sum <= 0:
        return None
    return weighted / volume_sum


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
        "anchor_price": None,
        "anchor_source": "",
        "anchor_ts": "",
        "weekend_peak_binance_price": None,
        "weekend_peak_price": None,
        "weekend_peak_time": "",
        "weekend_peak_premium_pct": None,
        "open_reference_time": "",
        "open_reference_price": None,
        "open_reference_method": "",
        "open_remaining_premium_pct": None,
        "premium_decay_pct": None,
        "premium_decay_ratio": None,
        "theoretical_short_return_pct": None,
        "net_short_return_pct": None,
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
        "kline_cache_status": "",
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


def _premium_pct(price: float | None, anchor_price: float | None) -> float | None:
    if price is None or anchor_price is None or anchor_price <= 0:
        return None
    return (price - anchor_price) / anchor_price * 100.0


def _change_pct(price: float | None, base_price: float | None) -> float | None:
    if price is None or base_price is None or base_price <= 0:
        return None
    return (price / base_price - 1.0) * 100.0


def _capture_pct(afterhours_close: float | None, weekend_max: float | None, overnight_close: float | None) -> float | None:
    if afterhours_close is None or weekend_max is None or overnight_close is None:
        return None
    denominator = weekend_max - afterhours_close
    if denominator == 0:
        return None
    return (overnight_close - afterhours_close) / denominator * 100.0


def _weekend_chain_quality(
    *,
    mapping_confidence: str,
    friday_afterhours_close: float | None,
    binance_weekend_max: float | None,
    overnight_first_1m_close: float | None,
) -> str:
    if str(mapping_confidence or "").strip().lower() != "confirmed":
        return "OBSERVE_ONLY"
    if binance_weekend_max is None or binance_weekend_max <= 0:
        return "CONTRACT_MISSING"
    if friday_afterhours_close is None or friday_afterhours_close <= 0:
        return "NO_AFTERHOURS_CLOSE"
    if overnight_first_1m_close is None or overnight_first_1m_close <= 0:
        return "MISSING_OVERNIGHT_FIRST_1M"
    return "OK"


def _anchor_for_ticker(ticker: str, config: dict[str, Any], anchors: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source = anchors.get(ticker) or config
    afterhours = _number(source.get("afterhours_reference_price"))
    if afterhours is not None and afterhours > 0:
        return {"anchor_price": afterhours, "anchor_source": "AFTERHOURS_REFERENCE"}
    regular = _number(source.get("regular_close_price") or source.get("friday_close") or source.get("friday_close_price"))
    if regular is not None and regular > 0:
        return {"anchor_price": regular, "anchor_source": "REGULAR_CLOSE"}
    return {"anchor_price": None, "anchor_source": "MISSING"}


def _audit_anchor_for_ticker(ticker: str, config: dict[str, Any], anchors: dict[str, dict[str, Any]], window: WeekendWindow) -> dict[str, Any]:
    root = anchors.get(ticker) or config
    weekly = root.get("weekly_anchors") if isinstance(root.get("weekly_anchors"), dict) else {}
    source = weekly.get(window.week_id) or root.get(window.week_id)
    anchor_by_week = root.get("anchor_by_week")
    if not source and isinstance(anchor_by_week, dict):
        source = anchor_by_week.get(window.week_id)
    if not isinstance(source, dict):
        source = root
    afterhours = _number(source.get("afterhours_reference_price"))
    if afterhours is not None and afterhours > 0:
        return {
            "anchor_price": afterhours,
            "anchor_source": str(source.get("anchor_source") or "AFTERHOURS_REFERENCE"),
            "anchor_ts": str(source.get("afterhours_reference_time") or ""),
            "afterhours_reference_price": afterhours,
            "afterhours_reference_time": str(source.get("afterhours_reference_time") or ""),
            "afterhours_reference_source": str(source.get("afterhours_reference_source") or ""),
            "afterhours_data_quality": str(source.get("afterhours_data_quality") or ""),
            "afterhours_cache_status": str(source.get("afterhours_cache_status") or ""),
            "afterhours_missing_reason": str(source.get("afterhours_missing_reason") or ""),
        }
    regular = _number(source.get("regular_close_price") or source.get("friday_close") or source.get("friday_close_price"))
    if regular is not None and regular > 0:
        regular_source = str(source.get("anchor_source") or "ANCHOR_REGULAR_CLOSE_ONLY")
        return {
            "anchor_price": regular,
            "anchor_source": regular_source,
            "anchor_ts": str(source.get("regular_close_date") or source.get("friday_close_date") or ""),
            "afterhours_reference_price": source.get("afterhours_reference_price"),
            "afterhours_reference_time": str(source.get("afterhours_reference_time") or ""),
            "afterhours_reference_source": str(source.get("afterhours_reference_source") or ""),
            "afterhours_data_quality": str(source.get("afterhours_data_quality") or ""),
            "afterhours_cache_status": str(source.get("afterhours_cache_status") or ""),
            "afterhours_missing_reason": str(source.get("afterhours_missing_reason") or ""),
        }
    return {"anchor_price": None, "anchor_source": "MISSING", "anchor_ts": ""}


def _is_auto_candidate(config: dict[str, Any]) -> bool:
    risk_note = str(config.get("risk_note") or "")
    confidence = str(config.get("mapping_confidence") or "").strip().lower()
    return confidence == "candidate" and ("ticker+USDT" in risk_note or "自动生成" in risk_note)


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


def _median(values: Iterable[float | None]) -> float | None:
    numbers = sorted(float(value) for value in values if value is not None)
    if not numbers:
        return None
    midpoint = len(numbers) // 2
    if len(numbers) % 2:
        return numbers[midpoint]
    return (numbers[midpoint - 1] + numbers[midpoint]) / 2.0


def _percentile_rank(values: list[float], value: float) -> float:
    if not values:
        return 0.0
    return sum(1 for item in values if item <= value) / len(values) * 100.0


def _ensure_utc_local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
