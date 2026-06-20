from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Iterable
from uuid import uuid4

from data.binance_provider import BinanceHTTPPriceProvider, BinancePriceProvider, CachedBinancePriceProvider
from data.weekend_spread_research import append_monitor_ticks, research_path_for_snapshot
from settings import PROJECT_ROOT


DEFAULT_MONITOR_SNAPSHOT_PATH = PROJECT_ROOT / "data" / "cache" / "weekend_spread_monitor_snapshots.json"
DEFAULT_MONITOR_INTERVAL_MINUTES = 3
MONITOR_SOURCE = "BINANCE_USDT_M"
TREND_WAITING = "等待下一轮比较"
TREND_WAIT_MORE = "等待更多样本"
TREND_STABLE = "价差稳定"
TREND_PREMIUM_EXPAND = "溢价扩大"
TREND_PREMIUM_CONVERGE = "溢价收敛"
TREND_DISCOUNT_EXPAND = "折价扩大"
TREND_DISCOUNT_CONVERGE = "折价收敛"
TREND_REVERSAL = "方向反转"


def run_monitor_scan(
    source_rows: Iterable[dict[str, Any]],
    *,
    price_provider: BinancePriceProvider | None = None,
    price_map: dict[str, float] | None = None,
    snapshot_path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH,
    now: datetime | None = None,
    symbols: Iterable[str] | None = None,
    premium_alert_pct: float = 2.0,
    extreme_premium_pct: float = 5.0,
    price_change_alert_pct: float = 1.0,
    premium_change_alert_pct: float = 1.0,
    interval_minutes: float = DEFAULT_MONITOR_INTERVAL_MINUTES,
    persist_ticks: bool = True,
    research_db_path: Path | None = None,
) -> dict[str, Any]:
    scan_time = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    selected_symbols = {str(item or "").strip().upper() for item in (symbols or []) if str(item or "").strip()}
    rows = [_normalize_source_row(row) for row in source_rows or []]
    if selected_symbols:
        rows = [row for row in rows if row["ticker"] in selected_symbols or row["binance_symbol"] in selected_symbols]

    state = read_monitor_state(snapshot_path)
    previous_by_ticker = _latest_rows_by_ticker(state)
    history_by_ticker = _history_rows_by_ticker(state)
    prices = {str(key or "").strip().upper(): float(value) for key, value in (price_map or {}).items() if _number(value) is not None}
    if price_map is None:
        prices = fetch_bulk_usdm_prices(price_provider or CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=45))

    monitor_rows: list[dict[str, Any]] = []
    skipped = {"ignored": 0, "anchor_missing": 0, "price_missing": 0, "unavailable": 0}
    for row in rows:
        if row["ignored"]:
            skipped["ignored"] += 1
            continue
        if row["excluded"]:
            skipped["unavailable"] += 1
            continue
        if not row["ticker"] or not row["binance_symbol"]:
            skipped["unavailable"] += 1
            continue
        anchor_price = _number(row.get("anchor_price"))
        if anchor_price is None or anchor_price <= 0:
            skipped["anchor_missing"] += 1
            continue
        binance_price = prices.get(row["binance_symbol"])
        if binance_price is None or binance_price <= 0:
            skipped["price_missing"] += 1
            continue
        previous = previous_by_ticker.get(row["ticker"]) or {}
        monitor_rows.append(
            _build_monitor_row(
                row,
                binance_price=binance_price,
                previous=previous,
                previous_history=history_by_ticker.get(row["ticker"], []),
                scan_time=scan_time,
                premium_alert_pct=premium_alert_pct,
                extreme_premium_pct=extreme_premium_pct,
                price_change_alert_pct=price_change_alert_pct,
                premium_change_alert_pct=premium_change_alert_pct,
            )
        )

    run_id = uuid4().hex
    for item in monitor_rows:
        item["run_id"] = run_id
    run = {
        "run_id": run_id,
        "scan_time": scan_time.isoformat(),
        "interval_minutes": interval_minutes,
        "rows": sorted(monitor_rows, key=lambda item: (-abs(_number(item.get("premium_pct")) or 0), item.get("ticker") or "")),
        "summary": summarize_monitor_rows(monitor_rows, skipped=skipped, scan_time=scan_time),
        "source": MONITOR_SOURCE,
        "created_at": scan_time.isoformat(),
    }
    if persist_ticks:
        try:
            append_monitor_ticks(
                run["rows"],
                db_path=research_db_path or research_path_for_snapshot(snapshot_path),
                run_id=run_id,
                scan_time=scan_time,
            )
        except Exception as exc:
            run["summary"]["tick_persist_error"] = str(exc)
    append_monitor_run(run, snapshot_path)
    return run


