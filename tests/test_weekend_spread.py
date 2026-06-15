from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pandas as pd

from data.afterhours_provider import AfterhoursReference, CachedAfterhoursProvider, resolve_afterhours_reference
from data.binance_provider import BinanceHTTPPriceProvider
from data.weekend_spread_backtest import (
    recent_weekend_windows,
    run_weekend_peak_short_backtest,
    summarize_backtest_results,
)
from data.weekend_spread import (
    build_mapping_diagnostics,
    build_weekend_spread_rows,
    classify_spread,
    discover_binance_symbol_candidates,
    load_binance_symbol_mapping,
    upsert_default_usdm_futures_mappings,
    upsert_local_binance_symbol_mapping,
)
from data.weekend_spread_cache import (
    annotate_cached_rows,
    has_successful_price,
    is_provider_failure,
    read_weekend_spread_snapshot,
    write_weekend_spread_failure,
    write_weekend_spread_snapshot,
)
from data.weekend_spread_log import (
    build_history_stats,
    generate_weekly_summary,
    get_weekly_log_snapshot,
    record_spread_samples,
    update_monday_outcome,
)
from scripts import smoke_binance_provider
from ui import weekend_spread


class FakeCache:
    def __init__(self, history: pd.DataFrame | None = None) -> None:
        self.history = history if history is not None else _history()

    def get_price_history(self, _ticker: str) -> pd.DataFrame:
        return self.history

    def get_quote_payload(self, ticker: str) -> dict:
        return {"companyName": f"{ticker} Inc."}


class FakeProvider:
    def __init__(
        self,
        price: float | None = 101.5,
        *,
        bid: float | None = 101.45,
        ask: float | None = 101.55,
        volume_24h: float | None = 100_000,
        funding_rate: float | None = 0.0001,
        error: str = "",
    ) -> None:
        self.price = price
        self.bid = bid
        self.ask = ask
        self.volume_24h = volume_24h
        self.funding_rate = funding_rate
        self.error = error
        self.calls: list[str] = []
        self.validation_exists = error != "invalid_symbol"
        self.candidates = [{"symbol": "NVDAUSDT", "market_type": "usdm_futures", "quote_currency": "USDT"}]

    def get_last_price(self, symbol: str, *, market_type: str = "usdm_futures", force_refresh: bool = False) -> dict:
        self.calls.append(f"{market_type}:{symbol}")
        return {
            "symbol": symbol,
            "last_price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "volume_24h": self.volume_24h,
            "funding_rate": self.funding_rate,
            "updated_at": "2026-06-14T12:00:00+00:00",
            "source": "mock_binance",
            "error": self.error,
        }

    def validate_symbol(self, symbol: str, *, market_type: str = "usdm_futures") -> dict:
        self.calls.append(f"validate:{market_type}:{symbol}")
        if self.error == "invalid_symbol":
            return {
                "symbol": symbol,
                "exists": False,
                "market_type": market_type,
                "status": "invalid_symbol",
                "error_message": "invalid_symbol",
                "updated_at": "2026-06-14T12:00:00+00:00",
            }
        return {
            "symbol": symbol,
            "exists": self.validation_exists,
            "market_type": market_type,
            "quote_currency": "USDT",
            "status": "valid",
            "base_asset": symbol.removesuffix("USDT"),
            "price_available": self.price is not None,
            "book_available": self.bid is not None and self.ask is not None,
            "volume_available": self.volume_24h is not None,
            "funding_available": market_type == "usdm_futures" and self.funding_rate is not None,
            "updated_at": "2026-06-14T12:00:00+00:00",
            "error_message": "",
        }

    def find_symbol_candidates(self, query: str, *, market_type: str = "usdm_futures", limit: int = 10) -> list[dict]:
        self.calls.append(f"candidates:{market_type}:{query}")
        matches = []
        for item in self.candidates:
            if query.upper() not in item["symbol"]:
                continue
            row = dict(item)
            row["market_type"] = market_type
            matches.append(row)
        return matches[:limit]


class FakeAfterhoursProvider:
    def __init__(
        self,
        reference_price: float | None = 102.0,
        *,
        reference_time: str = "2026-06-12T19:58:00-04:00",
        reference_source: str = "mock_afterhours",
        bid: float | None = 101.9,
        ask: float | None = 102.1,
        mid: float | None = 102.0,
        last_trade: float | None = 102.0,
        volume: float | None = 2_500,
        data_quality: str = "HIGH",
        missing_reason: str = "",
        cache_status: str = "API_LIVE",
        error: str = "",
    ) -> None:
        self.snapshot = AfterhoursReference(
            symbol="",
            reference_price=reference_price,
            reference_time=reference_time,
            reference_source=reference_source,
            bid=bid,
            ask=ask,
            mid=mid,
            last_trade=last_trade,
            volume=volume,
            data_quality=data_quality,
            error=error,
            missing_reason=missing_reason,
            cache_status=cache_status,
        )
        self.calls: list[tuple[str, str, bool]] = []

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        self.calls.append((symbol, regular_close_date, force_refresh))
        snapshot = self.snapshot
        return AfterhoursReference(
            symbol=symbol,
            reference_price=snapshot.reference_price,
            reference_time=snapshot.reference_time,
            reference_source=snapshot.reference_source,
            bid=snapshot.bid,
            ask=snapshot.ask,
            mid=snapshot.mid,
            last_trade=snapshot.last_trade,
            volume=snapshot.volume,
            data_quality=snapshot.data_quality,
            error=snapshot.error,
            missing_reason=snapshot.missing_reason,
            cache_status=snapshot.cache_status,
        )


class SequenceAfterhoursProvider:
    def __init__(self, snapshots: list[AfterhoursReference]) -> None:
        self.snapshots = list(snapshots)
        self.calls: list[tuple[str, str, bool]] = []

    def get_afterhours_reference(
        self,
        symbol: str,
        *,
        regular_close_date: str = "",
        force_refresh: bool = False,
    ) -> AfterhoursReference:
        self.calls.append((symbol, regular_close_date, force_refresh))
        if not self.snapshots:
            return AfterhoursReference(symbol=symbol, error="empty_sequence", missing_reason="FETCH_FAILED")
        snapshot = self.snapshots.pop(0)
        return AfterhoursReference(
            symbol=symbol,
            reference_price=snapshot.reference_price,
            reference_time=snapshot.reference_time,
            reference_source=snapshot.reference_source,
            bid=snapshot.bid,
            ask=snapshot.ask,
            mid=snapshot.mid,
            last_trade=snapshot.last_trade,
            volume=snapshot.volume,
            data_quality=snapshot.data_quality,
            error=snapshot.error,
            missing_reason=snapshot.missing_reason,
            cache_status=snapshot.cache_status,
        )


class FakeKlineProvider:
    def __init__(self, bars: list[list] | None = None, *, error: Exception | None = None) -> None:
        self.bars = bars or []
        self.error = error
        self.calls: list[dict] = []

    def get_klines(
        self,
        symbol: str,
        *,
        market_type: str = "usdm_futures",
        interval: str = "1m",
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        limit: int = 1000,
    ) -> list[list]:
        self.calls.append(
            {
                "symbol": symbol,
                "market_type": market_type,
                "interval": interval,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "limit": limit,
            }
        )
        if self.error:
            raise self.error
        start = start_time_ms or 0
        end = end_time_ms or 2**63 - 1
        return [bar for bar in self.bars if start <= int(bar[0]) < end][:limit]


class CandidateStatusProvider:
    def __init__(self, *, status: str = "OK", candidates: list[dict] | None = None, message: str = "") -> None:
        self.status = status
        self.candidates = candidates or []
        self.message = message
        self.calls: list[str] = []

    def find_symbol_candidates_with_status(self, query: str, *, market_type: str = "usdm_futures", limit: int = 10) -> dict:
        self.calls.append(f"status_candidates:{market_type}:{query}")
        return {
            "market_type": market_type,
            "candidates": self.candidates[:limit],
            "data_source_status": self.status,
            "error_message": self.message,
            "updated_at": "2026-06-14T12:00:00+00:00",
            "symbol_count": 0 if self.status == "EMPTY" else 1200,
            "btcusdt_found": self.status not in {"EMPTY", "UNAVAILABLE"},
            "provider_diagnostic_failed": self.status in {"EMPTY", "UNAVAILABLE"},
        }


class SpotSymbolSpecificProvider(BinanceHTTPPriceProvider):
    def __init__(self) -> None:
        super().__init__(spot_base_url="https://spot.test", exchange_info_cache_path=None)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
        self.calls.append((base_url, path, dict(params)))
        if path.endswith("exchangeInfo") and not params.get("symbol"):
            raise TimeoutError("full exchangeInfo timeout")
        if path.endswith("exchangeInfo"):
            symbol = params.get("symbol") or ""
            return {"symbols": [{"symbol": symbol, "baseAsset": symbol.removesuffix("USDT"), "quoteAsset": "USDT"}]}
        if path.endswith("ticker/price"):
            return {"price": "207"}
        if path.endswith("bookTicker"):
            return {"bidPrice": "206.8", "askPrice": "207.2"}
        if path.endswith("ticker/24hr"):
            return {"lastPrice": "207", "volume": "123456"}
        return {}


