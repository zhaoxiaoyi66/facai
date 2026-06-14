from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter, sleep

import pandas as pd
import pytest

from data.fundamentals import FundamentalCache
from data.refresh_policy import (
    RefreshMode,
    _reset_quote_provider_capabilities,
    refresh_symbols_by_mode,
    should_refresh_fundamentals,
    should_refresh_technicals,
    summarize_refresh_result,
)


@pytest.fixture(autouse=True)
def reset_quote_capabilities() -> None:
    _reset_quote_provider_capabilities()


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
        symbol_text = str(params.get("symbol") or params.get("symbols") or "")
        self.calls.append((endpoint, symbol_text, force_refresh))
        if endpoint == "batch-quote":
            raise RuntimeError("FMP 402 batch quote unavailable")
        symbols = symbol_text.split(",")
        return [
            {
                "symbol": symbol,
                "price": 123.4,
                "change": 1.4613,
                "volume": 123456,
                "marketCap": 9_000_000,
                "yearHigh": 150,
                "yearLow": 90,
            }
            for symbol in symbols
            if symbol not in self.fail_quote
        ]


class FakeBatchEmptyQuoteProvider(FakeFmpQuoteOnlyProvider):
    def _get_json(self, endpoint: str, params: dict, timeout_seconds: int = 20, retries: int = 2, force_refresh: bool = False):
        symbol_text = str(params.get("symbol") or params.get("symbols") or "")
        self.calls.append((endpoint, symbol_text, force_refresh))
        if endpoint == "batch-quote":
            raise RuntimeError("FMP 402 batch quote unavailable")
        if endpoint == "quote" and "," in symbol_text:
            return []
        if symbol_text in self.fail_quote:
            return []
        return [
            {
                "symbol": symbol_text,
                "price": 88.8,
                "changePercentage": 0.7,
                "volume": 111,
                "marketCap": 222,
            }
        ]


class FakeWrappedBatchQuoteProvider(FakeRefreshProvider):
    def _get_json(self, endpoint: str, params: dict, timeout_seconds: int = 20, retries: int = 2, force_refresh: bool = False):
        symbol_text = str(params.get("symbol") or params.get("symbols") or "")
        self.calls.append((endpoint, symbol_text, force_refresh))
        symbols = symbol_text.split(",")
        if endpoint != "batch-quote":
            return []
        return {
            "data": [
                {
                    "symbol": symbol,
                    "price": 77.7,
                    "changePercentage": -1.3,
                    "volume": 321,
                    "marketCap": 654,
                }
                for symbol in symbols
                if symbol
            ]
        }


class FakeSlowSingleQuoteProvider(FakeRefreshProvider):
    def _get_json(self, endpoint: str, params: dict, timeout_seconds: int = 20, retries: int = 2, force_refresh: bool = False):
        symbol_text = str(params.get("symbol") or params.get("symbols") or "")
        self.calls.append((endpoint, symbol_text, force_refresh))
        if endpoint == "batch-quote":
            raise RuntimeError("FMP 402 batch quote unavailable")
        if endpoint == "quote" and "," in symbol_text:
            return []
        sleep(0.2)
        return [{"symbol": symbol_text, "price": 50.0, "change": 1.0, "volume": 10}]


class FakeSingleFailureProvider(FakeFmpQuoteOnlyProvider):
    def _get_json(self, endpoint: str, params: dict, timeout_seconds: int = 20, retries: int = 2, force_refresh: bool = False):
        symbol_text = str(params.get("symbol") or params.get("symbols") or "")
        self.calls.append((endpoint, symbol_text, force_refresh))
        if endpoint == "batch-quote":
            raise RuntimeError("FMP 402 batch quote unavailable")
        if endpoint == "quote" and "," in symbol_text:
            return []
        if symbol_text in self.fail_quote:
            raise RuntimeError(f"{symbol_text} unavailable")
        return [{"symbol": symbol_text, "price": 91.0, "change": 1.0, "volume": 10}]


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
    assert result["live_success_count"] == 1
    assert result["cache_fallback_count"] == 0
    assert provider.fundamental_calls == 0
    assert provider.calls == [("batch-quote", "NVDA", True), ("quote-short", "NVDA", True)]
    assert updated is not None
    assert updated["current_price"] == 123.4
    assert updated["price_change_pct"] == pytest.approx(1.2, rel=0.01)
    assert updated["volume"] == 123456
    assert updated["market_cap"] == 9_000_000
    assert updated["total_revenue"] == 10
    assert updated["refresh_mode"] == "PRICE_ONLY"
    assert updated["fundamental_updated_at"]


