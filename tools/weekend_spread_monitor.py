from __future__ import annotations

import argparse
from datetime import datetime, time as dt_time, timedelta, timezone
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_provider import BinanceHTTPPriceProvider, CachedBinancePriceProvider
from data.portfolio import PortfolioPositionStore
from data.weekend_spread import is_binance_symbol_ignored, load_binance_symbol_ignore, load_binance_symbol_mapping
from data.weekend_spread_backtest import ET, get_last_us_trading_day_of_week
from data.weekend_spread_cache import read_weekend_spread_snapshot
from data.weekend_spread_monitor import (
    DEFAULT_MONITOR_INTERVAL_MINUTES,
    DEFAULT_MONITOR_SNAPSHOT_PATH,
    MONITOR_MODE_LOOP_PROCESS,
    MONITOR_MODE_MANUAL_ONCE,
    MONITOR_MODE_SCHEDULER,
    run_monitor_scan,
)
from settings import load_watchlist


def main() -> int:
    args = _parse_args()
    interval_seconds = max(1.0, float(args.interval_minutes) * 60)
    while True:
        run = _run_once(args)
        _print_run_summary(run)
        if args.once:
            break
        time.sleep(interval_seconds)
    return 0


def _run_once(args) -> dict:
    mapping = load_binance_symbol_mapping()
    ignored = load_binance_symbol_ignore()
    tickers = _selected_tickers(args, mapping)
    active_mapping = {
        ticker: config
        for ticker, config in mapping.items()
        if ticker in tickers
        and str(config.get("binance_symbol") or "").strip()
        and not is_binance_symbol_ignored(ticker, config.get("binance_symbol"), ignored)
        and _is_monitorable_mapping(config)
    }
    active_tickers = set(active_mapping)
    snapshot = read_weekend_spread_snapshot(
        mapping=active_mapping,
        tickers=active_tickers,
        expected_afterhours_date=_expected_afterhours_date(),
    )
    rows = [
        row
        for row in snapshot.get("rows") or []
        if str(row.get("ticker") or "").strip().upper() in active_tickers and _is_monitorable_mapping(row)
    ]
    provider = CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=45)
    return run_monitor_scan(
        rows,
        price_provider=provider,
        snapshot_path=DEFAULT_MONITOR_SNAPSHOT_PATH,
        now=datetime.now(timezone.utc),
        interval_minutes=float(args.interval_minutes),
        monitor_mode=_monitor_mode_for_source(args.source, once=args.once),
        source=args.source,
    )


def _selected_tickers(args, mapping: dict[str, dict]) -> set[str]:
    if args.symbols:
        return {item.strip().upper() for item in args.symbols.split(",") if item.strip()}
    tickers = {str(ticker or "").strip().upper() for ticker in mapping if str(ticker or "").strip()}
    if args.only_watchlist:
        tickers &= {str(ticker or "").strip().upper() for ticker in load_watchlist() if str(ticker or "").strip()}
    if args.only_position:
        tickers &= {
            str(position.get("symbol") or "").strip().upper()
            for position in PortfolioPositionStore().list_active_positions()
            if str(position.get("symbol") or "").strip()
        }
    return tickers


def _expected_afterhours_date(now: datetime | None = None) -> str:
    current_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    week_start = current_et.date() - timedelta(days=current_et.weekday())
    try:
        last_trading_day = get_last_us_trading_day_of_week(week_start)
    except Exception:
        return ""
    final_cutoff = datetime.combine(last_trading_day, dt_time(20, 5), ET)
    if current_et >= final_cutoff:
        return last_trading_day.isoformat()
    try:
        return get_last_us_trading_day_of_week(week_start - timedelta(days=7)).isoformat()
    except Exception:
        return ""