class ExchangeInfoCacheProvider(BinanceHTTPPriceProvider):
    def __init__(self, cache_path) -> None:
        super().__init__(spot_base_url="https://spot.test", exchange_info_cache_path=cache_path)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
        self.calls.append((base_url, path, dict(params)))
        return {"symbols": [{"symbol": params.get("symbol") or "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}]}


def _history(close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-06-11", "close": close - 2},
            {"date": "2026-06-12", "close": close},
            {"date": "2026-06-13", "close": close + 0.8},
        ]
    )


def _kline(moment: datetime, open_: float, high: float, low: float, close: float, volume: float = 1_000) -> list:
    return [int(moment.astimezone(timezone.utc).timestamp() * 1000), str(open_), str(high), str(low), str(close), str(volume)]


def _mapping(symbol: str = "NVDAUSDT", **overrides) -> dict:
    return {
        "NVDA": {
            "enabled": True,
            "binance_symbol": symbol,
            "market_type": "usdm_futures",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": "confirmed",
            "risk_note": "mock mapping",
            **overrides,
        }
    }


def _anchors(*, afterhours: float | None = 100.0, regular: float | None = 98.0) -> dict:
    return {"NVDA": {"afterhours_reference_price": afterhours, "regular_close_price": regular}}


def _explicit_empty_mapping() -> dict:
    return {"__NO_MAPPING__": {"enabled": False, "binance_symbol": ""}}


def test_spread_pct_and_alert_level_are_calculated_from_friday_close() -> None:
    provider = FakeProvider(price=101.5)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["friday_close"] == 100.0
    assert row["friday_close_date"] == "2026-06-12"
    assert row["close_source"] == "friday_close"
    assert row["binance_last_price"] == 101.5
    assert round(row["spread_pct"], 2) == 1.5
    assert row["alert_level"] == "FOCUS"
    assert row["spread_direction"] == "Binance 溢价"
    assert row["mapping_status"] == "映射已确认"
    assert provider.calls == ["usdm_futures:NVDAUSDT"]


def test_primary_spread_uses_afterhours_reference_when_available() -> None:
    provider = FakeProvider(price=210, bid=209.8, ask=210.2)
    afterhours_provider = FakeAfterhoursProvider(reference_price=208, last_trade=208)

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=provider,
        afterhours_provider=afterhours_provider,
        cache=FakeCache(_history(close=205)),
    )

    row = rows[0]
    assert row["regular_close_price"] == 205
    assert row["regular_close_date"] == "2026-06-12"
    assert row["afterhours_reference_price"] == 208
    assert round(row["afterhours_gap_pct"], 2) == 1.46
    assert round(row["spread_vs_regular_close_pct"], 2) == 2.44
    assert round(row["spread_vs_afterhours_pct"], 2) == 0.96
    assert round(row["primary_spread_pct"], 2) == 0.96
    assert round(row["spread_pct"], 2) == 0.96
    assert row["primary_spread_anchor"] == "AFTERHOURS_REFERENCE"
    assert row["afterhours_missing_reason"] == ""
    assert row["afterhours_cache_status"] == "API_LIVE"
    assert afterhours_provider.calls == [("NVDA", "2026-06-12", False)]


def test_primary_spread_falls_back_to_regular_close_when_afterhours_missing() -> None:
    provider = FakeProvider(price=210, bid=209.8, ask=210.2)
    afterhours_provider = FakeAfterhoursProvider(reference_price=None, data_quality="MISSING")

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=provider,
        afterhours_provider=afterhours_provider,
        cache=FakeCache(_history(close=205)),
    )

    row = rows[0]
    assert row["afterhours_reference_price"] is None
    assert row["afterhours_missing_reason"] == "NO_AFTERHOURS_TRADE"
    assert row["spread_vs_afterhours_pct"] is None
    assert round(row["spread_vs_regular_close_pct"], 2) == 2.44
    assert round(row["primary_spread_pct"], 2) == 2.44
    assert row["primary_spread_anchor"] == "REGULAR_CLOSE_FALLBACK"


def test_afterhours_quote_spread_wide_downgrades_quality() -> None:
    snapshot = resolve_afterhours_reference(
        "NVDA",
        trade={},
        quote={"bid": 100, "ask": 104, "timestamp": "2026-06-12T19:58:00-04:00", "volume": 5000},
        regular_close_date="2026-06-12",
    )

    assert snapshot.reference_price == 102
    assert snapshot.reference_source == "FMP_AFTERHOURS_QUOTE_MID"
    assert snapshot.data_quality == "LOW"


def test_weekend_spread_uses_afterhours_quote_mid_when_trade_missing() -> None:
    snapshot = resolve_afterhours_reference(
        "NVDA",
        trade={},
        quote={"bid": 207.8, "ask": 208.2, "timestamp": "2026-06-12T19:58:00-04:00", "volume": 1200},
        regular_close_date="2026-06-12",
    )
    afterhours_provider = FakeAfterhoursProvider(
        reference_price=snapshot.reference_price,
        reference_time=snapshot.reference_time,
        reference_source=snapshot.reference_source,
        bid=snapshot.bid,
        ask=snapshot.ask,
        mid=snapshot.mid,
        last_trade=snapshot.last_trade,
        volume=snapshot.volume,
        data_quality=snapshot.data_quality,
    )

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=210),
        afterhours_provider=afterhours_provider,
        cache=FakeCache(_history(close=205)),
    )

    assert rows[0]["afterhours_reference_price"] == 208.0
    assert rows[0]["afterhours_reference_source"] == "FMP_AFTERHOURS_QUOTE_MID"
    assert round(rows[0]["spread_vs_afterhours_pct"], 2) == 0.96


def test_afterhours_provider_missing_does_not_crash() -> None:
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=210),
        afterhours_provider=FakeAfterhoursProvider(reference_price=None, data_quality="MISSING"),
        cache=FakeCache(_history(close=205)),
    )

    assert rows[0]["status"] == "OK"
    assert rows[0]["afterhours_data_quality"] == "MISSING"
    assert rows[0]["afterhours_missing_reason"] == "NO_AFTERHOURS_TRADE"
    assert rows[0]["primary_spread_anchor"] == "REGULAR_CLOSE_FALLBACK"


def test_afterhours_provider_failure_uses_cached_reference(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    provider = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=208,
                reference_time="2026-06-12T19:58:00-04:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                last_trade=208,
                data_quality="HIGH",
            ),
            AfterhoursReference(symbol="NVDA", data_quality="MISSING", error="timeout", missing_reason="FETCH_FAILED"),
        ]
    )
    cached_provider = CachedAfterhoursProvider(provider, cache_path=cache_path)

    first = cached_provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12", force_refresh=False)
    second = cached_provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12", force_refresh=True)

    assert first.reference_price == 208
    assert first.cache_status == "API_LIVE"
    assert second.reference_price == 208
    assert second.cache_status == "CACHE_FALLBACK"
    assert second.error == "timeout"


def test_afterhours_cache_only_provider_reads_cached_reference_without_fetch(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    live_provider = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=208,
                reference_time="2026-06-12T19:58:00-04:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                data_quality="HIGH",
            )
        ]
    )
    CachedAfterhoursProvider(live_provider, cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )

    cache_only = CachedAfterhoursProvider(SequenceAfterhoursProvider([]), cache_path=cache_path)
    cached = cache_only.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert cached.reference_price == 208
    assert cached.cache_status == "CACHE_HIT"


def test_adbe_mock_focus_sample_keeps_observation_risk_notice() -> None:
    provider = FakeProvider(price=207, bid=206.8, ask=207.2)
    rows = build_weekend_spread_rows(
        ["ADBE"],
        mapping={
            "ADBE": {
                "enabled": True,
                "binance_symbol": "ADBEUSDT",
                "market_type": "usdm_futures",
                "quote_currency": "USDT",
                "unit_multiplier": 1,
                "mapping_confidence": "manual_required",
                "risk_note": "需人工确认该 symbol 是否真实映射 ADBE",
            }
        },
        provider=provider,
        cache=FakeCache(_history(close=204)),
    )

    row = rows[0]
    assert round(row["spread_pct"], 2) == 1.47
    assert row["alert_level_cn"] == "重点关注"
    assert row["mapping_status"] == "需人工确认映射"
    assert "不构成套利建议" in row["mapping_risk"]


def test_unit_multiplier_adjusts_binance_price() -> None:
    provider = FakeProvider(price=2_070)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(unit_multiplier=10),
        provider=provider,
        cache=FakeCache(_history(close=204)),
    )

    row = rows[0]
    assert row["adjusted_binance_price"] == 207
    assert round(row["spread_pct"], 2) == 1.47


def test_alert_level_thresholds() -> None:
    assert classify_spread(0.49)["level"] == "IGNORE"
    assert classify_spread(0.8)["level"] == "OBSERVE"
    assert classify_spread(1.5)["level"] == "FOCUS"
    assert classify_spread(2.1)["level"] == "ABNORMAL"
    assert classify_spread(None)["level"] == "DATA_INSUFFICIENT"


def test_missing_symbol_mapping_does_not_call_provider() -> None:
    provider = FakeProvider(price=101.5)
    rows = build_weekend_spread_rows(["MSFT"], mapping={}, provider=provider, cache=FakeCache())

    assert rows[0]["status"] == "NO_MAPPING"
    assert rows[0]["alert_level_cn"] == "暂无映射"
    assert rows[0]["mapping_status"] == "暂无映射"
    assert provider.calls == []


