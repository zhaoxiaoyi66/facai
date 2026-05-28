from __future__ import annotations

import unittest
from datetime import datetime, timezone

import pandas as pd

from data.market_data_refresh import refresh_symbol_market_data


class FakeMarketDataProvider:
    def __init__(self, *, quote=None, history=None, quote_error: Exception | None = None, history_error: Exception | None = None) -> None:
        self.quote = quote if quote is not None else {"ticker": "NVDA", "current_price": 212.6}
        self.history = history if history is not None else pd.DataFrame([{"date": "2026-05-27", "close": 212.6}])
        self.quote_error = quote_error
        self.history_error = history_error
        self.calls: list[tuple[str, str, bool]] = []

    def get_quote(self, symbol: str, *, force_refresh: bool = False) -> dict:
        self.calls.append(("quote", symbol, force_refresh))
        if self.quote_error:
            raise self.quote_error
        return self.quote

    def get_price_history(self, symbol: str, *, force_refresh: bool = False) -> pd.DataFrame:
        self.calls.append(("history", symbol, force_refresh))
        if self.history_error:
            raise self.history_error
        return self.history


class MarketDataRefreshTests(unittest.TestCase):
    def test_refresh_symbol_market_data_refreshes_quote_and_history(self) -> None:
        provider = FakeMarketDataProvider()

        result = refresh_symbol_market_data(
            "nvda",
            provider=provider,
            now=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(result["symbol"], "NVDA")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["quoteStatus"], "refreshed")
        self.assertEqual(result["historyStatus"], "refreshed")
        self.assertEqual(result["fetchedAt"], "2026-05-28T12:00:00+00:00")
        self.assertIsNone(result["error"])
        self.assertEqual(provider.calls, [("quote", "NVDA", True), ("history", "NVDA", True)])

    def test_refresh_symbol_market_data_returns_partial_when_one_side_fails(self) -> None:
        provider = FakeMarketDataProvider(quote_error=RuntimeError("quote unavailable"))

        result = refresh_symbol_market_data("MSFT", provider=provider)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["quoteStatus"], "failed")
        self.assertEqual(result["historyStatus"], "refreshed")
        self.assertIn("quote unavailable", result["error"])
        self.assertEqual(provider.calls, [("quote", "MSFT", True), ("history", "MSFT", True)])

    def test_refresh_symbol_market_data_returns_failed_for_empty_symbol(self) -> None:
        provider = FakeMarketDataProvider()

        result = refresh_symbol_market_data(" ", provider=provider)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["quoteStatus"], "failed")
        self.assertEqual(result["historyStatus"], "failed")
        self.assertEqual(result["error"], "symbol is required")
        self.assertEqual(provider.calls, [])


if __name__ == "__main__":
    unittest.main()
