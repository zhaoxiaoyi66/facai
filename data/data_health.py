from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from buy_zone_engine import generate_buy_zone
from data.cache_read_model import CacheReadModel
from data.prices import CACHE_PATH
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.total_score import calculate_total_score
from settings import load_watchlist


def build_data_health_summary(
    path: Path = CACHE_PATH,
    watchlist: list[str] | None = None,
    now: datetime | None = None,
    quote_max_age_hours: float = 24,
    history_max_age_hours: float = 72,
) -> dict[str, Any]:
    symbols = _normalize_symbols(watchlist if watchlist is not None else load_watchlist())
    summary = _empty_summary()
    summary["cacheExists"] = path.exists()
    if not path.exists():
        _add_issue(summary, "cache_missing", None, "cache.sqlite 不存在")
        return summary

    current_time = now or datetime.now(timezone.utc)
    cache = CacheReadModel(
        path,
        now=current_time,
        quote_max_age_hours=quote_max_age_hours,
        history_max_age_hours=history_max_age_hours,
    )
    healthy_symbols = 0

    for symbol in symbols:
        symbol_issues = 0
        payload = cache.get_quote_payload(symbol)
        current_price = cache.get_current_price(symbol)
        if current_price is None:
            summary["missingPriceCount"] += 1
            symbol_issues += 1
            _add_issue(summary, "missing_price", symbol, "观察池缺少 current price")
        if cache.get_price_status(symbol) == "stale_quote":
            summary["stalePriceCount"] += 1
            symbol_issues += 1
            _add_issue(summary, "stale_quote", symbol, "quote_snapshots 已过期")
        history_status = cache.get_history_status(symbol)
        if history_status == "missing":
            summary["missingHistoryCount"] += 1
            symbol_issues += 1
            _add_issue(summary, "missing_history", symbol, "price_history 缺失")
        elif history_status == "stale_history":
            summary["staleHistoryCount"] += 1
            symbol_issues += 1
            _add_issue(summary, "stale_history", symbol, "price_history 已过期")
        if not _can_generate_final_decision(cache, symbol, payload):
            summary["finalDecisionErrorCount"] += 1
            symbol_issues += 1
            _add_issue(summary, "final_decision_error", symbol, "finalDecision 无法用本地数据生成")
        if symbol_issues == 0:
            healthy_symbols += 1

    summary["healthyCount"] = healthy_symbols
    summary["portfolioMissingPriceCount"] = _portfolio_missing_price_count(path, cache)
    if summary["portfolioMissingPriceCount"]:
        _add_issue(summary, "portfolio_missing_price", None, "组合持仓存在缺价格标的")
    summary["outcomeMissingCount"] = _outcome_missing_count(path)
    if summary["outcomeMissingCount"]:
        _add_issue(summary, "outcome_missing", None, "decision_outcomes 存在 missing")
    summary["topIssues"] = summary["topIssues"][:10]
    return summary


def _empty_summary() -> dict[str, Any]:
    return {
        "cacheExists": False,
        "healthyCount": 0,
        "stalePriceCount": 0,
        "staleHistoryCount": 0,
        "missingPriceCount": 0,
        "missingHistoryCount": 0,
        "finalDecisionErrorCount": 0,
        "portfolioMissingPriceCount": 0,
        "outcomeMissingCount": 0,
        "topIssues": [],
    }


def _portfolio_missing_price_count(path: Path, cache: CacheReadModel) -> int:
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "portfolio_positions"):
            return 0
        rows = conn.execute(
            """
            SELECT symbol
            FROM portfolio_positions
            WHERE is_active = 1
            """
        ).fetchall()
    missing = 0
    for row in rows:
        symbol = _normalize_symbol(row[0])
        if cache.get_current_price(symbol) is None:
            missing += 1
    return missing


def _outcome_missing_count(path: Path) -> int:
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "decision_outcomes"):
            return 0
        row = conn.execute("SELECT COUNT(*) FROM decision_outcomes WHERE status = 'missing'").fetchone()
    return int(row[0] or 0) if row else 0


def _can_generate_final_decision(cache: CacheReadModel, symbol: str, payload: dict | None) -> bool:
    if not payload:
        return False
    history = cache.get_price_history(symbol)
    if history.empty:
        return False
    try:
        technicals = latest_technical_snapshot(add_technical_indicators(history))
        score = calculate_total_score(payload, technicals)
        stock_data = {**payload, **technicals}
        price = _first_number(stock_data.get("price"), stock_data.get("current_price"), stock_data.get("currentPrice"))
        if price is not None:
            stock_data["price"] = price
            stock_data.setdefault("current_price", price)
        zone = generate_buy_zone(symbol, stock_data, score, getattr(score, "scoring_model", None))
        bundle = build_final_decision_bundle(score, zone, symbol=symbol)
    except Exception:
        return False
    return bool(getattr(bundle, "finalAction", None))


def _first_number(*values: object) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None

def _add_issue(summary: dict[str, Any], category: str, symbol: str | None, message: str) -> None:
    summary["topIssues"].append({"category": category, "symbol": symbol, "message": message})


def _normalize_symbols(symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        clean = _normalize_symbol(symbol)
        if clean and clean not in seen:
            normalized.append(clean)
            seen.add(clean)
    return normalized


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)