def test_build_weekend_spread_rows_reports_refresh_progress() -> None:
    events: list[tuple[int, int, str]] = []

    rows = build_weekend_spread_rows(
        ["NVDA", "MSFT"],
        mapping=_mapping(),
        provider=FakeProvider(),
        cache=FakeCache(),
        force_refresh=True,
        progress_callback=lambda completed, total, ticker: events.append((completed, total, ticker)),
    )

    assert [row["ticker"] for row in rows] == ["NVDA", "MSFT"]
    assert events == [(1, 2, "NVDA"), (2, 2, "MSFT")]


def test_mapping_diagnostics_reports_missing_mapping_without_price_request() -> None:
    provider = FakeProvider()

    rows = build_mapping_diagnostics(["MSFT"], mapping={}, provider=provider, validate=True, include_candidates=False)

    assert rows[0]["validation_status"] == "暂无映射"
    assert rows[0]["configured_symbol"] == ""
    assert provider.calls == []


def test_explicit_empty_mapping_does_not_fallback_to_local_mapping() -> None:
    provider = FakeProvider()

    rows = build_weekend_spread_rows(["NVDA"], mapping={}, provider=provider, cache=FakeCache())
    diagnostics = build_mapping_diagnostics(["NVDA"], mapping={}, provider=provider, validate=True, include_candidates=False)

    assert rows[0]["status"] == "NO_MAPPING"
    assert diagnostics[0]["validation_status"] == "暂无映射"
    assert provider.calls == []


def test_unconfirmed_unit_or_currency_does_not_calculate_formal_spread() -> None:
    provider = FakeProvider(price=101.5)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(quote_currency="BTC"),
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["status"] == "UNIT_UNCONFIRMED"
    assert row["spread_pct"] is None
    assert row["mapping_status"] == "需确认映射单位"
    assert provider.calls == []


def test_binance_data_failure_is_unavailable_not_fake_price() -> None:
    provider = FakeProvider(price=None, error="timeout")
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=provider,
        cache=FakeCache(),
    )

    assert rows[0]["status"] == "BINANCE_UNAVAILABLE"
    assert rows[0]["binance_last_price"] is None
    assert rows[0]["spread_pct"] is None
    assert rows[0]["alert_level_cn"] == "Binance 数据不可用"


def test_invalid_binance_symbol_is_mapping_review_not_generic_api_failure() -> None:
    provider = FakeProvider(price=None, error="invalid_symbol")
    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=provider, cache=FakeCache())

    row = rows[0]
    assert row["status"] == "INVALID_SYMBOL"
    assert row["mapping_status"] == "symbol 无效 / 映射待确认"
    assert row["alert_level_cn"] == "symbol 无效 / 映射待确认"
    assert row["spread_pct"] is None


def test_mapping_diagnostics_marks_invalid_symbol() -> None:
    provider = FakeProvider(price=None, error="invalid_symbol")

    rows = build_mapping_diagnostics(["NVDA"], mapping=_mapping(), provider=provider, validate=True)

    assert rows[0]["validation_status"] == "symbol 无效"
    assert rows[0]["exists"] is False
    assert rows[0]["error_message"] == "invalid_symbol"


def test_mapping_diagnostics_marks_unverified_valid_symbol() -> None:
    provider = FakeProvider(price=101.5)

    rows = build_mapping_diagnostics(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="unverified"),
        provider=provider,
        validate=True,
    )

    assert rows[0]["validation_status"] == "symbol 有效但映射未确认"
    assert rows[0]["exists"] is True
    assert rows[0]["price_available"] is True


def test_spot_stock_mapping_is_disabled_before_price_fetch() -> None:
    provider = FakeProvider(price=101.5, funding_rate=None)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        provider=provider,
        cache=FakeCache(),
    )

    assert rows[0]["status"] == "SPOT_DISABLED"
    assert rows[0]["binance_last_price"] is None
    assert rows[0]["spread_pct"] is None
    assert rows[0]["error"] == "stock_mapping_requires_usdm_futures"
    assert provider.calls == []


def test_spot_stock_mapping_does_not_use_symbol_specific_spot_validate() -> None:
    provider = SpotSymbolSpecificProvider()

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["status"] == "SPOT_DISABLED"
    assert row["binance_last_price"] is None
    assert provider.calls == []


def test_friday_holiday_uses_previous_trading_day_close() -> None:
    provider = FakeProvider(price=101.5)
    cache = FakeCache(pd.DataFrame([{"date": "2026-06-11", "close": 98.0}]))

    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=provider, cache=cache)

    assert rows[0]["friday_close"] == 98.0
    assert rows[0]["friday_close_date"] == "2026-06-11"
    assert rows[0]["close_source"] == "previous_trading_day_before_friday"
    assert provider.calls == ["usdm_futures:NVDAUSDT"]


def test_bid_ask_spread_and_funding_warnings_are_exposed() -> None:
    provider = FakeProvider(price=101.5, bid=100, ask=102, volume_24h=5_000, funding_rate=0.001)

    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=provider, cache=FakeCache())

    row = rows[0]
    assert round(row["binance_spread_pct"], 2) == 1.98
    assert "流动性不足" in row["liquidity_warning"]
    assert "成交量不足" in row["liquidity_warning"]
    assert "资金费率" in row["liquidity_warning"]


def test_manual_override_is_explicitly_marked_non_realtime() -> None:
    provider = FakeProvider(price=999)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(manual_override_enabled=True, manual_override_price=102),
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["binance_last_price"] == 102
    assert row["source"] == "manual_override_non_realtime"
    assert row["manual_override"] is True
    assert "手动覆盖 / 非实时 Binance 数据" in row["mapping_risk"]
    assert provider.calls == []


def test_candidate_discovery_does_not_mark_mapping_confirmed() -> None:
    provider = FakeProvider()

    rows = build_mapping_diagnostics(["NVDA"], mapping=_explicit_empty_mapping(), provider=provider, validate=False, include_candidates=True)

    assert rows[0]["validation_status"] == "暂无映射"
    assert rows[0]["candidates"][0]["symbol"] == "NVDAUSDT"
    assert rows[0]["candidates"][0]["status"] == "candidate"


def test_discover_candidates_searches_markets_without_confirming_mapping() -> None:
    provider = FakeProvider()

    result = discover_binance_symbol_candidates("NVDA", provider=provider)
    candidates = result["candidates"]

    assert result["data_source_status"] == "OK"
    assert {item["market_type"] for item in candidates} == {"usdm_futures"}
    assert all(item["status"] == "candidate" for item in candidates)
    assert all(item.get("mapping_confidence") != "confirmed" for item in candidates)
    assert "candidates:spot:NVDA" not in provider.calls
    assert "candidates:usdm_futures:NVDA" in provider.calls


def test_discover_candidates_distinguishes_normal_empty_from_unavailable_source() -> None:
    provider = CandidateStatusProvider(status="OK", candidates=[])

    result = discover_binance_symbol_candidates("ZZZZ", provider=provider)

    assert result["data_source_status"] == "OK"
    assert result["candidates"] == []
    assert result["provider_diagnostic_failed"] is False


def test_discover_candidates_reports_empty_exchange_info() -> None:
    provider = CandidateStatusProvider(status="EMPTY", candidates=[], message="symbols list is empty")

    result = discover_binance_symbol_candidates("NVDA", market_type="usdm_futures", provider=provider)

    assert result["data_source_status"] == "EMPTY"
    assert result["candidates"] == []
    assert result["provider_diagnostic_failed"] is True
    assert "symbols list is empty" in result["error_message"]


def test_discover_candidates_reports_blocked_exchange_info() -> None:
    provider = CandidateStatusProvider(status="BLOCKED", candidates=[], message="HTTP 451")

    result = discover_binance_symbol_candidates("NVDA", market_type="spot", provider=provider)

    assert result["data_source_status"] == "BLOCKED"
    assert result["candidates"] == []
    assert "HTTP 451" in result["error_message"]


def test_discover_candidates_reports_schema_mismatch() -> None:
    provider = CandidateStatusProvider(status="SCHEMA_MISMATCH", candidates=[], message="missing symbols")

    result = discover_binance_symbol_candidates("NVDA", market_type="spot", provider=provider)

    assert result["data_source_status"] == "SCHEMA_MISMATCH"
    assert result["candidates"] == []
    assert "missing symbols" in result["error_message"]


def test_discover_candidates_marks_provider_diagnostic_failed_when_btcusdt_probe_fails() -> None:
    provider = CandidateStatusProvider(status="UNAVAILABLE", candidates=[], message="provider diagnostic failed: BTCUSDT not found")

    result = discover_binance_symbol_candidates("NVDA", market_type="spot", provider=provider)

    assert result["data_source_status"] == "UNAVAILABLE"
    assert result["provider_diagnostic_failed"] is True
    assert "BTCUSDT" in result["error_message"]


def test_mapping_diagnostics_does_not_render_provider_unavailable_as_no_candidates() -> None:
    provider = CandidateStatusProvider(status="BLOCKED", candidates=[], message="HTTP 451")

    rows = build_mapping_diagnostics(["NVDA"], mapping=_explicit_empty_mapping(), provider=provider, validate=False, include_candidates=True)

    assert rows[0]["candidate_scan_status"] == "BLOCKED"
    assert rows[0]["candidates"] == []
    assert rows[0]["candidate_scan_message"] == "Binance API 可能被网络或地区限制拦截"


def test_mapping_diagnostics_shows_futures_source_unavailable() -> None:
    provider = CandidateStatusProvider(status="UNAVAILABLE", candidates=[], message="timeout")

    rows = build_mapping_diagnostics(["NVDA"], mapping=_mapping(market_type="usdm_futures"), provider=provider, include_candidates=True)

    assert rows[0]["candidate_scan_status"] == "UNAVAILABLE"
    assert rows[0]["candidate_scan_message"] == "Futures 数据源不可用"