def _is_monitorable_mapping(config: dict) -> bool:
    if not isinstance(config, dict):
        return False
    if config.get("manually_locked"):
        return True
    quality = str(config.get("mapping_status") or config.get("mapping_quality") or "").strip()
    bucket = str(config.get("tradfi_bucket") or "").strip().upper()
    underlying = str(config.get("underlying_type") or "").strip().upper()
    category = str(config.get("binance_category") or "").strip().upper()
    note = str(config.get("mapping_risk") or config.get("risk_note") or config.get("reason") or "").upper()
    if quality == "其他 TradFi" or bucket == "OTHER_TRADFI":
        return False
    if underlying in {"COIN", "COMMODITY", "KR_EQUITY", "INDEX", "PREMARKET"}:
        return False
    if any(token in category for token in ("其他 TRADFI", "商品", "指数", "RWA", "KR EQUITY")):
        return False
    if any(token in note for token in ("其他 TRADFI", "非美股", "商品", "指数", "RWA", "KR EQUITY")):
        return False
    return True


def _print_run_summary(run: dict) -> None:
    summary = dict(run.get("summary") or {})
    top = dict(summary.get("top") or {})
    delta_label = _delta_label(run)
    print(f"[weekend-monitor] {run.get('scan_time')} valid={summary.get('valid_count', 0)}")
    for key, label in (
        ("max_premium", "最大溢价"),
        ("max_discount", "最大折价"),
        ("max_binance_change", f"{delta_label}涨幅"),
        ("fastest_premium_expand", f"{delta_label}价差扩大"),
        ("fastest_premium_converge", f"{delta_label}价差收敛"),
    ):
        row = top.get(key)
        if isinstance(row, dict):
            print(
                f"  {label}: {row.get('ticker')} premium={_fmt(row.get('premium_pct'))} "
                f"change={_fmt(row.get('binance_change_since_last_pct'))} "
                f"trend={row.get('premium_trend_label') or '等待下一轮比较'}"
            )


def _fmt(value) -> str:
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "待比较"


def _delta_label(run: dict) -> str:
    rows = [row for row in run.get("rows") or [] if isinstance(row, dict)]
    elapsed_values: list[float] = []
    for row in rows:
        try:
            elapsed = float(row.get("elapsed_minutes"))
        except (TypeError, ValueError):
            continue
        if elapsed > 0:
            elapsed_values.append(elapsed)
    if not elapsed_values:
        return "较上一轮"
    average_elapsed = sum(elapsed_values) / len(elapsed_values)
    if 2 <= average_elapsed <= 4:
        return "近3分钟"
    if 8 <= average_elapsed <= 10:
        return "约9分钟"
    if 13 <= average_elapsed <= 17:
        return "约15分钟"
    return "较上一轮"


def _parse_args():
    parser = argparse.ArgumentParser(description="Weekend spread monitor runner")
    parser.add_argument("--interval-minutes", type=float, default=DEFAULT_MONITOR_INTERVAL_MINUTES)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--source", choices=["manual", "scheduler", "loop"], default="")
    parser.add_argument("--monitor-mode", default="", help=argparse.SUPPRESS)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--only-watchlist", action="store_true")
    parser.add_argument("--only-position", action="store_true", help="Reserved for a read-only position filter.")
    args = parser.parse_args()
    if not args.source:
        args.source = _source_from_legacy_mode(args.monitor_mode) or "scheduler"
    return args


def _monitor_mode_for_source(source: str, *, once: bool) -> str:
    if not once:
        return MONITOR_MODE_LOOP_PROCESS
    if source == "manual":
        return MONITOR_MODE_MANUAL_ONCE
    if source == "loop":
        return MONITOR_MODE_LOOP_PROCESS
    return MONITOR_MODE_SCHEDULER


def _source_from_legacy_mode(monitor_mode: str) -> str:
    text = str(monitor_mode or "").strip().lower()
    if text in {"manual", "manual_once"}:
        return "manual"
    if text in {"loop", "loop_process"}:
        return "loop"
    if text == "scheduler":
        return "scheduler"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
