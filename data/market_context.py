from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from data.cache_read_model import CacheReadModel
from data.prices import CACHE_PATH
from data.price_history_selection import select_latest_history_key


def build_market_context(
    symbol: str,
    path: Path = CACHE_PATH,
    *,
    now: datetime | None = None,
    quote_max_age_hours: float | None = 24,
    history_max_age_hours: float | None = 72,
) -> dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    cache = CacheReadModel(
        path,
        now=now,
        quote_max_age_hours=quote_max_age_hours,
        history_max_age_hours=history_max_age_hours,
    )
    quote = cache.get_quote_snapshot(normalized)
    quote_price = _quote_price(quote)
    quote_volume = _quote_volume(quote)
    history = _latest_history_snapshot(path, normalized)
    latest_close = _number(history.get("close")) if history else None
    price_status = cache.get_price_status(normalized)
    history_status = cache.get_history_status(normalized)

    if quote_price is not None:
        current_price = quote_price
        price_source = "quote_snapshot"
        fetched_at = quote.get("fetched_at") if quote else None
    elif latest_close is not None:
        current_price = latest_close
        price_source = "price_history"
        fetched_at = history.get("fetched_at")
    else:
        current_price = None
        price_source = "missing"
        fetched_at = None

    is_stale = price_status == "stale_quote" or history_status == "stale_history"
    warning = _warning(
        price_source=price_source,
        price_status=price_status,
        history_status=history_status,
        quote_price=quote_price,
        latest_close=latest_close,
    )
    return {
        "symbol": normalized,
        "currentPrice": current_price,
        "priceSource": price_source,
        "priceStatus": price_status,
        "quotePrice": quote_price,
        "quoteVolume": quote_volume,
        "latestClose": latest_close,
        "fetchedAt": fetched_at,
        "isStale": is_stale,
        "historyStatus": history_status,
        "historyLatestDate": history.get("date") if history else None,
        "historyTickerKey": history.get("ticker") if history else None,
        "warning": warning,
    }


def build_market_history(
    symbol: str,
    path: Path = CACHE_PATH,
    *,
    now: datetime | None = None,
    quote_max_age_hours: float | None = 24,
    history_max_age_hours: float | None = 72,
) -> pd.DataFrame:
    context = build_market_context(
        symbol,
        path=path,
        now=now,
        quote_max_age_hours=quote_max_age_hours,
        history_max_age_hours=history_max_age_hours,
    )
    history_key = str(context.get("historyTickerKey") or "").strip()
    if not history_key:
        return _empty_history_frame()
    return _history_frame_for_key(path, history_key)


def _history_frame_for_key(path: Path, history_key: str) -> pd.DataFrame:
    if not path.exists():
        return _empty_history_frame()
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "price_history"):
            return _empty_history_frame()
        columns = _table_columns(conn, "price_history")
        selected = [column for column in ("date", "open", "high", "low", "close", "volume") if column in columns]
        if "date" not in selected or "close" not in selected:
            return _empty_history_frame()
        frame = pd.read_sql_query(
            f"""
            SELECT {", ".join(selected)}
            FROM price_history
            WHERE ticker = ?
              AND close IS NOT NULL
            ORDER BY date
            """,
            conn,
            params=(history_key,),
        )
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"])
    return frame


def _latest_history_snapshot(path: Path, symbol: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as conn:
        if not _table_exists(conn, "price_history"):
            return None
        history_key = _select_history_key(conn, symbol)
        if history_key is None:
            return None
        row = conn.execute(
            """
            SELECT close, fetched_at, ticker, date
            FROM price_history
            WHERE ticker = ?
              AND close IS NOT NULL
            ORDER BY date DESC
            LIMIT 1
            """,
            (history_key,),
        ).fetchone()
    if not row:
        return None
    return {"close": row[0], "fetched_at": row[1], "ticker": row[2], "date": row[3]}


def _select_history_key(conn: sqlite3.Connection, symbol: str) -> str | None:
    rows = conn.execute(
        """
        SELECT ticker, MAX(fetched_at) AS latest_fetch, MAX(date) AS latest_date
        FROM price_history
        WHERE ticker IN (?, ?)
          AND close IS NOT NULL
        GROUP BY ticker
        """,
        (symbol, f"FMP:{symbol}"),
    ).fetchall()
    if not rows:
        return None
    return select_latest_history_key(rows, symbol)


def _quote_price(quote: dict[str, Any] | None) -> float | None:
    if not quote:
        return None
    payload = quote.get("payload") or {}
    return _first_number(
        payload.get("current_price"),
        payload.get("currentPrice"),
        payload.get("price"),
        payload.get("regularMarketPrice"),
    )


def _quote_volume(quote: dict[str, Any] | None) -> float | None:
    if not quote:
        return None
    payload = quote.get("payload") or {}
    volume = _first_number(
        payload.get("volume"),
        payload.get("regularMarketVolume"),
        payload.get("latest_volume"),
        payload.get("latestVolume"),
    )
    return volume if volume is not None and volume > 0 else None


def _warning(
    *,
    price_source: str,
    price_status: str,
    history_status: str,
    quote_price: float | None,
    latest_close: float | None,
) -> str:
    warnings: list[str] = []
    if price_status == "stale_quote":
        warnings.append("价格数据可能过期")
    if price_source == "price_history":
        warnings.append("quote 缺失，使用最新收盘价替代 current price")
    if price_source == "missing":
        warnings.append("缺少 quote 和 price_history，无法生成 current price")
    if history_status == "stale_history":
        warnings.append("price_history 可能过期")
    elif history_status == "missing" and quote_price is not None and latest_close is None:
        warnings.append("price_history 缺失")
    return "；".join(warnings)


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _empty_history_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol(symbol: object) -> str:
    return str(symbol or "").strip().upper()