def test_smoke_script_requires_local_mapping_without_provider_calls(tmp_path) -> None:
    provider = FakeProvider()

    result = smoke_binance_provider.run_smoke(tmp_path / "missing.local.json", provider=provider)

    assert result["mapping_missing"] is True
    assert result["count"] == 0
    assert "Copy" in result["message"]
    assert provider.calls == []


def test_smoke_script_validates_and_fetches_enabled_local_mapping(tmp_path) -> None:
    mapping_path = tmp_path / "binance_symbol_mapping.local.json"
    mapping_path.write_text(
        json.dumps(
            {
                "mappings": {
                    "NVDA": {
                        "enabled": True,
                        "binance_symbol": "NVDAUSDT",
                        "market_type": "usdm_futures",
                        "quote_currency": "USDT",
                        "unit_multiplier": 1,
                        "mapping_confidence": "confirmed",
                    },
                    "MSFT": {
                        "enabled": False,
                        "binance_symbol": "MSFTUSDT",
                        "market_type": "usdm_futures",
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    provider = FakeProvider(price=207, bid=206.8, ask=207.2, volume_24h=123_456, funding_rate=0.0002)

    result = smoke_binance_provider.run_smoke(mapping_path, provider=provider)

    assert result["mapping_missing"] is False
    assert result["count"] == 1
    row = result["results"][0]
    assert row["ticker"] == "NVDA"
    assert row["binance_symbol"] == "NVDAUSDT"
    assert row["mapping_confidence"] == "confirmed"
    assert row["exists"] is True
    assert row["last_price"] == 207
    assert row["bid"] == 206.8
    assert row["ask"] == 207.2
    assert row["volume_24h"] == 123_456
    assert row["funding_rate"] == 0.0002
    assert round(row["bid_ask_spread_pct"], 3) == 0.193
    assert provider.calls == ["validate:usdm_futures:NVDAUSDT", "usdm_futures:NVDAUSDT"]


def test_smoke_script_supports_one_off_symbol_validation() -> None:
    provider = FakeProvider(price=207, bid=206.8, ask=207.2, volume_24h=123_456, funding_rate=None)

    row = smoke_binance_provider.run_symbol_smoke(ticker="ADBE", symbol="ADBEUSDT", market_type="spot", provider=provider)

    assert row["ticker"] == "ADBE"
    assert row["binance_symbol"] == "ADBEUSDT"
    assert row["exists"] is True
    assert row["last_price"] == 207
    assert row["bid"] == 206.8
    assert row["ask"] == 207.2
    assert row["volume_24h"] == 123_456
    assert provider.calls == ["validate:spot:ADBEUSDT", "spot:ADBEUSDT"]


def test_smoke_script_cli_defaults_to_usdm_futures_for_stock_mapping() -> None:
    source = inspect.getsource(smoke_binance_provider.main)

    assert 'default="usdm_futures"' in source


class RecordingHTTPProvider(BinanceHTTPPriceProvider):
    def __init__(self) -> None:
        super().__init__(spot_base_url="https://spot.test", futures_base_url="https://futures.test", exchange_info_cache_path=None)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
        self.calls.append((base_url, path, dict(params)))
        if path.endswith("exchangeInfo"):
            symbol = params.get("symbol")
            if symbol:
                return {"symbols": [{"symbol": symbol, "baseAsset": symbol.removesuffix("USDT"), "quoteAsset": "USDT"}]}
            return {
                "symbols": [
                    {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"},
                    {"symbol": "NVDAUSDT", "baseAsset": "NVDA", "quoteAsset": "USDT"},
                ]
            }
        if path.endswith("ticker/price"):
            return {"price": "101.5"}
        if path.endswith("bookTicker"):
            return {"bidPrice": "101.4", "askPrice": "101.6"}
        if path.endswith("ticker/24hr"):
            return {"lastPrice": "101.5", "volume": "100000"}
        if path.endswith("premiumIndex"):
            return {"lastFundingRate": "0.0001"}
        if path.endswith("klines"):
            return [[params.get("startTime") or 0, "100", "110", "95", "105", "1000"]]
        return {}


class FallbackSpotProvider(RecordingHTTPProvider):
    def __init__(self) -> None:
        super().__init__()
        self.spot_base_urls = ["https://data-api.test", "https://api.test"]
        self.spot_base_url = self.spot_base_urls[0]

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
        self.calls.append((base_url, path, dict(params)))
        if base_url == "https://data-api.test":
            raise TimeoutError("data-api timeout")
        return super()._get_json(base_url, path, params)


class DefaultSpotProvider(BinanceHTTPPriceProvider):
    def __init__(self) -> None:
        super().__init__(exchange_info_cache_path=None)
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
        self.calls.append((base_url, path, dict(params)))
        if path.endswith("exchangeInfo"):
            return {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}]}
        if path.endswith("ticker/price"):
            return {"price": "101.5"}
        if path.endswith("bookTicker"):
            return {"bidPrice": "101.4", "askPrice": "101.6"}
        if path.endswith("ticker/24hr"):
            return {"lastPrice": "101.5", "volume": "100000"}
        return {}


def test_http_provider_uses_usdm_futures_endpoints_and_validates_symbol() -> None:
    provider = RecordingHTTPProvider()

    snapshot = provider.get_last_price("NVDAUSDT", market_type="usdm_futures")

    assert snapshot.last_price == 101.5
    assert snapshot.funding_rate == 0.0001
    assert snapshot.source == "binance_usdm_futures"
    assert [path for _, path, _ in provider.calls] == [
        "/fapi/v1/exchangeInfo",
        "/fapi/v2/ticker/price",
        "/fapi/v1/ticker/bookTicker",
        "/fapi/v1/ticker/24hr",
        "/fapi/v1/premiumIndex",
    ]


def test_http_provider_uses_spot_endpoints_without_funding_rate() -> None:
    provider = RecordingHTTPProvider()

    snapshot = provider.get_last_price("NVDAUSDT", market_type="spot")

    assert snapshot.last_price == 101.5
    assert snapshot.funding_rate is None
    assert snapshot.source == "binance_spot"
    assert [path for _, path, _ in provider.calls] == [
        "/api/v3/exchangeInfo",
        "/api/v3/ticker/price",
        "/api/v3/ticker/bookTicker",
        "/api/v3/ticker/24hr",
    ]


def test_http_provider_fetches_klines_endpoints() -> None:
    provider = RecordingHTTPProvider()

    futures = provider.get_klines("NVDAUSDT", market_type="usdm_futures", start_time_ms=1, end_time_ms=2)
    spot = provider.get_klines("NVDAUSDT", market_type="spot", start_time_ms=1, end_time_ms=2)

    assert futures[0][2] == "110"
    assert spot[0][2] == "110"
    assert (provider.futures_base_url, "/fapi/v1/klines", {"symbol": "NVDAUSDT", "interval": "1m", "limit": "1000", "startTime": "1", "endTime": "2"}) in provider.calls
    assert ("https://spot.test", "/api/v3/klines", {"symbol": "NVDAUSDT", "interval": "1m", "limit": "1000", "startTime": "1", "endTime": "2"}) in provider.calls


def test_http_provider_spot_uses_data_api_base_url_first() -> None:
    provider = DefaultSpotProvider()

    provider.get_last_price("BTCUSDT", market_type="spot")

    assert provider.calls[0][0] == "https://data-api.binance.vision"


def test_http_provider_spot_falls_back_when_data_api_fails() -> None:
    provider = FallbackSpotProvider()

    snapshot = provider.get_last_price("BTCUSDT", market_type="spot")

    assert snapshot.last_price == 101.5
    assert ("https://data-api.test", "/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}) in provider.calls
    assert ("https://api.test", "/api/v3/exchangeInfo", {"symbol": "BTCUSDT"}) in provider.calls


def test_exchange_info_cache_is_written_and_reused(tmp_path) -> None:
    cache_path = tmp_path / "exchange_info_cache.json"
    provider = ExchangeInfoCacheProvider(cache_path)

    first = provider._exchange_info("spot", "BTCUSDT")
    second = provider._exchange_info("spot", "BTCUSDT")

    assert first == second
    assert len(provider.calls) == 1
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["spot:BTCUSDT"]["market_type"] == "spot"
    assert payload["spot:BTCUSDT"]["symbol_count"] == 1


def test_exchange_info_cache_falls_back_to_expired_payload_when_network_fails(tmp_path) -> None:
    cache_path = tmp_path / "exchange_info_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "spot:BTCUSDT": {
                    "updated_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                    "market_type": "spot",
                    "base_url": "https://spot.test",
                    "symbol_count": 1,
                    "payload": {"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}]},
                }
            }
        ),
        encoding="utf-8",
    )

    class FailingProvider(ExchangeInfoCacheProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            raise TimeoutError("network down")

    provider = FailingProvider(cache_path)

    payload = provider._exchange_info("spot", "BTCUSDT")

    assert payload["symbols"][0]["symbol"] == "BTCUSDT"


def test_http_provider_marks_unknown_symbol_invalid() -> None:
    class InvalidSymbolProvider(RecordingHTTPProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            self.calls.append((base_url, path, dict(params)))
            if path.endswith("exchangeInfo"):
                return {"symbols": []}
            return {}

    provider = InvalidSymbolProvider()

    snapshot = provider.get_last_price("BADUSDT", market_type="spot")

    assert snapshot.error == "invalid_symbol"
    assert snapshot.last_price is None
    assert [path for _, path, _ in provider.calls] == ["/api/v3/exchangeInfo"]


def test_http_provider_discovers_candidates_without_confirming_mapping() -> None:
    provider = RecordingHTTPProvider()

    candidates = provider.find_symbol_candidates("NVDA", market_type="usdm_futures")

    assert candidates[0].symbol == "NVDAUSDT"
    assert candidates[0].status == "candidate"
    assert candidates[0].quote_currency == "USDT"


def test_http_provider_candidate_search_reports_region_block() -> None:
    class BlockedProvider(RecordingHTTPProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            raise HTTPError(f"{base_url}{path}", 451, "Unavailable For Legal Reasons", hdrs=None, fp=None)

    provider = BlockedProvider()

    result = provider.find_symbol_candidates_with_status("NVDA", market_type="usdm_futures")

    assert result.data_source_status == "BLOCKED"
    assert result.candidates == []
    assert "HTTPError 451" in result.error_message


def test_http_provider_candidate_search_reports_forbidden_block() -> None:
    class ForbiddenProvider(RecordingHTTPProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            raise HTTPError(f"{base_url}{path}", 403, "Forbidden", hdrs=None, fp=None)

    provider = ForbiddenProvider()

    result = provider.find_symbol_candidates_with_status("NVDA", market_type="usdm_futures")

    assert result.data_source_status == "BLOCKED"
    assert result.candidates == []
    assert "HTTPError 403" in result.error_message


def test_http_provider_candidate_search_reports_json_parse_error() -> None:
    class JsonParseProvider(RecordingHTTPProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            raise json.JSONDecodeError("bad json", "not-json", 0)

    provider = JsonParseProvider()

    result = provider.find_symbol_candidates_with_status("NVDA", market_type="spot")

    assert result.data_source_status == "PARSE_ERROR"
    assert result.candidates == []
    assert "JSONDecodeError" in result.error_message


def test_http_provider_candidate_search_reports_schema_mismatch() -> None:
    class SchemaMismatchProvider(RecordingHTTPProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            return {"unexpected": []}

    provider = SchemaMismatchProvider()

    result = provider.find_symbol_candidates_with_status("NVDA", market_type="spot")

    assert result.data_source_status == "SCHEMA_MISMATCH"
    assert result.candidates == []
    assert "missing symbols" in result.error_message


def test_http_provider_candidate_search_reports_btcusdt_probe_failure() -> None:
    class MissingBtcProvider(RecordingHTTPProvider):
        def _get_json(self, base_url: str, path: str, params: dict[str, str]) -> dict:
            return {"symbols": [{"symbol": "NVDAUSDT", "baseAsset": "NVDA", "quoteAsset": "USDT"}]}

    provider = MissingBtcProvider()

    result = provider.find_symbol_candidates_with_status("NVDA", market_type="spot")

    assert result.data_source_status == "UNAVAILABLE"
    assert result.provider_diagnostic_failed is True
    assert "BTCUSDT" in result.error_message


def test_http_provider_uses_env_base_url_override(monkeypatch) -> None:
    monkeypatch.setenv("BINANCE_SPOT_BASE_URL", "https://spot.env.test")
    monkeypatch.setenv("BINANCE_SPOT_DATA_BASE_URL", "https://data.env.test")
    monkeypatch.setenv("BINANCE_USDM_BASE_URL", "https://usdm.env.test")

    provider = BinanceHTTPPriceProvider()

    assert provider.spot_base_urls[:2] == ["https://data.env.test", "https://spot.env.test"]
    assert provider.spot_base_url == "https://data.env.test"
    assert provider.futures_base_url == "https://usdm.env.test"


def test_mapping_config_loads_structured_and_legacy_symbols(tmp_path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text(
        json.dumps(
            {
                "mappings": {
                    "nvda": "nvdausdt",
                    "adbe": {
                        "enabled": True,
                        "binance_symbol": "adbeusdt",
                        "market_type": "usdm_futures",
                        "quote_currency": "USDT",
                        "unit_multiplier": 1,
                        "mapping_confidence": "manual_required",
                    },
                    "": "bad",
                }
            }
        ),
        encoding="utf-8",
    )

    mapping = load_binance_symbol_mapping(path, local_path=None)

    assert mapping["NVDA"]["binance_symbol"] == "NVDAUSDT"
    assert mapping["NVDA"]["mapping_confidence"] == "confirmed"
    assert mapping["ADBE"]["binance_symbol"] == "ADBEUSDT"
    assert mapping["ADBE"]["mapping_confidence"] == "manual_required"


def test_mapping_loader_prefers_local_mapping_without_merging_example(tmp_path) -> None:
    base = tmp_path / "mapping.json"
    local = tmp_path / "mapping.local.json"
    base.write_text(
        json.dumps(
            {
                "mappings": {
                    "NVDA": {"enabled": True, "binance_symbol": "OLDUSDT"},
                    "ADBE": {"enabled": True, "binance_symbol": "ADBEUSDT"},
                }
            }
        ),
        encoding="utf-8",
    )
    local.write_text(
        json.dumps(
            {
                "mappings": {
                    "NVDA": {
                        "enabled": True,
                        "binance_symbol": "NVDAUSDT",
                        "market_type": "usdm_futures",
                        "quote_currency": "USDT",
                        "unit_multiplier": 1,
                        "mapping_confidence": "confirmed",
                        "validation_status": "confirmed",
                        "last_validated_at": "2026-06-14T12:00:00+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    mapping = load_binance_symbol_mapping(base, local_path=local)

    assert mapping["NVDA"]["binance_symbol"] == "NVDAUSDT"
    assert mapping["NVDA"]["validation_status"] == "confirmed"
    assert "ADBE" not in mapping


def test_mapping_loader_returns_empty_when_example_and_local_are_missing(tmp_path) -> None:
    mapping = load_binance_symbol_mapping(
        tmp_path / "missing.example.json",
        local_path=tmp_path / "missing.local.json",
    )

    assert mapping == {}


def test_upsert_local_mapping_saves_symbol_without_realtime_price(tmp_path) -> None:
    path = tmp_path / "binance_symbol_mapping.local.json"

    mapping = upsert_local_binance_symbol_mapping(
        "nvda",
        "nvdabusdt",
        market_type="usdm_futures",
        mapping_confidence="candidate",
        risk_note="候选 symbol 不代表真实美股映射关系，需要人工确认。",
        path=path,
    )
    loaded = load_binance_symbol_mapping(path, local_path=None)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert mapping["NVDA"]["binance_symbol"] == "NVDABUSDT"
    assert loaded["NVDA"]["market_type"] == "usdm_futures"
    assert loaded["NVDA"]["mapping_confidence"] == "candidate"
    assert "manual_override_price" not in payload["mappings"]["NVDA"]
    assert "last_price" not in payload["mappings"]["NVDA"]


def test_upsert_local_mapping_rejects_missing_symbol(tmp_path) -> None:
    path = tmp_path / "binance_symbol_mapping.local.json"

    try:
        upsert_local_binance_symbol_mapping("NVDA", "", path=path)
    except ValueError as exc:
        assert str(exc) == "binance_symbol_required"
    else:
        raise AssertionError("missing symbol should be rejected")

    assert not path.exists()


def test_upsert_local_mapping_rejects_spot_market_type(tmp_path) -> None:
    path = tmp_path / "binance_symbol_mapping.local.json"

    try:
        upsert_local_binance_symbol_mapping("NVDA", "NVDABUSDT", market_type="spot", path=path)
    except ValueError as exc:
        assert str(exc) == "stock_mapping_requires_usdm_futures"
    else:
        raise AssertionError("spot stock mapping should be disabled")

    assert not path.exists()


def test_upsert_default_usdm_futures_mappings_adds_ticker_usdt_candidates(tmp_path) -> None:
    path = tmp_path / "binance_symbol_mapping.local.json"

    result = upsert_default_usdm_futures_mappings(["nvda", "msft"], path=path)
    mapping = load_binance_symbol_mapping(path, local_path=None)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert result["created"] == 2
    assert result["skipped"] == 0
    assert mapping["NVDA"]["binance_symbol"] == "NVDAUSDT"
    assert mapping["NVDA"]["market_type"] == "usdm_futures"
    assert mapping["NVDA"]["mapping_confidence"] == "candidate"
    assert mapping["MSFT"]["binance_symbol"] == "MSFTUSDT"
    assert "manual_override_price" not in payload["mappings"]["NVDA"]
    assert "last_price" not in payload["mappings"]["NVDA"]


def test_upsert_default_usdm_futures_mappings_preserves_existing_mapping(tmp_path) -> None:
    path = tmp_path / "binance_symbol_mapping.local.json"
    upsert_local_binance_symbol_mapping("NVDA", "CUSTOMUSDT", path=path)

    result = upsert_default_usdm_futures_mappings(["NVDA", "MSFT"], path=path)
    mapping = load_binance_symbol_mapping(path, local_path=None)

    assert result["created"] == 1
    assert result["skipped"] == 1
    assert mapping["NVDA"]["binance_symbol"] == "CUSTOMUSDT"
    assert mapping["MSFT"]["binance_symbol"] == "MSFTUSDT"


def test_mapping_counts_separate_local_mapping_from_universe_mapping() -> None:
    mapping = {
        "BTC": {
            "enabled": True,
            "binance_symbol": "BTCUSDT",
            "market_type": "spot",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": "candidate",
        }
    }
    rows = build_weekend_spread_rows(["NVDA", "MSFT"], mapping=mapping, provider=FakeProvider(), cache=FakeCache())

    counts = weekend_spread._mapping_counts(rows, mapping)
    default_rows = weekend_spread._filter_rows(
        rows,
        scope="重点/有数据",
        confirmed_only=False,
        focus_only=False,
        abnormal_only=False,
    )

    assert counts["local_mapping_count"] == 1
    assert counts["universe_mapping_count"] == 0
    assert counts["price_row_count"] == 0
    assert counts["universe_total"] == 2
    assert default_rows == []
    assert weekend_spread._should_show_empty_mapping_state(counts, "重点/有数据") is True


def test_weekend_spread_snapshot_cache_round_trips_fresh_rows(tmp_path) -> None:
    path = tmp_path / "weekend_spread_snapshot.json"
    mapping = _mapping()
    rows = build_weekend_spread_rows(["NVDA"], mapping=mapping, provider=FakeProvider(price=103), cache=FakeCache())
    generated_at = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)

    write_weekend_spread_snapshot(rows, mapping=mapping, tickers=["NVDA"], path=path, generated_at=generated_at)
    snapshot = read_weekend_spread_snapshot(mapping=mapping, tickers=["NVDA"], path=path, now=generated_at + timedelta(hours=1))
    cached_rows = annotate_cached_rows(snapshot["rows"], cache_state=snapshot["cache_state"], generated_at=snapshot["generated_at"])

    assert snapshot["cache_state"] == "FRESH"
    assert cached_rows[0]["binance_last_price"] == 103
    assert cached_rows[0]["data_source_text"] == "缓存"


def test_weekend_spread_snapshot_cache_marks_stale_and_changed_hashes(tmp_path) -> None:
    path = tmp_path / "weekend_spread_snapshot.json"
    mapping = _mapping()
    rows = build_weekend_spread_rows(["NVDA"], mapping=mapping, provider=FakeProvider(price=103), cache=FakeCache())
    generated_at = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)

    write_weekend_spread_snapshot(rows, mapping=mapping, tickers=["NVDA"], path=path, generated_at=generated_at)

    stale = read_weekend_spread_snapshot(mapping=mapping, tickers=["NVDA"], path=path, now=generated_at + timedelta(hours=25))
    changed_mapping = read_weekend_spread_snapshot(mapping=_mapping(symbol="MSFTUSDT"), tickers=["NVDA"], path=path, now=generated_at + timedelta(hours=1))
    changed_universe = read_weekend_spread_snapshot(mapping=mapping, tickers=["NVDA", "MSFT"], path=path, now=generated_at + timedelta(hours=1))

    assert stale["cache_state"] == "STALE"
    assert stale["rows"][0]["binance_last_price"] == 103
    assert changed_mapping["cache_state"] == "MAPPING_CHANGED"
    assert changed_universe["cache_state"] == "UNIVERSE_CHANGED"


def test_provider_failure_preserves_last_good_snapshot(tmp_path) -> None:
    path = tmp_path / "weekend_spread_snapshot.json"
    mapping = _mapping()
    good_rows = build_weekend_spread_rows(["NVDA"], mapping=mapping, provider=FakeProvider(price=103), cache=FakeCache())
    write_weekend_spread_snapshot(good_rows, mapping=mapping, tickers=["NVDA"], path=path)

    failure_rows = build_weekend_spread_rows(["NVDA"], mapping=mapping, provider=FakeProvider(price=None, error="timeout"), cache=FakeCache())
    write_weekend_spread_failure(error_message="timeout", path=path)
    snapshot = read_weekend_spread_snapshot(mapping=mapping, tickers=["NVDA"], path=path)

    assert has_successful_price(good_rows) is True
    assert is_provider_failure(failure_rows) is True
    assert snapshot["rows"][0]["binance_last_price"] == 103
    assert snapshot["last_failure"]["data_status"] == "REFRESH_FAILED"
    assert snapshot["last_failure"]["error_message"] == "timeout"


def test_mapping_counts_include_universe_mapping_when_ticker_is_in_watchlist() -> None:
    rows = build_weekend_spread_rows(["NVDA", "MSFT"], mapping=_mapping(), provider=FakeProvider(), cache=FakeCache())

    counts = weekend_spread._mapping_counts(rows, _mapping())
    default_rows = weekend_spread._filter_rows(
        rows,
        scope="重点/有数据",
        confirmed_only=False,
        focus_only=False,
        abnormal_only=False,
    )

    assert counts["local_mapping_count"] == 1
    assert counts["universe_mapping_count"] == 1
    assert counts["price_row_count"] == 1
    assert counts["universe_total"] == 2
    assert [row["ticker"] for row in default_rows] == ["NVDA"]


def test_empty_mapping_state_explains_configuration_when_universe_has_no_mapping(tmp_path) -> None:
    counts = {
        "local_mapping_count": 0,
        "universe_mapping_count": 0,
        "price_row_count": 0,
        "universe_total": 32,
    }

    message = weekend_spread._empty_mapping_message(counts, tmp_path / "binance_symbol_mapping.local.json")

    assert "当前观察池暂无 Binance 映射" in message
    assert "Binance 价格可通过 API 自动读取" in message
    assert "ticker -> binance_symbol" in message
    assert "binance_symbol_mapping.local.json" in message
    assert "NVDA -> NVDAUSDT / usdm_futures / candidate" in message
    assert "不属于当前观察池" not in message
    assert weekend_spread._off_universe_mapping_note(counts) == "本地未配置映射"


def test_empty_mapping_state_mentions_local_mapping_outside_universe(tmp_path) -> None:
    counts = {
        "local_mapping_count": 1,
        "universe_mapping_count": 0,
        "price_row_count": 0,
        "universe_total": 32,
    }

    message = weekend_spread._empty_mapping_message(counts, tmp_path / "binance_symbol_mapping.local.json")

    assert "本地配置有 mapping，但不属于当前观察池" in message
    assert weekend_spread._off_universe_mapping_note(counts) == "本地配置有 mapping，但不属于当前观察池"


def test_non_universe_mapping_is_not_recorded_as_sample(tmp_path) -> None:
    mapping = {
        "BTC": {
            "enabled": True,
            "binance_symbol": "BTCUSDT",
            "market_type": "spot",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": "confirmed",
        }
    }
    rows = build_weekend_spread_rows(["NVDA"], mapping=mapping, provider=FakeProvider(), cache=FakeCache())

    samples = record_spread_samples(rows, path=tmp_path / "weekend_spread_log.json", week_id="2026-W24")

    assert samples == []


def test_record_current_snapshot_writes_mapped_samples_only(tmp_path) -> None:
    provider = FakeProvider(price=101.5)
    rows = build_weekend_spread_rows(
        ["NVDA", "MSFT"],
        mapping=_mapping(),
        provider=provider,
        cache=FakeCache(),
    )

    samples = record_spread_samples(
        rows,
        path=tmp_path / "weekend_spread_log.json",
        week_id="2026-W24",
        observed_at="2026-06-14T12:00:00+00:00",
    )

    assert len(samples) == 1
    assert samples[0]["ticker"] == "NVDA"
    assert samples[0]["binance_symbol"] == "NVDAUSDT"
    assert samples[0]["data_quality"] == "REGULAR_CLOSE_FALLBACK"


def test_no_mapping_rows_do_not_pollute_weekly_summary(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    rows = build_weekend_spread_rows(["MSFT"], mapping=_explicit_empty_mapping(), provider=FakeProvider(), cache=FakeCache())

    samples = record_spread_samples(rows, path=path, week_id="2026-W24")
    summaries = generate_weekly_summary(path=path, week_id="2026-W24")

    assert samples == []
    assert summaries == []


def test_weekly_summary_calculates_peak_spreads(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    premium = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(price=103), cache=FakeCache())
    discount = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(price=98), cache=FakeCache())
    record_spread_samples(premium, path=path, week_id="2026-W24", observed_at="2026-06-14T12:00:00+00:00")
    record_spread_samples(discount, path=path, week_id="2026-W24", observed_at="2026-06-15T12:00:00+00:00")

    summaries = generate_weekly_summary(path=path, week_id="2026-W24")
    summary = summaries[0]

    assert round(summary["max_premium_pct"], 2) == 3.0
    assert round(summary["max_discount_pct"], 2) == -2.0
    assert round(summary["max_abs_spread_pct"], 2) == 3.0
    assert summary["sample_count"] == 2


def test_weekly_summary_uses_primary_spread_pct_for_peak_spreads(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    premium = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=121),
        afterhours_provider=FakeAfterhoursProvider(reference_price=120),
        cache=FakeCache(_history(close=100)),
    )
    discount = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=118),
        afterhours_provider=FakeAfterhoursProvider(reference_price=120),
        cache=FakeCache(_history(close=100)),
    )
    record_spread_samples(premium, path=path, week_id="2026-W24", observed_at="2026-06-14T12:00:00+00:00")
    record_spread_samples(discount, path=path, week_id="2026-W24", observed_at="2026-06-15T12:00:00+00:00")

    summary = generate_weekly_summary(path=path, week_id="2026-W24")[0]

    assert round(summary["max_premium_pct"], 2) == 0.83
    assert round(summary["max_discount_pct"], 2) == -1.67
    assert round(summary["max_abs_spread_pct"], 2) == 1.67
    assert summary["primary_spread_anchor"] == "AFTERHOURS_REFERENCE"
    assert summary["data_quality"] == "OK"


def test_monday_reference_generates_gap_direction_and_capture(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(price=103), cache=FakeCache())
    record_spread_samples(rows, path=path, week_id="2026-W24", observed_at="2026-06-14T12:00:00+00:00")
    generate_weekly_summary(path=path, week_id="2026-W24")

    updated = update_monday_outcome(
        "NVDA",
        path=path,
        week_id="2026-W24",
        monday_reference_price=102,
        reference_type="MONDAY_PREMARKET_OPEN",
        estimated_cost_pct=0.1,
    )

    assert updated is not None
    assert round(updated["monday_gap_pct"], 2) == 2.0
    assert updated["direction_hit"] is True
    assert round(updated["capture_ratio"], 2) == 0.67
    assert round(updated["net_edge_pct"], 2) == 1.9
    assert updated["outcome_status"] == "HIT"


def test_monday_reference_prefers_afterhours_anchor(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=210),
        afterhours_provider=FakeAfterhoursProvider(reference_price=208),
        cache=FakeCache(_history(close=205)),
    )
    record_spread_samples(rows, path=path, week_id="2026-W24", observed_at="2026-06-14T12:00:00+00:00")
    generate_weekly_summary(path=path, week_id="2026-W24")

    updated = update_monday_outcome("NVDA", path=path, week_id="2026-W24", monday_reference_price=209)

    assert updated is not None
    assert round(updated["monday_gap_pct"], 2) == 0.48
    assert round(updated["monday_gap_from_regular_close_pct"], 2) == 1.95
    assert round(updated["monday_gap_from_afterhours_pct"], 2) == 0.48


def test_outcome_status_partial_miss_and_invalid(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(price=104), cache=FakeCache())
    record_spread_samples(rows, path=path, week_id="2026-W24", observed_at="2026-06-14T12:00:00+00:00")
    generate_weekly_summary(path=path, week_id="2026-W24")

    partial = update_monday_outcome("NVDA", path=path, week_id="2026-W24", monday_reference_price=101)
    miss = update_monday_outcome("NVDA", path=path, week_id="2026-W24", monday_reference_price=98)

    unconfirmed = build_weekend_spread_rows(
        ["ADBE"],
        mapping={
            "ADBE": {
                "enabled": True,
                "binance_symbol": "ADBEUSDT",
                "market_type": "usdm_futures",
                "quote_currency": "USDT",
                "unit_multiplier": 1,
                "mapping_confidence": "manual_required",
            }
        },
        provider=FakeProvider(price=207),
        cache=FakeCache(_history(close=204)),
    )
    record_spread_samples(unconfirmed, path=path, week_id="2026-W24", observed_at="2026-06-14T12:05:00+00:00")
    generate_weekly_summary(path=path, week_id="2026-W24")
    invalid = update_monday_outcome("ADBE", path=path, week_id="2026-W24", monday_reference_price=207)

    assert partial is not None and partial["outcome_status"] == "PARTIAL"
    assert miss is not None and miss["outcome_status"] == "MISS"
    assert invalid is not None and invalid["outcome_status"] == "INVALID"
    assert invalid["data_quality"] == "MAPPING_UNCONFIRMED"


def test_history_stats_calculates_hit_rate(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    week1 = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(price=104), cache=FakeCache())
    week2 = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(price=104), cache=FakeCache())
    record_spread_samples(week1, path=path, week_id="2026-W24", observed_at="2026-06-14T12:00:00+00:00")
    record_spread_samples(week2, path=path, week_id="2026-W25", observed_at="2026-06-21T12:00:00+00:00")
    generate_weekly_summary(path=path, week_id="2026-W24")
    generate_weekly_summary(path=path, week_id="2026-W25")
    update_monday_outcome("NVDA", path=path, week_id="2026-W24", monday_reference_price=102)
    update_monday_outcome("NVDA", path=path, week_id="2026-W25", monday_reference_price=98)

    stats = build_history_stats(path)

    assert stats[0]["ticker"] == "NVDA"
    assert stats[0]["sample_weeks"] == 2
    assert stats[0]["hit_count"] == 1
    assert stats[0]["miss_count"] == 1
    assert stats[0]["hit_rate"] == 0.5


def test_recent_weekend_windows_use_sunday_20_et_and_convert_dst_to_shanghai() -> None:
    windows = recent_weekend_windows(weeks=1, now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc))
    window = windows[0]

    assert window.start_et.tzinfo == ZoneInfo("America/New_York")
    assert window.start_et.strftime("%A %H:%M") == "Friday 20:00"
    assert window.end_et.strftime("%A %H:%M") == "Sunday 20:00"
    assert window.end_shanghai.strftime("%A %H:%M") == "Monday 08:00"


def test_recent_weekend_windows_convert_winter_to_shanghai_09() -> None:
    window = recent_weekend_windows(weeks=1, now=datetime(2026, 1, 5, 2, tzinfo=timezone.utc))[0]

    assert window.end_et.strftime("%A %H:%M") == "Sunday 20:00"
    assert window.end_shanghai.strftime("%A %H:%M") == "Monday 09:00"


def test_weekend_peak_short_backtest_calculates_premium_decay_from_open_window_vwap() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=10), 100, 110, 99, 108),
        _kline(window.start_et + timedelta(hours=8), 108, 115, 107, 114),
        _kline(window.end_et, 105, 106, 103, 104, 1),
        _kline(window.end_et + timedelta(minutes=1), 104, 105, 102, 103, 3),
    ]

    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        weeks=1,
        open_window_minutes=5,
        fee_pct=0.10,
        slippage_pct=0.10,
        funding_pct=0.00,
        now=now,
    )

    row = rows[0]
    assert row["anchor_price"] == 100
    assert row["anchor_source"] == "AFTERHOURS_REFERENCE"
    assert row["weekend_peak_binance_price"] == 115
    assert row["weekend_peak_price"] == 115
    assert row["open_reference_method"] == "VWAP_5M"
    assert round(row["open_reference_price"], 2) == 103.25
    assert round(row["weekend_peak_premium_pct"], 2) == 15.0
    assert round(row["open_remaining_premium_pct"], 2) == 3.25
    assert round(row["premium_decay_pct"], 2) == 11.75
    assert round(row["premium_decay_ratio"], 2) == 78.33
    assert round(row["theoretical_short_return_pct"], 2) == 10.22
    assert round(row["net_short_return_pct"], 2) == 10.02
    assert round(row["short_return_at_open_pct"], 2) == 10.22
    assert row["data_quality"] == "OK"


def test_weekend_peak_short_backtest_falls_back_to_first_open_without_vwap() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(hours=8), 108, 115, 107, 114),
        _kline(window.end_et, 105, 106, 103, 104, 0),
    ]

    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        weeks=1,
        now=now,
    )

    assert rows[0]["open_reference_method"] == "FIRST_OPEN"
    assert rows[0]["open_reference_price"] == 105