def fetch_bulk_usdm_prices(provider: BinancePriceProvider) -> dict[str, float]:
    providers = [provider]
    wrapped = getattr(provider, "provider", None)
    if wrapped is not None:
        providers.append(wrapped)
    for candidate in providers:
        getter = getattr(candidate, "_get_market_payload", None)
        if not callable(getter):
            continue
        try:
            payload = getter("usdm_futures", "price", {})
        except Exception:
            continue
        if not isinstance(payload, list):
            continue
        prices: dict[str, float] = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            price = _number(item.get("price"))
            if symbol and price is not None and price > 0:
                prices[symbol] = price
        if prices:
            return prices
    return {}


def read_monitor_state(path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "runs": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "runs": [], "corrupted": True, "message": "监控快照损坏，请重新扫描。"}
    if not isinstance(payload, dict):
        return {"version": 1, "runs": [], "corrupted": True, "message": "监控快照损坏，请重新扫描。"}
    runs = payload.get("runs")
    if not isinstance(runs, list):
        runs = []
    return {"version": 1, "runs": [run for run in runs if isinstance(run, dict)]}


def append_monitor_run(run: dict[str, Any], path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH, *, keep_runs: int = 200) -> dict[str, Any]:
    state = read_monitor_state(path)
    runs = list(state.get("runs") or [])
    runs.append(dict(run))
    state = {"version": 1, "runs": runs[-keep_runs:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, state)
    return state


def latest_monitor_run(path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH) -> dict[str, Any] | None:
    runs = read_monitor_state(path).get("runs") or []
    return runs[-1] if runs else None


def recent_monitor_runs(path: Path = DEFAULT_MONITOR_SNAPSHOT_PATH, *, limit: int = 10) -> list[dict[str, Any]]:
    runs = list(read_monitor_state(path).get("runs") or [])
    return list(reversed(runs[-limit:]))


def summarize_monitor_rows(rows: list[dict[str, Any]], *, skipped: dict[str, int] | None = None, scan_time: datetime | None = None) -> dict[str, Any]:
    skipped = skipped or {}
    return {
        "scan_time": (scan_time or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
        "valid_count": len(rows),
        "anchor_missing_count": int(skipped.get("anchor_missing") or 0),
        "ignored_count": int(skipped.get("ignored") or 0),
        "price_missing_count": int(skipped.get("price_missing") or 0),
        "extreme_count": sum(1 for row in rows if row.get("status") == "极端价差"),
        "direction_reversal_count": sum(1 for row in rows if row.get("premium_trend_label") == TREND_REVERSAL),
        "attention_count": sum(
            1
            for row in rows
            if row.get("status")
            in {
                "重点关注",
                "极端价差",
                TREND_PREMIUM_EXPAND,
                TREND_DISCOUNT_EXPAND,
                TREND_REVERSAL,
            }
        ),
        "top": build_monitor_top(rows),
    }


def build_monitor_top(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    priced = [row for row in rows if _number(row.get("premium_pct")) is not None]
    with_prev = [row for row in rows if _number(row.get("binance_change_since_last_pct")) is not None]
    with_delta = [row for row in rows if _number(row.get("premium_change_since_last_pct_point")) is not None]
    premium_rows = [row for row in with_delta if (_number(row.get("premium_pct")) or 0) > 0]
    return {
        "max_premium": max(priced, key=lambda row: _number(row.get("premium_pct")) or float("-inf"), default=None),
        "max_discount": min(priced, key=lambda row: _number(row.get("premium_pct")) or float("inf"), default=None),
        "max_binance_change": max(with_prev, key=lambda row: _number(row.get("binance_change_since_last_pct")) or float("-inf"), default=None),
        "fastest_premium_expand": max(with_delta, key=lambda row: _number(row.get("premium_change_since_last_pct_point")) or float("-inf"), default=None),
        "fastest_premium_converge": min(
            premium_rows or with_delta,
            key=lambda row: _number(row.get("premium_change_since_last_pct_point")) or float("inf"),
            default=None,
        ),
        "direction_reversal_count": sum(1 for row in rows if row.get("premium_trend_label") == TREND_REVERSAL),
    }


def monitor_history_rows(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for run in runs or []:
        summary = dict(run.get("summary") or {})
        top = dict(summary.get("top") or {})
        history.append(
            {
                "scan_time": run.get("scan_time") or summary.get("scan_time") or "",
                "valid_count": summary.get("valid_count") or 0,
                "max_premium": _top_label(top.get("max_premium"), "premium_pct"),
                "max_discount": _top_label(top.get("max_discount"), "premium_pct"),
                "max_since_last_change": _top_label(top.get("max_binance_change"), "binance_change_since_last_pct"),
                "max_premium_expand": _top_label(top.get("fastest_premium_expand"), "premium_change_since_last_pct_point", suffix=" pct"),
                "direction_reversal_count": top.get("direction_reversal_count") or 0,
                "attention_count": summary.get("attention_count") or 0,
            }
        )
    return history


def _build_monitor_row(
    row: dict[str, Any],
    *,
    binance_price: float,
    previous: dict[str, Any],
    previous_history: list[dict[str, Any]],
    scan_time: datetime,
    premium_alert_pct: float,
    extreme_premium_pct: float,
    price_change_alert_pct: float,
    premium_change_alert_pct: float,
) -> dict[str, Any]:
    anchor_price = float(row["anchor_price"])
    premium_pct = (binance_price / anchor_price - 1) * 100
    previous_price = _number(previous.get("binance_price"))
    previous_premium = _number(previous.get("premium_pct"))
    previous_scan_time = _parse_utc_time(previous.get("scan_time"))
    elapsed_minutes = (scan_time - previous_scan_time).total_seconds() / 60 if previous_scan_time else None
    binance_change = (binance_price / previous_price - 1) * 100 if previous_price and previous_price > 0 else None
    premium_change = premium_pct - previous_premium if previous_premium is not None else None
    regular_close_price = _number(row.get("regular_close_price"))
    vs_regular_close_pct = (binance_price / regular_close_price - 1) * 100 if regular_close_price and regular_close_price > 0 else None
    trend_label = classify_premium_trend(previous_premium, premium_pct, premium_change)
    premium_change_9m, trend_9m = _multi_round_trend(previous_history, premium_pct, lookback_rounds=3)
    premium_change_15m, trend_15m = _multi_round_trend(previous_history, premium_pct, lookback_rounds=5)
    return {
        "run_id": "",
        "scan_time": scan_time.isoformat(),
        "ticker": row["ticker"],
        "binance_symbol": row["binance_symbol"],
        "anchor_price": anchor_price,
        "anchor_time": row.get("anchor_time") or "",
        "binance_price": binance_price,
        "premium_pct": premium_pct,
        "regular_close_price": regular_close_price,
        "vs_regular_close_pct": vs_regular_close_pct,
        "atr14_pct": _number(row.get("atr14_pct")),
        "avg_range_20d_pct": _number(row.get("avg_range_20d_pct")),
        "spread_atr_ratio": _number(row.get("spread_atr_ratio")),
        "spread_reasonableness": row.get("spread_reasonableness") or "",
        "news_label": row.get("news_label") or "",
        "previous_binance_price": previous_price,
        "binance_15m_change_pct": binance_change,
        "binance_change_since_last_pct": binance_change,
        "previous_premium_pct": previous_premium,
        "premium_15m_change_pct": premium_change,
        "premium_change_since_last_pct": premium_change,
        "premium_change_since_last_pct_point": premium_change,
        "premium_change_3m_pct_point": premium_change,
        "premium_change_9m_pct_point": premium_change_9m,
        "premium_change_15m_pct_point": premium_change_15m,
        "premium_trend_label": trend_label,
        "trend_3m_label": trend_label,
        "trend_9m_label": trend_9m,
        "trend_15m_label": trend_15m,
        "previous_scan_time": previous.get("scan_time") or "",
        "elapsed_minutes": elapsed_minutes,
        "status": _monitor_status(
            premium_pct,
            binance_change,
            premium_change,
            trend_label,
            premium_alert_pct=premium_alert_pct,
            extreme_premium_pct=extreme_premium_pct,
            price_change_alert_pct=price_change_alert_pct,
            premium_change_alert_pct=premium_change_alert_pct,
        ),
        "source": MONITOR_SOURCE,
        "created_at": scan_time.isoformat(),
        "is_watchlist": bool(row.get("is_watchlist")),
        "is_position": bool(row.get("is_position")),
        "is_core": bool(row.get("is_core") or row.get("is_core_position")),
    }


def _monitor_status(
    premium_pct: float,
    binance_change_pct: float | None,
    premium_change_pct: float | None,
    trend_label: str,
    *,
    premium_alert_pct: float,
    extreme_premium_pct: float,
    price_change_alert_pct: float,
    premium_change_alert_pct: float,
) -> str:
    if abs(premium_pct) >= extreme_premium_pct:
        return "极端价差"
    if trend_label in {TREND_PREMIUM_EXPAND, TREND_PREMIUM_CONVERGE, TREND_DISCOUNT_EXPAND, TREND_DISCOUNT_CONVERGE, TREND_REVERSAL}:
        return trend_label
    if trend_label == TREND_WAITING:
        return TREND_WAITING
    if trend_label == TREND_STABLE:
        return TREND_STABLE
    if premium_change_pct is not None and abs(premium_change_pct) >= premium_change_alert_pct:
        return "重点关注"
    if binance_change_pct is not None and abs(binance_change_pct) >= price_change_alert_pct:
        return "重点关注"
    if abs(premium_pct) >= premium_alert_pct:
        return "重点关注"
    return "正常"


def classify_premium_trend(
    previous_premium_pct: float | None,
    current_premium_pct: float | None,
    change_pct_point: float | None,
    *,
    stable_threshold_pct_point: float = 0.20,
) -> str:
    if previous_premium_pct is None or current_premium_pct is None or change_pct_point is None:
        return TREND_WAITING
    if previous_premium_pct * current_premium_pct < 0:
        return TREND_REVERSAL
    if abs(change_pct_point) < stable_threshold_pct_point:
        return TREND_STABLE
    if current_premium_pct > 0 and change_pct_point >= stable_threshold_pct_point:
        return TREND_PREMIUM_EXPAND
    if current_premium_pct > 0 and change_pct_point <= -stable_threshold_pct_point:
        return TREND_PREMIUM_CONVERGE
    if current_premium_pct < 0 and change_pct_point <= -stable_threshold_pct_point:
        return TREND_DISCOUNT_EXPAND
    if current_premium_pct < 0 and change_pct_point >= stable_threshold_pct_point:
        return TREND_DISCOUNT_CONVERGE
    return TREND_STABLE


def _multi_round_trend(
    previous_history: list[dict[str, Any]],
    current_premium_pct: float,
    *,
    lookback_rounds: int,
) -> tuple[float | None, str]:
    if len(previous_history) < lookback_rounds:
        return None, TREND_WAIT_MORE
    baseline = previous_history[-lookback_rounds]
    baseline_premium = _number(baseline.get("premium_pct"))
    if baseline_premium is None:
        return None, TREND_WAIT_MORE
    change = current_premium_pct - baseline_premium
    return change, classify_premium_trend(baseline_premium, current_premium_pct, change)


def _normalize_source_row(row: dict[str, Any]) -> dict[str, Any]:
    other_tradfi = _is_other_tradfi_source(row)
    manual_locked = _is_manual_locked_source(row)
    return {
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "binance_symbol": str(row.get("binance_symbol") or "").strip().upper(),
        "anchor_price": _number(row.get("afterhours_reference_price") or row.get("anchor_price")),
        "anchor_time": _normalize_time_text(row.get("afterhours_reference_time") or row.get("anchor_time") or ""),
        "regular_close_price": _number(row.get("regular_close_price") or row.get("friday_close")),
        "atr14_pct": _number(row.get("atr14_pct")),
        "avg_range_20d_pct": _number(row.get("avg_range_20d_pct") or row.get("avg_range_20d")),
        "spread_atr_ratio": _number(row.get("spread_atr_ratio")),
        "spread_reasonableness": str(row.get("spread_reasonableness") or row.get("spread_reasonableness_label") or ""),
        "news_label": str(row.get("closed_market_news_label") or row.get("news_label") or ""),
        "ignored": bool(row.get("ignored")),
        "excluded": other_tradfi and not manual_locked,
        "is_watchlist": bool(row.get("is_watchlist")),
        "is_position": bool(row.get("is_position")),
        "is_core": bool(row.get("is_core") or row.get("is_core_position")),
    }


def _is_manual_locked_source(row: dict[str, Any]) -> bool:
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    confidence = str(row.get("mapping_confidence") or row.get("mapping_status") or "").strip().lower()
    return bool(row.get("manually_locked")) or quality == "人工锁定" or confidence in {"confirmed", "人工锁定"}


def _is_other_tradfi_source(row: dict[str, Any]) -> bool:
    quality = str(row.get("mapping_quality") or row.get("mapping_status") or "").strip()
    bucket = str(row.get("tradfi_bucket") or "").strip().upper()
    underlying = str(row.get("underlying_type") or "").strip().upper()
    category = str(row.get("binance_category") or "").strip().upper()
    note = str(row.get("mapping_risk") or row.get("risk_note") or row.get("mapping_quality_reason") or row.get("reason") or "").upper()
    return (
        quality == "其他 TradFi"
        or bucket == "OTHER_TRADFI"
        or underlying in {"COIN", "COMMODITY", "KR_EQUITY", "INDEX", "PREMARKET"}
        or any(token in category for token in ("其他 TRADFI", "商品", "指数", "RWA", "KR EQUITY"))
        or any(token in note for token in ("其他 TRADFI", "非美股", "商品", "指数", "RWA", "KR EQUITY"))
    )


def _latest_rows_by_ticker(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runs = list(state.get("runs") or [])
    if not runs:
        return {}
    rows = runs[-1].get("rows") if isinstance(runs[-1], dict) else []
    return {str(row.get("ticker") or "").strip().upper(): dict(row) for row in rows or [] if isinstance(row, dict)}


def _history_rows_by_ticker(state: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    history: dict[str, list[dict[str, Any]]] = {}
    for run in state.get("runs") or []:
        if not isinstance(run, dict):
            continue
        for row in run.get("rows") or []:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                history.setdefault(ticker, []).append(dict(row))
    return history


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    last_error: OSError | None = None
    for attempt in range(5):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error


def _parse_utc_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        if text.isdigit():
            try:
                timestamp = int(text)
                if timestamp > 10_000_000_000:
                    timestamp = timestamp / 1000
                return datetime.fromtimestamp(timestamp, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_time_text(value: Any) -> str:
    parsed = _parse_utc_time(value)
    return parsed.isoformat() if parsed is not None else str(value or "")


def _top_label(row: Any, metric_key: str, *, suffix: str = "%") -> str:
    if not isinstance(row, dict):
        return "暂无"
    ticker = str(row.get("ticker") or "").strip().upper() or "UNKNOWN"
    value = _number(row.get(metric_key))
    if value is None:
        return ticker
    return f"{ticker} {value:+.2f}{suffix}"


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
