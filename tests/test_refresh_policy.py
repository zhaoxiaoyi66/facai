from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from data.fundamentals import FundamentalCache
from data.refresh_policy import RefreshMode, refresh_symbols_by_mode, should_refresh_fundamentals


class FakeRefreshProvider:
    def __init__(self, *, fail_quote: set[str] | None = None) -> None:
        self.fail_quote = fail_quote or set()
        self.calls: list[tuple[str, str, bool]] = []
        self.fundamental_calls = 0

    def get_quote(self, symbol: str, force_refresh: bool = False) -> dict:
        self.calls.append(("quote", symbol, force_refresh))
        self.fundamental_calls += 1
        if symbol in self.fail_quote:
            raise RuntimeError(f"{symbol} quote unavailable")
        return {"ticker": symbol, "current_price": 100.0, "market_cap": 1_000_000}

    def get_price_history(self, symbol: str, force_refresh: bool = False) -> pd.DataFrame:
        self.calls.append(("history", symbol, force_refresh))
        return pd.DataFrame([{"date": "2026-06-10", "close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0, "volume": 10}])


class FakeFmpQuoteOnlyProvider(FakeRefreshProvider):
    def _get_json(self, endpoint: str, params: dict, timeout_seconds: int = 20, retries: int = 2, force_refresh: bool = False):
        self.calls.append((endpoint, str(params.get("symbol")), force_refresh))
        symbols = str(params.get("symbol") or "").split(",")
        return [
            {
                "symbol": symbol,
                "price": 123.4,
                "changesPercentage": 1.2,
                "volume": 123456,
                "marketCap": 9_000_000,
                "yearHigh": 150,
                "yearLow": 90,
            }
            for symbol in symbols
            if symbol not in self.fail_quote
        ]


def test_price_only_updates_quote_cache_without_fundamentals(tmp_path) -> None:
    path = tmp_path / "refresh.sqlite"
    cache = FundamentalCache(path)
    cache.set_snapshot(
        "NVDA",
        {
            "ticker": "NVDA",
            "current_price": 100.0,
            "total_revenue": 10,
            "forward_pe": 30,
        },
    )
    provider = FakeFmpQuoteOnlyProvider()

    result = refresh_symbols_by_mode(
        ["NVDA"],
        RefreshMode.PRICE_ONLY,
        provider=provider,
        cache=cache,
        now=datetime(2026, 6, 11, 12, tzinfo=timezone.utc),
    )

    updated = cache.get_snapshot("NVDA", max_age_hours=24 * 3650)
    assert result["status"] == "success"
    assert result["refreshed_count"] == 1
    assert provider.fundamental_calls == 0
    assert provider.calls == [("quote", "NVDA", True)]
    assert updated is not None
    assert updated["current_price"] == 123.4
    assert updated["price_change_pct"] == 1.2
    assert updated["volume"] == 123456
    assert updated["market_cap"] == 9_000_000
    assert updated["total_revenue"] == 10
    assert updated["refresh_mode"] == "PRICE_ONLY"
    assert updated["fundamental_updated_at"]


def test_price_only_single_ticker_failure_does_not_block_others(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeFmpQuoteOnlyProvider(fail_quote={"MSFT"})

    result = refresh_symbols_by_mode(["NVDA", "MSFT"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)

    assert result["status"] == "partial"
    assert result["refreshed_count"] == 1
    assert result["failed_count"] == 1
    assert cache.get_snapshot("NVDA", max_age_hours=24 * 3650)["current_price"] == 123.4
    assert cache.get_snapshot("MSFT", max_age_hours=24 * 3650) is None


def test_daily_technical_refresh_does_not_call_fundamentals(tmp_path) -> None:
    provider = FakeRefreshProvider()

    result = refresh_symbols_by_mode(
        ["NVDA"],
        RefreshMode.DAILY_TECHNICAL,
        provider=provider,
        cache=FundamentalCache(tmp_path / "refresh.sqlite"),
    )

    assert result["status"] == "success"
    assert provider.calls == [("history", "NVDA", True)]
    assert provider.fundamental_calls == 0


def test_should_refresh_fundamentals_detects_earnings_event() -> None:
    snapshot = {"next_earnings_date": "2026-06-12"}

    assert should_refresh_fundamentals(
        "NVDA",
        snapshot,
        now=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )


def test_fundamentals_if_event_skips_without_event(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    cache.set_snapshot("NVDA", {"ticker": "NVDA", "next_earnings_date": "2026-08-01"})
    provider = FakeRefreshProvider()

    result = refresh_symbols_by_mode(
        ["NVDA"],
        RefreshMode.FUNDAMENTALS_IF_EVENT,
        provider=provider,
        cache=cache,
        now=datetime(2026, 6, 11, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert result["skipped_count"] == 1
    assert provider.calls == []


def test_full_refresh_calls_quote_and_history(tmp_path) -> None:
    provider = FakeRefreshProvider()

    result = refresh_symbols_by_mode(
        ["NVDA"],
        RefreshMode.FULL_REFRESH,
        provider=provider,
        cache=FundamentalCache(tmp_path / "refresh.sqlite"),
    )

    assert result["status"] == "success"
    assert provider.calls == [("quote", "NVDA", True), ("history", "NVDA", True)]
    assert provider.fundamental_calls == 1