def test_weekend_peak_short_backtest_falls_back_to_regular_close_anchor() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(hours=8), 108, 115, 107, 114),
        _kline(window.end_et, 105, 106, 103, 104, 1),
    ]

    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=None, regular=100),
        provider=FakeKlineProvider(bars),
        weeks=1,
        now=now,
    )

    assert rows[0]["anchor_price"] == 100
    assert rows[0]["anchor_source"] == "REGULAR_CLOSE"
    assert round(rows[0]["weekend_peak_premium_pct"], 2) == 15.0


def test_weekend_peak_short_backtest_marks_missing_anchor_invalid() -> None:
    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=None, regular=None),
        provider=FakeKlineProvider([]),
        weeks=1,
        now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
    )

    assert rows[0]["data_quality"] == "INVALID"
    assert rows[0]["error_message"] == "missing anchor price"


def test_weekend_peak_short_backtest_marks_spot_as_observation_only() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=10), 100, 110, 99, 108),
        _kline(window.end_et, 105, 106, 103, 104),
    ]

    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        anchors=_anchors(),
        provider=FakeKlineProvider(bars),
        weeks=1,
        now=now,
    )

    assert rows[0]["data_quality"] == "SPOT_OBSERVATION_ONLY"
    assert "观察收益" in rows[0]["result_note"]