def test_price_only_emits_refresh_progress_events(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeFmpQuoteOnlyProvider()
    events: list[dict] = []

    result = refresh_symbols_by_mode(
        ["NVDA", "MSFT"],
        RefreshMode.PRICE_ONLY,
        provider=provider,
        cache=cache,
        progress_callback=events.append,
    )

    running_symbols = [event["symbol"] for event in events if event["status"] == "running" and event["symbol"]]
    assert result["status"] == "success"
    assert events[0]["mode"] == "PRICE_ONLY"
    assert events[0]["index"] == 0
    assert events[0]["total"] == 2
    assert running_symbols[-2:] == ["NVDA", "MSFT"]


def test_daily_technical_emits_per_ticker_refresh_progress(tmp_path) -> None:
    provider = FakeRefreshProvider()
    events: list[dict] = []

    result = refresh_symbols_by_mode(
        ["NVDA", "MSFT"],
        RefreshMode.DAILY_TECHNICAL,
        provider=provider,
        cache=FundamentalCache(tmp_path / "refresh.sqlite"),
        progress_callback=events.append,
    )

    completed = [event for event in events if event["status"] == "success"]
    assert result["status"] == "success"
    assert [(event["symbol"], event["index"], event["total"]) for event in completed] == [
        ("NVDA", 1, 2),
        ("MSFT", 2, 2),
    ]


def test_price_only_falls_back_to_single_quote_when_batch_returns_empty(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeBatchEmptyQuoteProvider()

    result = refresh_symbols_by_mode(["NOW", "ADBE"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)

    now_snapshot = cache.get_snapshot("NOW", max_age_hours=24 * 3650)
    adbe_snapshot = cache.get_snapshot("ADBE", max_age_hours=24 * 3650)
    assert result["status"] == "success"
    assert result["refreshed_count"] == 2
    assert result["live_success_count"] == 2
    assert provider.fundamental_calls == 0
    assert ("batch-quote", "NOW,ADBE", True) in provider.calls
    assert ("quote", "NOW,ADBE", True) in provider.calls
    assert ("quote-short", "NOW", True) in provider.calls
    assert ("quote-short", "ADBE", True) in provider.calls
    assert now_snapshot is not None
    assert now_snapshot["current_price"] == 88.8
    assert now_snapshot["price_change_pct"] == 0.7
    assert adbe_snapshot is not None
    assert adbe_snapshot["current_price"] == 88.8


def test_price_only_disables_batch_quote_after_402(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeBatchEmptyQuoteProvider()

    first = refresh_symbols_by_mode(["NOW", "ADBE"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)
    provider.calls.clear()
    second = refresh_symbols_by_mode(["NOW", "ADBE"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)

    assert first["status"] == "success"
    assert second["status"] == "success"
    assert not any(call[0] == "batch-quote" for call in provider.calls)
    assert any("batch quote disabled" in note for note in second["provider_notes"])


def test_price_only_disables_empty_multi_symbol_quote(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeBatchEmptyQuoteProvider()

    refresh_symbols_by_mode(["NOW", "ADBE"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)
    provider.calls.clear()
    refresh_symbols_by_mode(["NOW", "ADBE"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)

    assert not any(call[0] == "quote" and call[1] == "NOW,ADBE" for call in provider.calls)


def test_price_only_single_quote_fallback_runs_concurrently(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeSlowSingleQuoteProvider()
    symbols = ["A", "B", "C", "D"]

    started = perf_counter()
    result = refresh_symbols_by_mode(symbols, RefreshMode.PRICE_ONLY, provider=provider, cache=cache)
    duration = perf_counter() - started

    assert result["status"] == "success"
    assert result["live_success_count"] == 4
    assert duration < 0.65
    assert all(("quote-short", symbol, True) in provider.calls for symbol in symbols)


def test_price_only_uses_cache_fallback_for_failed_single_quote(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    cache.set_snapshot("MSFT", {"ticker": "MSFT", "current_price": 300.0, "quote_updated_at": "2026-06-01T00:00:00+00:00"})
    provider = FakeSingleFailureProvider(fail_quote={"MSFT"})

    result = refresh_symbols_by_mode(["NVDA", "MSFT"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)

    assert result["status"] == "success"
    assert result["live_success_count"] == 1
    assert result["cache_fallback_count"] == 1
    msft = cache.get_snapshot("MSFT", max_age_hours=24 * 3650)
    assert msft["current_price"] == 300.0
    assert msft["quote_updated_at"] == "2026-06-01T00:00:00+00:00"


def test_price_only_accepts_wrapped_batch_quote_payload(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    provider = FakeWrappedBatchQuoteProvider()

    result = refresh_symbols_by_mode(["NOW", "ADBE"], RefreshMode.PRICE_ONLY, provider=provider, cache=cache)

    assert result["status"] == "success"
    assert result["refreshed_count"] == 2
    assert provider.calls == [("batch-quote", "NOW,ADBE", True)]
    assert cache.get_snapshot("NOW", max_age_hours=24 * 3650)["current_price"] == 77.7
    assert cache.get_snapshot("NOW", max_age_hours=24 * 3650)["price_change_pct"] == -1.3


def test_price_only_accepts_market_cap_aliases(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    now = datetime(2026, 6, 11, 12, tzinfo=timezone.utc)

    for alias in ("marketCap", "market_cap", "mktCap", "company_market_cap"):
        cache.set_snapshot("GLW", {"ticker": "GLW", "current_price": 10})
        quote = {"symbol": "GLW", "price": 11.0, alias: 123_000_000}
        merged = __import__("data.refresh_policy", fromlist=[""])._merge_quote_snapshot(
            "GLW",
            cache.get_snapshot("GLW", max_age_hours=24 * 3650) or {},
            quote,
            previous_fetched_at=None,
            now=now,
        )
        assert merged["market_cap"] == 123_000_000


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
        now=datetime(2026, 6, 11, 12, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert provider.calls == [("history", "NVDA", True)]
    assert provider.fundamental_calls == 0


def test_daily_technical_refresh_skips_fresh_technical_cache(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    cache.set_snapshot(
        "NVDA",
        {
            "ticker": "NVDA",
            "current_price": 100,
            "technical_updated_at": datetime(2026, 6, 11, 6, tzinfo=timezone.utc).isoformat(),
        },
    )
    provider = FakeRefreshProvider()

    result = refresh_symbols_by_mode(
        ["NVDA"],
        RefreshMode.DAILY_TECHNICAL,
        provider=provider,
        cache=cache,
        now=datetime(2026, 6, 11, 12, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert result["skipped_count"] == 1
    assert result["refreshed_count"] == 0
    assert result["ticker_results"][0]["status"] == "skipped"
    assert provider.calls == []


def test_daily_technical_refresh_marks_snapshot_timestamp_after_success(tmp_path) -> None:
    cache = FundamentalCache(tmp_path / "refresh.sqlite")
    cache.set_snapshot("NVDA", {"ticker": "NVDA", "current_price": 100})
    provider = FakeRefreshProvider()
    now = datetime(2026, 6, 11, 12, tzinfo=timezone.utc)

    result = refresh_symbols_by_mode(
        ["NVDA"],
        RefreshMode.DAILY_TECHNICAL,
        provider=provider,
        cache=cache,
        now=now,
    )

    updated = cache.get_snapshot("NVDA", max_age_hours=24 * 3650)
    assert result["status"] == "success"
    assert updated["current_price"] == 100
    assert updated["technical_updated_at"] == now.isoformat()
    assert updated["history_updated_at"] == now.isoformat()
    assert updated["price_history_updated_at"] == now.isoformat()
    assert updated["refresh_mode"] == "DAILY_TECHNICAL"


def test_should_refresh_technicals_detects_stale_or_missing_timestamp() -> None:
    now = datetime(2026, 6, 11, 12, tzinfo=timezone.utc)

    assert should_refresh_technicals({}, now=now)
    assert should_refresh_technicals(
        {"technical_updated_at": datetime(2026, 6, 9, 12, tzinfo=timezone.utc).isoformat()},
        now=now,
    )
    assert not should_refresh_technicals(
        {"technical_updated_at": datetime(2026, 6, 11, 6, tzinfo=timezone.utc).isoformat()},
        now=now,
    )


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


def test_macro_only_refresh_calls_macro_refresher_without_stock_provider(tmp_path) -> None:
    provider = FakeRefreshProvider()
    macro_calls: list[str] = []

    def macro_refresher() -> dict:
        macro_calls.append("macro")
        return {"status": "partial", "duration_seconds": 0.4, "indicators": {"vix": {"status": "success", "value": 19.4}}}

    result = refresh_symbols_by_mode(
        ["NVDA", "MSFT"],
        RefreshMode.MACRO_ONLY,
        provider=provider,
        cache=FundamentalCache(tmp_path / "refresh.sqlite"),
        macro_refresher=macro_refresher,
    )

    assert result["mode"] == "MACRO_ONLY"
    assert result["status"] == "partial"
    assert result["refreshed_count"] == 1
    assert result["ticker_results"] == []
    assert result["macro_result"]["indicators"]["vix"]["value"] == 19.4
    assert macro_calls == ["macro"]
    assert provider.calls == []
    assert provider.fundamental_calls == 0


def test_macro_only_refresh_uses_unified_official_refresh_by_default(monkeypatch, tmp_path) -> None:
    from data.macro_regime import MACRO_FORCE_OFFICIAL_REFRESH
    import data.refresh_policy as refresh_policy

    calls: list[str] = []

    def fake_refresh_macro_indicators(*, mode: str):
        calls.append(mode)
        return {"status": "partial", "duration_seconds": 0.1, "indicators": {"hy_oas": {"status": "failed"}}}

    monkeypatch.setattr(refresh_policy, "refresh_macro_indicators", fake_refresh_macro_indicators)

    result = refresh_symbols_by_mode(
        [],
        RefreshMode.MACRO_ONLY,
        cache=FundamentalCache(tmp_path / "refresh.sqlite"),
    )

    assert result["status"] == "partial"
    assert calls == [MACRO_FORCE_OFFICIAL_REFRESH]


def test_summarize_refresh_result_uses_mode_specific_label() -> None:
    assert summarize_refresh_result(
        RefreshMode.PRICE_ONLY,
        refreshed_count=31,
        skipped_count=0,
        failed_count=2,
        duration_seconds=2.1,
    ) == "更新价格完成：31只成功，0只跳过，2只失败，用时 2.1s"
