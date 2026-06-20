from __future__ import annotations

import inspect
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from data.market_context import build_market_context
import data.market_context as market_context_module


NOW = datetime(2026, 5, 30, 12, tzinfo=timezone.utc)


def _db(tmpdir: str) -> Path:
    return Path(tmpdir) / "market_context.sqlite"


def _insert_quote(path: Path, symbol: str, price: float | None, fetched_at: str) -> None:
    payload = {} if price is None else {"current_price": price}
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS quote_snapshots (
                ticker TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT OR REPLACE INTO quote_snapshots VALUES (?, ?, ?)",
            (symbol.upper(), json.dumps(payload), fetched_at),
        )
        conn.commit()


def _insert_history(path: Path, ticker: str, rows: list[tuple[str, float]], fetched_at: str) -> None:
    with closing(sqlite3.connect(path)) as conn:
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
        conn.executemany(
            """
            INSERT OR REPLACE INTO price_history (ticker, date, open, high, low, close, volume, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [(ticker.upper(), day, close, close, close, close, 1000, fetched_at) for day, close in rows],
        )
        conn.commit()


def test_quote_price_is_preferred_over_latest_close() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_quote(path, "NVDA", 130, "2026-05-30T11:00:00+00:00")
        _insert_history(path, "FMP:NVDA", [("2026-05-30", 125)], "2026-05-30T10:00:00+00:00")

        context = build_market_context("nvda", path=path, now=NOW)

        assert context["symbol"] == "NVDA"
        assert context["currentPrice"] == 130
        assert context["priceSource"] == "quote_snapshot"
        assert context["quotePrice"] == 130
        assert context["latestClose"] == 125
        assert context["fetchedAt"] == "2026-05-30T11:00:00+00:00"
        assert context["isStale"] is False
        assert context["warning"] == ""


def test_stale_quote_is_marked_without_silent_fallback() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        now = datetime(2026, 6, 16, 14, 0, tzinfo=timezone.utc)
        _insert_quote(path, "NVDA", 130, "2026-06-16T13:50:00+00:00")
        _insert_history(path, "FMP:NVDA", [("2026-06-15", 125)], "2026-06-16T10:00:00+00:00")

        context = build_market_context("NVDA", path=path, now=now, quote_max_age_hours=24)

        assert context["currentPrice"] == 130
        assert context["priceSource"] == "quote_snapshot"
        assert context["isStale"] is True
        assert context["quoteFreshnessLabel"] == "盘中价格过期"
        assert "盘中价格过期" in context["warning"]


def test_closed_market_stale_quote_is_valid_until_next_session() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        now = datetime(2026, 6, 13, 16, 0, tzinfo=timezone.utc)
        _insert_quote(path, "NVDA", 130, "2026-06-12T20:30:00+00:00")
        _insert_history(path, "FMP:NVDA", [("2026-06-12", 125)], "2026-06-12T21:00:00+00:00")

        context = build_market_context("NVDA", path=path, now=now, quote_max_age_hours=1)

        assert context["currentPrice"] == 130
        assert context["rawPriceStatus"] == "stale_quote"
        assert context["priceStatus"] == "quote_snapshot"
        assert context["quoteFreshnessLabel"] == "休市中，价格有效"
        assert context["quoteShouldPromptRefresh"] is False
        assert context["isStale"] is False
        assert "过期" not in context["warning"]


def test_missing_quote_falls_back_to_latest_close_with_warning() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_history(path, "FMP:MSFT", [("2026-05-29", 420), ("2026-05-30", 425)], "2026-05-30T10:00:00+00:00")

        context = build_market_context("MSFT", path=path, now=NOW)

        assert context["currentPrice"] == 425
        assert context["priceSource"] == "price_history"
        assert context["quotePrice"] is None
        assert context["latestClose"] == 425
        assert context["fetchedAt"] == "2026-05-30T10:00:00+00:00"
        assert "使用最新收盘价替代" in context["warning"]


def test_latest_history_chooses_newer_symbol_or_fmp_key() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_history(path, "CRWV", [("2026-05-27", 60)], "2026-05-28T10:00:00+00:00")
        _insert_history(path, "FMP:CRWV", [("2026-05-29", 70)], "2026-05-30T10:00:00+00:00")

        context = build_market_context("CRWV", path=path, now=NOW)

        assert context["currentPrice"] == 70
        assert context["latestClose"] == 70
        assert context["historyTickerKey"] == "FMP:CRWV"
        assert context["historyLatestDate"] == "2026-05-29"


def test_latest_history_prefers_newer_trade_date_over_newer_fetch_time() -> None:
    with TemporaryDirectory() as tmpdir:
        path = _db(tmpdir)
        _insert_history(path, "ORCL", [("2026-06-16", 190)], "2026-06-17T02:00:00+00:00")
        _insert_history(path, "FMP:ORCL", [("2026-06-14", 184)], "2026-06-18T12:00:00+00:00")

        context = build_market_context("ORCL", path=path, now=NOW)

        assert context["currentPrice"] == 190
        assert context["latestClose"] == 190
        assert context["historyTickerKey"] == "ORCL"
        assert context["historyLatestDate"] == "2026-06-16"


def test_missing_quote_and_history_returns_safe_missing_context() -> None:
    with TemporaryDirectory() as tmpdir:
        context = build_market_context("NBIS", path=_db(tmpdir), now=NOW)

        assert context["symbol"] == "NBIS"
        assert context["currentPrice"] is None
        assert context["priceSource"] == "missing"
        assert context["quotePrice"] is None
        assert context["latestClose"] is None
        assert context["fetchedAt"] is None
        assert context["isStale"] is False
        assert context["historyStatus"] == "missing"
        assert context["historyLatestDate"] is None
        assert context["historyTickerKey"] is None
        assert "缺少 quote 和 price_history" in context["warning"]


def test_market_context_is_cache_only_and_does_not_force_refresh() -> None:
    source = inspect.getsource(market_context_module)

    assert "get_market_data_provider" not in source
    assert "force_refresh" not in source