def test_weekend_peak_short_backtest_handles_futures_timeout() -> None:
    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(),
        provider=FakeKlineProvider(error=TimeoutError("futures timeout")),
        weeks=1,
        now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc),
    )

    assert rows[0]["data_quality"] == "DATA_UNAVAILABLE"
    assert "futures timeout" in rows[0]["error_message"]


def test_weekend_peak_short_backtest_marks_unconfirmed_mapping() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=10), 100, 110, 99, 108),
        _kline(window.end_et, 105, 106, 103, 104),
    ]

    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        anchors=_anchors(),
        provider=FakeKlineProvider(bars),
        weeks=1,
        now=now,
    )

    assert rows[0]["data_quality"] == "UNCONFIRMED_MAPPING"
    assert "仅作观察" in rows[0]["result_note"]


def test_backtest_summary_excludes_unconfirmed_mapping_from_formal_win_rate() -> None:
    rows = [
        {"net_short_return_pct": 2.0, "theoretical_short_return_pct": 2.2, "premium_decay_ratio": 80, "premium_decay_pct": 4, "open_remaining_premium_pct": 1, "data_quality": "OK"},
        {"net_short_return_pct": 9.0, "theoretical_short_return_pct": 9.2, "premium_decay_ratio": 90, "premium_decay_pct": 9, "open_remaining_premium_pct": 1, "data_quality": "UNCONFIRMED_MAPPING"},
    ]

    summary = summarize_backtest_results(rows)

    assert summary["sample_weeks"] == 1
    assert summary["positive_weeks"] == 1
    assert summary["win_rate"] == 1.0
    assert summary["avg_net_return_pct"] == 2.0


