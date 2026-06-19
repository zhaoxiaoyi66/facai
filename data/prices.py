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

            columns = _table_columns(conn, "price_history")
            selected = [
                column
                for column in ("date", "open", "high", "low", "close", "adjusted_close", "volume")
                if column in columns
            ]
            df = pd.read_sql_query(
                """
                SELECT {columns}
                FROM price_history
                WHERE ticker = ?
                ORDER BY date
                """.format(columns=", ".join(selected)),
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
        with self.connect() as conn:
            columns = _table_columns(conn, "price_history")

        has_adjusted_close_column = "adjusted_close" in columns and "adjusted_close" in history.columns
        records = []
        for row in history.itertuples(index=False):
            base = [
                ticker.upper(),
                pd.to_datetime(row.date).date().isoformat(),
                _clean_number(row.open),
                _clean_number(row.high),
                _clean_number(row.low),
                _clean_number(row.close),
            ]
            if has_adjusted_close_column:
                base.append(_clean_number(getattr(row, "adjusted_close", None)))
            base.extend([_clean_number(row.volume), fetched_at])
            records.append(tuple(base))

        with self.connect() as conn:
            insert_columns = ["ticker", "date", "open", "high", "low", "close"]
            placeholders = ["?", "?", "?", "?", "?", "?"]
            update_columns = ["open", "high", "low", "close"]
            if has_adjusted_close_column:
                insert_columns.append("adjusted_close")
                placeholders.append("?")
                update_columns.append("adjusted_close")
            insert_columns.extend(["volume", "fetched_at"])
            placeholders.extend(["?", "?"])
            update_columns.extend(["volume", "fetched_at"])
            update_sql = ",\n                    ".join(f"{column} = excluded.{column}" for column in update_columns)
            conn.executemany(
                f"""
                INSERT INTO price_history ({", ".join(insert_columns)})
                VALUES ({", ".join(placeholders)})
                ON CONFLICT(ticker, date) DO UPDATE SET
                    {update_sql}
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
        "Adj Close": "adjusted_close",
        "Adjusted Close": "adjusted_close",
        "adjClose": "adjusted_close",
        "adjustedClose": "adjusted_close",
        "Volume": "volume",
    }
    df = df.rename(columns=rename_map)
    keep = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]
    df = df[[column for column in keep if column in df.columns]].copy()
    for column in ["open", "high", "low", "close", "adjusted_close", "volume"]:
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


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _clean_number(value: object) -> float | None:
    if pd.isna(value):
        return None
    return float(value)
