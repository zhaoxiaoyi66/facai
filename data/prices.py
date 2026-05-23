from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from settings import PROJECT_ROOT


CACHE_PATH = PROJECT_ROOT / "data" / "cache.sqlite"


class PriceCache:
    """SQLite price cache.

    Provider implementations use this cache. UI code should use a
    MarketDataProvider instead of this cache directly.
    """

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_history (
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    fetched_at TEXT NOT NULL,
                    PRIMARY KEY (ticker, date)
                )
                """
            )

    def get_history(self, ticker: str, max_age_hours: float = 12, min_rows: int = 100) -> pd.DataFrame | None:
        with self.connect() as conn:
            fetched_row = conn.execute(
                "SELECT MAX(fetched_at) FROM price_history WHERE ticker = ?",
                (ticker.upper(),),
            ).fetchone()
            fetched_at = fetched_row[0] if fetched_row else None
            if not fetched_at or not _is_fresh(fetched_at, max_age_hours):
                return None

            df = pd.read_sql_query(
                """
                SELECT date, open, high, low, close, volume
                FROM price_history
                WHERE ticker = ?
                ORDER BY date
                """,
                conn,
                params=(ticker.upper(),),
            )

        if len(df) < min_rows:
            return None
        df["date"] = pd.to_datetime(df["date"])
        return df

    def set_history(self, ticker: str, history: pd.DataFrame) -> None:
        if history.empty:
            return

        fetched_at = datetime.now(timezone.utc).isoformat()
        records = []
        for row in history.itertuples(index=False):
            records.append(
                (
                    ticker.upper(),
                    pd.to_datetime(row.date).date().isoformat(),
                    _clean_number(row.open),
                    _clean_number(row.high),
                    _clean_number(row.low),
                    _clean_number(row.close),
                    _clean_number(row.volume),
                    fetched_at,
                )
            )

        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO price_history (ticker, date, open, high, low, close, volume, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    fetched_at = excluded.fetched_at
                """,
                records,
            )


def get_price_history(
    ticker: str,
    period: str = "2y",
    force_refresh: bool = False,
    cache: PriceCache | None = None,
) -> pd.DataFrame:
    from data.providers import get_market_data_provider

    provider = get_market_data_provider()
    return provider.get_price_history(ticker, period=period, force_refresh=force_refresh)


def normalize_price_history(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    df = raw.reset_index()
    rename_map = {
        "Date": "date",
        "Datetime": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename_map)
    keep = ["date", "open", "high", "low", "close", "volume"]
    df = df[[column for column in keep if column in df.columns]].copy()
    for column in ["open", "high", "low", "close", "volume"]:
        if column not in df.columns:
            df[column] = None
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df[keep].dropna(subset=["date", "close"])


def _is_fresh(fetched_at: str, max_age_hours: float) -> bool:
    fetched_dt = datetime.fromisoformat(fetched_at)
    if fetched_dt.tzinfo is None:
        fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - fetched_dt <= timedelta(hours=max_age_hours)


def _clean_number(value: object) -> float | None:
    if pd.isna(value):
        return None
    return float(value)