def test_backtest_summary_and_empty_ui_frame_do_not_crash() -> None:
    assert summarize_backtest_results([])["sample_weeks"] == 0
    assert weekend_spread._backtest_frame([]).empty


def test_weekend_spread_log_handles_empty_store(tmp_path) -> None:
    snapshot = get_weekly_log_snapshot(path=tmp_path / "missing.json", week_id="2026-W24")

    assert snapshot["sample_count"] == 0
    assert snapshot["summaries"] == []
    assert weekend_spread._summary_frame([]).empty
    assert weekend_spread._history_frame([]).empty


def test_weekend_spread_render_declares_workflow_tabs() -> None:
    source = inspect.getsource(weekend_spread.render)

    assert "st.tabs" in source
    assert [
        weekend_spread.TAB_REALTIME,
        weekend_spread.TAB_BACKTEST,
        weekend_spread.TAB_MAPPING,
    ] == ["实时观察", "历史回测", "映射管理"]
    assert "TAB_WEEKLY" not in source
    assert "TAB_MONDAY" not in source
    assert "TAB_HISTORY" not in source


def test_weekend_spread_refresh_path_shows_progress_feedback() -> None:
    source = inspect.getsource(weekend_spread._build_weekend_spread_rows_with_feedback)

    assert "st.progress" in source
    assert "progress_callback" in source
    assert "Refreshing Binance data" in source
    assert "Refresh complete" in source


