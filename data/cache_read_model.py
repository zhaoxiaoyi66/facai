from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from data.prices import CACHE_PATH
from data.price_history_selection import select_latest_history_key


class CacheReadModel:
    def __init__(
        self,
        path: Path = CACHE_PATH,
        *,
        now: datetime | None = None,
        quote_max_age_hours: float | None = None,
        history_max_age_hours: float | None = None,
    ) -> None:
        self.path = path
        self.now = now
        self.quote_max_age_hours = quote_max_age_hours
        self.history_max_age_hours = history_max_age_hours

    def cache_exists(self) -> bool:
        return self.path.exists()

    def get_current_price(self, symbol: str) -> float | None:
        quote_price = self._quote_current_price(symbol)
        if quote_price is not None:
            return quote_price
        return self.get_latest_close(symbol)

    def get_latest_close(self, symbol: str) -> float | None:
        latest = self._latest_history_snapshot(symbol)
        return _number(latest.get("close")) if latest else None

    def get_price_status(self, symbol: str) -> str:
        quote = self.get_quote_snapshot(symbol)
        if quote and self._is_stale(quote.get("fetched_at")):
            return "stale_quote"
        if quote and _current_price(quote["payload"]) is not None:
            return "quote_snapshot"
        if self.get_latest_close(symbol) is not None:
            return "price_history"
        return "missing"

    def get_history_status(self, symbol: str) -> str:
        latest = self._latest_history_snapshot(symbol)
        if latest is None or _number(latest.get("close")) is None:
            return "missing"
        if self.history_max_age_hours is not None and self._is_stale(latest.get("fetched_at"), self.history_max_age_hours):
            return "stale_history"
        return "available"

    def get_quote_payload(self, symbol: str) -> dict[str, Any] | None:
        snapshot = self.get_quote_snapshot(symbol)
        return dict(snapshot["payload"]) if snapshot else None

    def get_quote_snapshot(self, symbol: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path)) as conn:
            if not _table_exists(conn, "quote_snapshots"):
                return None
            row = conn.execute(
                "SELECT payload_json, fetched_at FROM quote_snapshots WHERE ticker = ?",
                (_normalize_symbol(symbol),),
            ).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row[0] or "{}")
        except json.JSONDecodeError:
            payload = {}
        return {
            "payload": payload if isinstance(payload, dict) else {},
            "fetched_at": row[1],
        }

    def get_price_history(self, symbol: str) -> pd.DataFrame:
        if not self.path.exists():
            return _empty_history_frame()
        with closing(sqlite3.connect(self.path)) as conn:
            if not _table_exists(conn, "price_history"):
                return _empty_history_frame()
            history_key = self._select_history_key(conn, symbol)
            if history_key is None:
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

    def _quote_current_price(self, symbol: str) -> float | None:
        payload = self.get_quote_payload(symbol)
        return _current_price(payload or {})

    def _latest_history_snapshot(self, symbol: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path)) as conn:
            if not _table_exists(conn, "price_history"):
                return None
            history_key = self._select_history_key(conn, symbol)
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
        return {"close": row[0], "fetched_at": row[1], "ticker": row[2], "date": row[3]} if row else None

    def _select_history_key(self, conn: sqlite3.Connection, symbol: str) -> str | None:
        rows = conn.execute(
            """
            SELECT ticker, MAX(fetched_at) AS latest_fetch, MAX(date) AS latest_date
            FROM price_history
            WHERE ticker IN (?, ?)
              AND close IS NOT NULL
            GROUP BY ticker
            """,
            _history_keys(symbol),
        ).fetchall()
        if not rows:
            return None
        plain_key = _normalize_symbol(symbol)
        return select_latest_history_key(rows, plain_key)

    def _is_stale(self, fetched_at: object, max_age_hours: float | None = None) -> bool:
        ttl_hours = self.quote_max_age_hours if max_age_hours is None else max_age_hours
        if ttl_hours is None:
            return False
        if not fetched_at:
            return True
        try:
            fetched = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
        except ValueError:
            return True
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        current_time = self.now or datetime.now(timezone.utc)
        return (
            current_time.astimezone(timezone.utc) - fetched.astimezone(timezone.utc)
        ).total_seconds() > ttl_hours * 3600


def _current_price(payload: dict[str, Any]) -> float | None:
    return _first_number(
        payload.get("current_price"),
        payload.get("currentPrice"),
        payload.get("price"),
        payload.get("regularMarketPrice"),
    )


def _history_keys(symbol: object) -> tuple[str, str]:
    normalized = _normalize_symbol(symbol)
    return normalized, f"FMP:{normalized}"


def _history_key_placeholders() -> str:
    return "?, ?"


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


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return bool(row)


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _empty_history_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
