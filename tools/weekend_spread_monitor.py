from __future__ import annotations

import argparse
from datetime import datetime, timezone
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.binance_provider import BinanceHTTPPriceProvider, CachedBinancePriceProvider
from data.portfolio import PortfolioPositionStore
from data.weekend_spread import is_binance_symbol_ignored, load_binance_symbol_ignore, load_binance_symbol_mapping
from data.weekend_spread_cache import read_weekend_spread_snapshot
from data.weekend_spread_monitor import DEFAULT_MONITOR_SNAPSHOT_PATH, run_monitor_scan
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
    }
    snapshot = read_weekend_spread_snapshot(mapping=active_mapping, tickers=tickers)
    rows = [row for row in snapshot.get("rows") or [] if str(row.get("ticker") or "").strip().upper() in tickers]
    provider = CachedBinancePriceProvider(BinanceHTTPPriceProvider(), ttl_seconds=45)
    return run_monitor_scan(
        rows,
        price_provider=provider,
        snapshot_path=DEFAULT_MONITOR_SNAPSHOT_PATH,
        now=datetime.now(timezone.utc),
        interval_minutes=float(args.interval_minutes),
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
            print(f"  {label}: {row.get('ticker')} premium={_fmt(row.get('premium_pct'))} change={_fmt(row.get('binance_15m_change_pct'))}")


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
    return "近15分钟" if 13 <= average_elapsed <= 17 else "较上一轮"


def _parse_args():
    parser = argparse.ArgumentParser(description="Weekend spread monitor runner")
    parser.add_argument("--interval-minutes", type=float, default=15)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--symbols", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--only-watchlist", action="store_true")
    parser.add_argument("--only-position", action="store_true", help="Reserved for a read-only position filter.")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