def test_weekend_spread_initial_load_does_not_request_live_prices() -> None:
    source = inspect.getsource(weekend_spread._build_weekend_spread_rows_with_feedback)

    assert "provider=_IdleBinanceProvider()" in source
    assert "CachedAfterhoursProvider(NullAfterhoursProvider())" in source


def test_idle_provider_marks_rows_as_waiting_refresh() -> None:
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=weekend_spread._IdleBinanceProvider(),
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["status"] == "PRICE_NOT_LOADED"
    assert row["error"] == "price_not_loaded"
    assert row["alert_level_cn"] == "等待刷新"
    assert weekend_spread._binance_status_text(rows, 1) == "等待刷新"
    assert weekend_spread._market_price_source_status(rows, "usdm_futures") == "无请求"


def test_candidate_mapping_strongest_signal_warns_unconfirmed() -> None:
    rows = build_weekend_spread_rows(
        ["NVDA", "MSFT"],
        mapping=_mapping(mapping_confidence="candidate"),
        provider=FakeProvider(price=103),
        cache=FakeCache(),
    )

    strongest = weekend_spread._strongest_signal_row(rows)

    assert strongest is not None
    assert strongest["ticker"] == "NVDA"
    assert weekend_spread._strongest_signal_warning(strongest) == "映射未确认，不能作为正式套利信号。"


def test_realtime_ui_uses_row_details_not_standalone_detail_block() -> None:
    source = inspect.getsource(weekend_spread)

    assert "查看实时价差详情" not in source
    assert "行详情" in source


def test_candidate_mapping_is_excluded_from_backtest_by_default() -> None:
    source = inspect.getsource(weekend_spread._render_backtest_tab)

    assert "包含未确认映射" in source
    assert "weekend_backtest_include_unconfirmed" in source
    assert 'mapping_confidence") or "") == "confirmed"' in source


def test_market_price_source_status_uses_request_language() -> None:
    no_rows = build_weekend_spread_rows(["NVDA"], mapping=_explicit_empty_mapping(), provider=FakeProvider(), cache=FakeCache())
    futures_rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(), cache=FakeCache())

    assert weekend_spread._market_price_source_status(no_rows, "spot") == "无请求"
    assert weekend_spread._market_price_source_status(no_rows, "usdm_futures") == "无请求"
    assert weekend_spread._market_price_source_status(futures_rows, "usdm_futures") == "可用"


def test_live_frame_keeps_only_core_realtime_columns_and_shows_anchor() -> None:
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=104.58),
        afterhours_provider=FakeAfterhoursProvider(reference_price=102.88),
        cache=FakeCache(_history(close=102.15)),
    )

    frame = weekend_spread._live_frame(rows)

    assert list(frame.columns) == [
        "Ticker",
        "价格锚点",
        "Binance 最新",
        "vs 盘后",
        "vs 收盘",
        "状态",
        "风险",
        "更新时间",
    ]
    assert frame.loc[0, "价格锚点"] == "盘后 $102.88"
    assert frame.loc[0, "vs 盘后"] == "+1.65%"
    assert frame.loc[0, "vs 收盘"] == "+2.38%"
    assert "bid" not in frame.columns
    assert "ask" not in frame.columns
    assert "funding_rate" not in frame.columns
    assert "risk_note" not in frame.columns


def test_live_frame_marks_afterhours_missing_and_fallback() -> None:
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=104.58),
        afterhours_provider=FakeAfterhoursProvider(reference_price=None),
        cache=FakeCache(_history(close=102.15)),
    )

    frame = weekend_spread._live_frame(rows)

    assert "$102.15" in frame.iloc[0, 1]
    assert frame.iloc[0, 3] in {"—", "鈥?"}
    assert "fallback" in frame.iloc[0, 6]


def test_live_frame_formats_updated_at_as_short_hkt() -> None:
    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=FakeProvider(), cache=FakeCache())

    frame = weekend_spread._live_frame(rows)

    assert frame.loc[0, "更新时间"] == "06-14 20:00 HKT"
    assert "T12:" not in frame.loc[0, "更新时间"]


def test_candidate_mapping_risk_is_merged_into_risk_column() -> None:
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        provider=FakeProvider(price=104.58),
        cache=FakeCache(_history(close=102.15)),
    )

    frame = weekend_spread._live_frame(rows)

    assert "映射未确认，仅观察，不能作为正式交易信号" in frame.loc[0, "风险"]


def test_row_details_are_split_into_three_blocks() -> None:
    source = inspect.getsource(weekend_spread._render_row_details)

    assert "**盘后锚点**" in source
    assert "**Binance 行情**" in source
    assert "**风险说明**" in source


def test_no_mapping_frame_only_shows_minimal_columns() -> None:
    rows = build_weekend_spread_rows(["MSFT"], mapping=_explicit_empty_mapping(), provider=FakeProvider(), cache=FakeCache())

    frame = weekend_spread._no_mapping_frame(rows)

    assert list(frame.columns) == ["Ticker", "周五收盘", "收盘日期"]
    assert "Binance 最新" not in frame.columns
    assert "暂缺" not in frame.columns


def test_summary_frame_uses_recorded_peak_labels_not_realtime_labels() -> None:
    summaries = [
        {
            "ticker": "NVDA",
            "max_premium_pct": 1.5,
            "max_discount_pct": -0.8,
            "max_abs_spread_pct": 1.5,
            "max_abs_spread_direction": "Binance 溢价",
            "sample_count": 2,
            "data_quality": "OK",
        }
    ]

    frame = weekend_spread._summary_frame(summaries)

    assert "已记录最大溢价" in frame.columns
    assert "已记录最大折价" in frame.columns
    assert "本周最大溢价" not in frame.columns
    assert "本周最大折价" not in frame.columns


def test_monday_outcome_frame_uses_signal_validation_columns() -> None:
    summaries = [
        {
            "ticker": "NVDA",
            "max_abs_spread_pct": 1.5,
            "monday_gap_pct": 0.8,
            "direction_hit": True,
            "capture_ratio": 0.53,
            "net_edge_pct": 0.7,
            "outcome_status": "HIT",
        }
    ]

    frame = weekend_spread._monday_outcome_frame(summaries)

    assert list(frame.columns) == [
        "Ticker",
        "max_abs_spread_pct",
        "monday_gap_pct",
        "direction_hit",
        "capture_ratio",
        "net_edge_pct",
        "outcome_status",
    ]


def test_mapping_management_tab_counts_local_universe_confirmed_and_candidate() -> None:
    mapping = {
        "NVDA": _mapping()["NVDA"],
        "ADBE": {
            "enabled": True,
            "binance_symbol": "ADBEUSDT",
            "market_type": "usdm_futures",
            "quote_currency": "USDT",
            "unit_multiplier": 1,
            "mapping_confidence": "candidate",
            "risk_note": "candidate only",
        },
    }
    rows = build_weekend_spread_rows(["NVDA", "MSFT"], mapping=mapping, provider=FakeProvider(), cache=FakeCache())

    counts = weekend_spread._mapping_management_counts(rows, mapping)
    frame = weekend_spread._mapping_management_frame(rows, mapping)

    assert counts["local_mapping_count"] == 2
    assert counts["universe_mapping_count"] == 1
    assert counts["confirmed_count"] == 1
    assert counts["candidate_count"] == 1
    assert counts["no_mapping_count"] == 1
    assert "不在观察池" in set(frame["validation_status"])


def test_weekend_spread_ui_does_not_allow_manual_realtime_price_input() -> None:
    source = inspect.getsource(weekend_spread)

    assert "manual_override_price" not in source
    assert "Binance 手动价格" not in source
    assert "周一验证价" in source
