from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from data.afterhours_provider import AfterhoursReference, CachedAfterhoursProvider, NullAfterhoursProvider, resolve_afterhours_reference
from data.binance_provider import BinanceHTTPPriceProvider
from data.equity_afterhours_provider import (
    AlphaVantageAfterhoursProvider,
    MultiProviderAfterhoursProvider,
    PolygonOpenCloseAfterhoursProvider,
    PolygonQuoteMidAfterhoursProvider,
    PolygonTradesAfterhoursProvider,
)
from data.overnight_price_provider import (
    AlpacaBoatsOvernightProvider,
    JsonFileOvernightProvider,
    build_overnight_provider_self_check,
    default_overnight_price_provider,
)
from data.weekend_basis import (
    BasisStrategyConfig,
    build_basis_opportunity,
    close_weekend_basis_trade,
    create_weekend_basis_trade,
    evaluate_open_trade,
    max_broker_buy_price,
    record_broker_hedge,
)
from data.weekend_basis_mapping_audit import (
    audit_weekend_basis_mappings,
    confirm_weekend_basis_mapping,
    reject_weekend_basis_mapping,
)
from data.weekend_spread_backtest import (
    build_weekend_backtest_preflight,
    clear_backtest_view_state,
    get_first_valid_stock_bar_after_weekend,
    load_backtest_results,
    recent_weekend_windows,
    run_weekend_basis_backfill_audit,
    run_weekend_basis_backtest,
    run_weekend_peak_short_backtest,
    save_backtest_results,
    summarize_backfill_audit_results,
    summarize_backtest_results,
)
from data.tradingview_price_cache import (
    EVENT_FRIDAY_AFTERHOURS_CLOSE,
    EVENT_OVERNIGHT_FIRST_1M_CLOSE,
    import_tradingview_csv_file,
    record_tradingview_webhook,
    find_friday_afterhours_close,
    find_overnight_first_1m_close,
    load_price_cache,
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


class FakePolygonOpenCloseProvider(PolygonOpenCloseAfterhoursProvider):
    def __init__(self, payload: dict, *, api_key: str = "polygon-key") -> None:
        super().__init__(api_key=api_key, base_url="https://polygon.test")
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict:
        self.calls.append((endpoint, dict(params)))
        return dict(self.payload)


class FakePolygonTradesProvider(PolygonTradesAfterhoursProvider):
    def __init__(self, results: list[dict], *, api_key: str = "polygon-key") -> None:
        super().__init__(api_key=api_key, base_url="https://polygon.test")
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict:
        self.calls.append((endpoint, dict(params)))
        return {"results": list(self.results)}


class FakePolygonQuotesProvider(PolygonQuoteMidAfterhoursProvider):
    def __init__(self, results: list[dict], *, api_key: str = "polygon-key") -> None:
        super().__init__(api_key=api_key, base_url="https://polygon.test")
        self.results = results
        self.calls: list[tuple[str, dict]] = []

    def _get_json(self, endpoint: str, params: dict[str, str]) -> dict:
        self.calls.append((endpoint, dict(params)))
        return {"results": list(self.results)}


class FakeAlphaVantageProvider(AlphaVantageAfterhoursProvider):
    def __init__(self, payload: dict, *, api_key: str = "alpha-key") -> None:
        super().__init__(api_key=api_key, base_url="https://alpha.test/query")
        self.payload = payload
        self.calls: list[dict[str, str]] = []

    def _get_json(self, params: dict[str, str]) -> dict:
        self.calls.append(dict(params))
        return dict(self.payload)


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


class FakeMappingAuditProvider(FakeKlineProvider):
    def __init__(
        self,
        bars: list[list] | None = None,
        *,
        exists: bool = True,
        volume_24h: float | None = 250_000,
        bid: float | None = 101.0,
        ask: float | None = 101.1,
    ) -> None:
        super().__init__(bars or [])
        self.exists = exists
        self.volume_24h = volume_24h
        self.bid = bid
        self.ask = ask

    def validate_symbol(self, symbol: str, *, market_type: str = "usdm_futures") -> dict:
        self.calls.append({"symbol": symbol, "market_type": market_type, "kind": "validate"})
        return {
            "symbol": symbol,
            "exists": self.exists,
            "status": "valid" if self.exists else "invalid_symbol",
            "market_type": market_type,
            "quote_currency": "USDT",
        }

    def get_last_price(self, symbol: str, *, market_type: str = "usdm_futures", force_refresh: bool = False) -> dict:
        self.calls.append({"symbol": symbol, "market_type": market_type, "kind": "last_price"})
        return {
            "symbol": symbol,
            "last_price": self.bid,
            "bid": self.bid,
            "ask": self.ask,
            "volume_24h": self.volume_24h,
            "updated_at": "2026-06-14T12:00:00+00:00",
        }


class FakeBasisQuoteProvider:
    def __init__(self, quotes: list[dict] | None = None, *, error: Exception | None = None) -> None:
        self.quotes = quotes or []
        self.error = error
        self.calls: list[dict] = []

    def get_basis_quotes(
        self,
        symbol: str,
        *,
        market_type: str = "usdm_futures",
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[dict]:
        self.calls.append(
            {
                "symbol": symbol,
                "market_type": market_type,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
            }
        )
        if self.error:
            raise self.error
        start = start_time_ms or 0
        end = end_time_ms or 2**63 - 1
        return [
            quote
            for quote in self.quotes
            if start <= int(datetime.fromisoformat(str(quote["ts"]).replace("Z", "+00:00")).timestamp() * 1000) < end
        ]


class FakeBrokerBarProvider:
    def __init__(self, bars: dict[str, list[dict]] | None = None) -> None:
        self.bars = bars or {}
        self.calls: list[dict] = []

    def get_overnight_bars(
        self,
        symbol: str,
        *,
        start_time_ms: int,
        end_time_ms: int,
        interval: str = "1m",
    ) -> list[dict]:
        self.calls.append(
            {
                "symbol": symbol,
                "start_time_ms": start_time_ms,
                "end_time_ms": end_time_ms,
                "interval": interval,
            }
        )
        rows = self.bars.get(interval, [])
        return [
            row
            for row in rows
            if start_time_ms
            <= int(datetime.fromisoformat(str(row["ts"]).replace("Z", "+00:00")).timestamp() * 1000)
            < end_time_ms
        ]


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


def _basis_quote(moment: datetime, bid: float, ask: float | None = None, **extra) -> dict:
    return {
        "ts": moment.astimezone(timezone.utc).isoformat(),
        "bid": bid,
        "ask": ask if ask is not None else bid + 0.02,
        **extra,
    }


def _broker_bar(moment: datetime, bid: float, ask: float | None = None, **extra) -> dict:
    effective_ask = ask if ask is not None else bid + 0.02
    return {
        "ts": moment.astimezone(timezone.utc).isoformat(),
        "bid": bid,
        "ask": effective_ask,
        "close": extra.pop("close", effective_ask),
        "quote_age_seconds": 10,
        **extra,
    }


def _audit_broker_history(prices: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": date_text, "close": price} for date_text, price in prices]
    )


def _audit_binance_daily_bars(prices: list[tuple[str, float]]) -> list[list]:
    rows = []
    for date_text, price in prices:
        ts = int(datetime.fromisoformat(f"{date_text}T19:59:00+00:00").timestamp() * 1000)
        rows.append([ts, price, price, price, price, 1000])
    return rows


def _audit_weekend_bars(price: float = 101.0) -> list[list]:
    sunday = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
    ts = int(sunday.timestamp() * 1000)
    return [[ts, price, price + 1, price - 1, price, 1000]]


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
    payload = {"afterhours_reference_price": afterhours, "regular_close_price": regular}
    if afterhours is not None:
        payload["afterhours_reference_time"] = "2026-07-03T19:59:00-04:00"
        payload["afterhours_reference_source"] = "mock_afterhours"
    return {"NVDA": payload}


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


def test_fmp_afterhours_millisecond_timestamp_must_match_regular_close_date() -> None:
    valid = resolve_afterhours_reference(
        "NVDA",
        trade={"price": 205.42, "timestamp": "1781308799000"},
        regular_close_date="2026-06-12",
    )
    stale = resolve_afterhours_reference(
        "NVDA",
        trade={"price": 211.94, "timestamp": "1781567999000"},
        regular_close_date="2026-06-12",
    )
    stale_quote = resolve_afterhours_reference(
        "NVDA",
        trade={},
        quote={"bid": 211.8, "ask": 212.0, "timestamp": "1781567999000"},
        regular_close_date="2026-06-12",
    )

    assert valid.reference_price == 205.42
    assert valid.data_quality == "HIGH"
    assert stale.reference_price is None
    assert stale.missing_reason == "NO_AFTERHOURS_TRADE"
    assert stale_quote.reference_price is None
    assert stale_quote.missing_reason == "NO_AFTERHOURS_QUOTE"


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


def test_afterhours_cache_write_permission_error_does_not_break_live_reference(tmp_path, monkeypatch) -> None:
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
    path_type = type(cache_path)
    original_replace = path_type.replace

    def locked_replace(self, target):
        if str(self).endswith(".tmp"):
            raise PermissionError("[WinError 5] 拒绝访问")
        return original_replace(self, target)

    monkeypatch.setattr(path_type, "replace", locked_replace)

    snapshot = CachedAfterhoursProvider(live_provider, cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )

    assert snapshot.reference_price == 208
    assert snapshot.cache_status == "API_LIVE"
    assert not list(tmp_path.glob("*.tmp"))


def test_afterhours_corrupt_cache_reports_cache_corrupt(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    cache_path.write_text("not valid json", encoding="utf-8")

    cached = CachedAfterhoursProvider(NullAfterhoursProvider(), cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )

    assert cached.reference_price is None
    assert cached.cache_status == "CACHE_CORRUPT"
    assert cached.missing_reason == "CACHE_CORRUPT"
    assert "JSONDecodeError" in cached.error_message


def test_afterhours_corrupt_cache_recovers_reference_entries(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    cache_path.write_text(
        '{"2026-W24:2026-06-12:CEG": {"symbol": "CEG", "reference_price": 260, "data_quality": "HIGH"}}'
        '  "NVDA:2026-06-12": {"symbol": "NVDA", "reference_price": 208, '
        '"reference_time": "2026-06-12T19:58:00-04:00", "reference_source": "FMP_AFTERHOURS_TRADE", "data_quality": "HIGH"}',
        encoding="utf-8",
    )

    cached = CachedAfterhoursProvider(NullAfterhoursProvider(), cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )

    assert cached.reference_price == 208
    assert cached.cache_status == "CACHE_HIT"


def test_afterhours_cache_skips_date_mismatched_primary_and_uses_legacy(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    payload = {
        "2026-W24:2026-06-12:NVDA": {
            "symbol": "NVDA",
            "reference_price": 211.94,
            "reference_time": "1781567999000",
            "reference_source": "FMP_AFTERHOURS_TRADE",
            "data_quality": "MEDIUM",
        },
        "NVDA:2026-06-12": {
            "symbol": "NVDA",
            "reference_price": 205.42,
            "reference_time": "1781308799000",
            "reference_source": "FMP_AFTERHOURS_TRADE",
            "data_quality": "HIGH",
        },
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    cached = CachedAfterhoursProvider(NullAfterhoursProvider(), cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )

    assert cached.reference_price == 205.42
    assert cached.cache_status == "CACHE_HIT"


def test_afterhours_cache_date_mismatch_reports_clear_reason(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    payload = {
        "2026-W24:2026-06-12:NVDA": {
            "symbol": "NVDA",
            "reference_price": 211.94,
            "reference_time": "1781567999000",
            "reference_source": "FMP_AFTERHOURS_TRADE",
            "data_quality": "MEDIUM",
        },
    }
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    cached = CachedAfterhoursProvider(NullAfterhoursProvider(), cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )

    assert cached.reference_price is None
    assert cached.cache_status == "CACHE_DATE_MISMATCH"
    assert cached.missing_reason == "CACHE_DATE_MISMATCH"


def test_afterhours_legacy_cache_key_is_migrated_with_anchor_metadata(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    legacy_payload = {
        "NVDA:2026-06-12": {
            "symbol": "NVDA",
            "reference_price": 208,
            "reference_time": "2026-06-12T19:58:00-04:00",
            "reference_source": "FMP_AFTERHOURS_TRADE",
            "data_quality": "HIGH",
        }
    }
    cache_path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    cached = CachedAfterhoursProvider(SequenceAfterhoursProvider([]), cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2026-06-12",
    )
    migrated = json.loads(cache_path.read_text(encoding="utf-8"))

    assert cached.reference_price == 208
    assert cached.cache_status == "CACHE_HIT"
    assert cached.week_id == "2026-W24"
    assert cached.friday_date == "2026-06-12"
    assert cached.anchor_status == "FINAL"
    assert cached.provider_name == "FMP"
    assert "2026-W24:2026-06-12:friday_afterhours_close:sequenceafterhoursprovider:NVDA" in migrated


def test_afterhours_anchor_cache_uses_week_friday_ticker_metadata(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    provider = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=208,
                reference_time="2000-01-07T19:58:00-05:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                data_quality="HIGH",
            )
        ]
    )

    snapshot = CachedAfterhoursProvider(provider, cache_path=cache_path).get_afterhours_reference(
        "NVDA",
        regular_close_date="2000-01-07",
    )
    payload = json.loads(cache_path.read_text(encoding="utf-8"))

    assert "2000-W01:2000-01-07:friday_afterhours_close:sequenceafterhoursprovider:NVDA" in payload
    assert snapshot.week_id == "2000-W01"
    assert snapshot.friday_date == "2000-01-07"
    assert snapshot.anchor_status == "FINAL"
    assert snapshot.finalized_at
    assert snapshot.fetched_at
    assert snapshot.provider_name == "FMP"


def test_binance_refresh_reuses_afterhours_anchor_cache_without_provider_fetch(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    live_provider = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=208,
                reference_time="2026-06-12T19:58:00-04:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                data_quality="HIGH",
            ),
            AfterhoursReference(
                symbol="NVDA",
                reference_price=209,
                reference_time="2026-06-12T19:59:00-04:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                data_quality="HIGH",
            ),
        ]
    )
    cached_provider = CachedAfterhoursProvider(live_provider, cache_path=cache_path)
    cached_provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=210),
        afterhours_provider=cached_provider,
        afterhours_force_refresh=False,
        force_refresh=True,
        cache=FakeCache(_history(close=205)),
    )

    assert rows[0]["afterhours_reference_price"] == 208
    assert rows[0]["afterhours_cache_status"] == "CACHE_HIT"
    assert len(live_provider.calls) == 1


def test_anchor_refresh_can_force_rebuild_afterhours_cache(tmp_path) -> None:
    cache_path = tmp_path / "afterhours_reference_cache.json"
    live_provider = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=208,
                reference_time="2026-06-12T19:58:00-04:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                data_quality="HIGH",
            ),
            AfterhoursReference(
                symbol="NVDA",
                reference_price=209,
                reference_time="2026-06-12T19:59:00-04:00",
                reference_source="FMP_AFTERHOURS_TRADE",
                data_quality="HIGH",
            ),
        ]
    )
    cached_provider = CachedAfterhoursProvider(live_provider, cache_path=cache_path)
    cached_provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=210),
        afterhours_provider=cached_provider,
        afterhours_force_refresh=True,
        cache=FakeCache(_history(close=205)),
    )

    assert rows[0]["afterhours_reference_price"] == 209
    assert rows[0]["afterhours_cache_status"] == "API_LIVE"
    assert len(live_provider.calls) == 2


def test_polygon_open_close_afterhours_provider_uses_afterhours_price() -> None:
    provider = FakePolygonOpenCloseProvider({"afterHours": 208.5})

    snapshot = provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert snapshot.reference_price == 208.5
    assert snapshot.reference_source == "POLYGON_OPEN_CLOSE_AFTERHOURS"
    assert snapshot.data_quality == "HIGH"
    assert snapshot.missing_reason == ""
    assert provider.calls[0][0] == "v1/open-close/NVDA/2026-06-12"


def test_polygon_trades_provider_prefers_1955_to_2000_trade() -> None:
    provider = FakePolygonTradesProvider(
        [
            {"price": 207.0, "sip_timestamp": "2026-06-12T19:30:00-04:00", "size": 100},
            {"price": 208.25, "sip_timestamp": "2026-06-12T19:58:00-04:00", "size": 200},
        ]
    )

    snapshot = provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert snapshot.reference_price == 208.25
    assert snapshot.reference_source == "POLYGON_TRADES_1955_2000"
    assert snapshot.data_quality == "HIGH"
    assert snapshot.volume == 200


def test_polygon_quote_mid_provider_uses_valid_quote_mid() -> None:
    provider = FakePolygonQuotesProvider(
        [
            {"bid_price": 207.8, "ask_price": 208.2, "sip_timestamp": "2026-06-12T19:58:00-04:00", "size": 10},
        ]
    )

    snapshot = provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert snapshot.reference_price == 208.0
    assert snapshot.reference_source == "POLYGON_QUOTE_MID"
    assert snapshot.bid == 207.8
    assert snapshot.ask == 208.2
    assert snapshot.data_quality == "HIGH"


def test_multi_provider_falls_back_to_fmp_after_polygon_misses() -> None:
    polygon = SequenceAfterhoursProvider([AfterhoursReference(symbol="NVDA", data_quality="MISSING", missing_reason="NO_AFTERHOURS_TRADE")])
    fmp = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=208,
                reference_source="FMP_AFTERHOURS_TRADE",
                reference_time="2026-06-12T19:58:00-04:00",
                data_quality="HIGH",
            )
        ]
    )
    provider = MultiProviderAfterhoursProvider([polygon, fmp])

    snapshot = provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert snapshot.reference_price == 208
    assert snapshot.reference_source == "FMP_AFTERHOURS_TRADE"


def test_multi_provider_falls_back_to_alphavantage_after_prior_misses() -> None:
    alpha = FakeAlphaVantageProvider(
        {
            "Time Series (1min)": {
                "2026-06-12 19:59:00": {"4. close": "208.75", "5. volume": "900"},
            }
        }
    )
    provider = MultiProviderAfterhoursProvider(
        [
            SequenceAfterhoursProvider([AfterhoursReference(symbol="NVDA", data_quality="MISSING", missing_reason="API_KEY_MISSING")]),
            alpha,
        ]
    )

    snapshot = provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert snapshot.reference_price == 208.75
    assert snapshot.reference_source == "ALPHAVANTAGE_INTRADAY_EXTENDED"
    assert snapshot.data_quality == "HIGH"


def test_afterhours_provider_api_key_missing_does_not_crash() -> None:
    provider = PolygonOpenCloseAfterhoursProvider(api_key="polygon-key")
    provider.api_key = ""

    snapshot = provider.get_afterhours_reference("NVDA", regular_close_date="2026-06-12")

    assert snapshot.reference_price is None
    assert snapshot.missing_reason == "API_KEY_MISSING"


def test_all_afterhours_providers_missing_keeps_regular_close_fallback() -> None:
    provider = MultiProviderAfterhoursProvider(
        [
            SequenceAfterhoursProvider([AfterhoursReference(symbol="NVDA", data_quality="MISSING", missing_reason="API_KEY_MISSING")]),
            SequenceAfterhoursProvider([AfterhoursReference(symbol="NVDA", data_quality="MISSING", missing_reason="NO_AFTERHOURS_TRADE")]),
        ]
    )

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(),
        provider=FakeProvider(price=210),
        afterhours_provider=provider,
        cache=FakeCache(_history(close=205)),
    )

    assert rows[0]["afterhours_reference_price"] is None
    assert rows[0]["afterhours_missing_reason"] == "NO_AFTERHOURS_TRADE"
    assert rows[0]["primary_spread_anchor"] == "REGULAR_CLOSE_FALLBACK"


def test_ui_maps_afterhours_source_and_quality_text() -> None:
    assert weekend_spread._afterhours_source_text("POLYGON_OPEN_CLOSE_AFTERHOURS").startswith("Polygon/Massive")
    assert weekend_spread._afterhours_source_text("ALPHAVANTAGE_INTRADAY_EXTENDED").startswith("Alpha Vantage")
    assert weekend_spread._afterhours_reason_text("CACHE_CORRUPT") == "盘后缓存损坏"
    assert weekend_spread._afterhours_cache_text("CACHE_CORRUPT") == "盘后缓存损坏"
    assert weekend_spread._afterhours_reason_text("CACHE_DATE_MISMATCH") == "盘后缓存日期不匹配"
    assert weekend_spread._afterhours_cache_text("CACHE_DATE_MISMATCH") == "盘后缓存日期不匹配"


def test_backtest_error_message_aggregates_missing_stock_first_bar() -> None:
    rows = [
        {"ticker": "NVDA", "data_quality": "MISSING_STOCK_FIRST_BAR", "error_message": "MISSING_STOCK_FIRST_BAR"},
        {"ticker": "NVDA", "data_quality": "MISSING_STOCK_FIRST_BAR", "error_message": "MISSING_STOCK_FIRST_BAR"},
        {"ticker": "NVDA", "data_quality": "MISSING_STOCK_FIRST_BAR", "error_message": "MISSING_STOCK_FIRST_BAR"},
        {"ticker": "NVDA", "data_quality": "MISSING_STOCK_FIRST_BAR", "error_message": "MISSING_STOCK_FIRST_BAR"},
    ]

    message = weekend_spread._backtest_error_message(rows)

    assert "NVDA：近 4 周均缺少美股端第一根有效 1m bar，已排除 4 个样本" in message


def test_weekend_review_empty_reason_names_missing_stock_first_bar() -> None:
    reason = weekend_spread._weekend_review_empty_reason(
        [
            {
                "ticker": "NVDA",
                "data_quality": "STOCK_MISSING",
                "raw_row": {"data_quality": "MISSING_STOCK_FIRST_BAR"},
            }
        ]
    )

    assert "缺少券商周一夜盘 20:00 ET 第一根 1m bar" in reason
    assert "IBKR 夜盘历史 1m 数据权限" in reason


def test_historical_weekly_anchor_uses_afterhours_reference() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    cache = FakeCache(pd.DataFrame([{"date": "2026-07-03", "close": 100.0}]))
    provider = FakeAfterhoursProvider(
        reference_price=102.5,
        reference_time="2026-07-03T19:58:00-04:00",
        reference_source="mock_afterhours",
        cache_status="API_LIVE",
    )
    anchors: dict[str, dict] = {}

    weekend_spread._merge_historical_weekly_anchors(
        anchors,
        ["NVDA"],
        weeks=1,
        cache=cache,
        afterhours_provider=provider,
        now=now,
    )

    weekly = anchors["NVDA"]["weekly_anchors"][window.week_id]
    assert weekly["regular_close_price"] == 100.0
    assert weekly["afterhours_reference_price"] == 102.5
    assert weekly["afterhours_reference_time"] == "2026-07-03T19:58:00-04:00"
    assert weekly["anchor_source"] == "HISTORICAL_AFTERHOURS_REFERENCE"
    assert provider.calls == [("NVDA", "2026-07-03", False)]


def test_historical_weekly_anchor_falls_back_to_regular_close_when_afterhours_missing() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    cache = FakeCache(pd.DataFrame([{"date": "2026-07-03", "close": 100.0}]))
    provider = FakeAfterhoursProvider(
        reference_price=None,
        data_quality="MISSING",
        missing_reason="NO_AFTERHOURS_TRADE",
        cache_status="CACHE_MISSING",
    )
    anchors: dict[str, dict] = {}

    weekend_spread._merge_historical_weekly_anchors(
        anchors,
        ["NVDA"],
        weeks=1,
        cache=cache,
        afterhours_provider=provider,
        now=now,
    )

    weekly = anchors["NVDA"]["weekly_anchors"][window.week_id]
    assert weekly["regular_close_price"] == 100.0
    assert weekly["afterhours_reference_price"] is None
    assert weekly["afterhours_missing_reason"] == "NO_AFTERHOURS_TRADE"
    assert weekly["afterhours_cache_status"] == "CACHE_MISSING"
    assert weekly["anchor_source"] == "HISTORICAL_REGULAR_CLOSE"


def test_historical_weekly_anchor_reads_distinct_afterhours_prices_per_friday() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    cache = FakeCache(
        pd.DataFrame(
            [
                {"date": "2026-06-12", "close": 100.0},
                {"date": "2026-06-19", "close": 110.0},
                {"date": "2026-06-26", "close": 120.0},
                {"date": "2026-07-03", "close": 130.0},
            ]
        )
    )
    provider = SequenceAfterhoursProvider(
        [
            AfterhoursReference(
                symbol="NVDA",
                reference_price=131.0,
                reference_time="2026-07-03T19:58:00-04:00",
                reference_source="mock_afterhours",
                data_quality="HIGH",
            ),
            AfterhoursReference(
                symbol="NVDA",
                reference_price=121.0,
                reference_time="2026-06-26T19:58:00-04:00",
                reference_source="mock_afterhours",
                data_quality="HIGH",
            ),
            AfterhoursReference(
                symbol="NVDA",
                reference_price=111.0,
                reference_time="2026-06-19T19:58:00-04:00",
                reference_source="mock_afterhours",
                data_quality="HIGH",
            ),
            AfterhoursReference(
                symbol="NVDA",
                reference_price=101.0,
                reference_time="2026-06-12T19:58:00-04:00",
                reference_source="mock_afterhours",
                data_quality="HIGH",
            ),
        ]
    )
    anchors: dict[str, dict] = {}

    weekend_spread._merge_historical_weekly_anchors(
        anchors,
        ["NVDA"],
        weeks=4,
        cache=cache,
        afterhours_provider=provider,
        now=now,
    )

    weekly = anchors["NVDA"]["weekly_anchors"]
    prices = {anchor["regular_close_date"]: anchor["afterhours_reference_price"] for anchor in weekly.values()}

    assert prices == {
        "2026-06-12": 101.0,
        "2026-06-19": 111.0,
        "2026-06-26": 121.0,
        "2026-07-03": 131.0,
    }
    assert provider.calls == [
        ("NVDA", "2026-07-03", False),
        ("NVDA", "2026-06-26", False),
        ("NVDA", "2026-06-19", False),
        ("NVDA", "2026-06-12", False),
    ]


def test_weekend_basis_backtest_uses_historical_afterhours_anchor() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    cache = FakeCache(pd.DataFrame([{"date": "2026-07-03", "close": 99.0}]))
    provider = FakeAfterhoursProvider(
        reference_price=100.0,
        reference_time="2026-07-03T19:58:00-04:00",
        reference_source="mock_afterhours",
        cache_status="API_LIVE",
    )
    anchors: dict[str, dict] = {}
    weekend_spread._merge_historical_weekly_anchors(
        anchors,
        ["NVDA"],
        weeks=1,
        cache=cache,
        afterhours_provider=provider,
        now=now,
    )
    quotes = [
        _basis_quote(window.start_et + timedelta(minutes=1), 100.5, 100.55),
        _basis_quote(window.start_et + timedelta(hours=2), 101.0, 101.05),
        _basis_quote(window.start_et + timedelta(hours=4), 102.0, 102.05),
        _basis_quote(window.start_et + timedelta(hours=5), 101.65, 101.7),
        _basis_quote(window.end_et, 101.1, 101.2),
    ]
    broker = FakeBrokerBarProvider({"1m": [_broker_bar(window.end_et, 100.8, 100.9)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        broker_provider=broker,
        weeks=1,
        now=now,
        opening_anchor="overnight",
        open_window_minutes=2,
    )

    assert rows[0]["status"] == "HEDGE_LOCKED"
    assert rows[0]["anchor_price"] == 100.0
    assert rows[0]["anchor_source"] == "HISTORICAL_AFTERHOURS_REFERENCE"
    assert rows[0]["anchor_ts"] == "2026-07-03T19:58:00-04:00"
    assert rows[0]["afterhours_reference_price"] == 100.0


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


def test_legacy_spot_stock_mapping_is_normalized_to_usdm_futures() -> None:
    provider = FakeProvider(price=101.5, funding_rate=None)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        provider=provider,
        cache=FakeCache(),
    )

    assert rows[0]["status"] == "OK"
    assert rows[0]["binance_market_type"] == "usdm_futures"
    assert rows[0]["binance_last_price"] == 101.5
    assert provider.calls == ["usdm_futures:NVDAUSDT"]


def test_legacy_spot_stock_mapping_does_not_use_spot_price_path() -> None:
    provider = SpotSymbolSpecificProvider()

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["status"] == "OK"
    assert row["binance_market_type"] == "usdm_futures"
    assert all(call[0] != "https://spot.test" for call in provider.calls)


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
    assert row["market_type"] == "usdm_futures"
    assert row["exists"] is True
    assert row["last_price"] == 207
    assert row["bid"] == 206.8
    assert row["ask"] == 207.2
    assert row["volume_24h"] == 123_456
    assert provider.calls == ["validate:usdm_futures:ADBEUSDT", "usdm_futures:ADBEUSDT"]


def test_smoke_script_cli_defaults_to_usdm_futures_for_stock_mapping() -> None:
    source = inspect.getsource(smoke_binance_provider.main)

    assert 'default="usdm_futures"' in source
    assert 'choices=["usdm_futures"]' in source


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
    assert "股票代码到 Binance 合约代码" in message
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


def test_weekend_basis_backtest_locks_hedge_with_bid_entry_and_broker_ask() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    quotes = [
        _basis_quote(window.start_et + timedelta(minutes=1), 100.5, 100.55),
        _basis_quote(window.start_et + timedelta(hours=2), 101.0, 101.05),
        _basis_quote(window.start_et + timedelta(hours=4), 102.0, 102.05),
        _basis_quote(window.start_et + timedelta(hours=5), 101.65, 101.7),
        _basis_quote(window.end_et, 101.1, 101.2),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "afterhours_reference_time": "2026-07-03T19:59:00-04:00",
        }
    }
    broker = FakeBrokerBarProvider({"1m": [_broker_bar(window.end_et, 100.8, 100.9)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        broker_provider=broker,
        weeks=1,
        now=now,
        opening_anchor="overnight",
    )

    row = rows[0]
    assert row["status"] == "HEDGE_LOCKED"
    assert row["binance_entry_bid"] == 101.65
    assert row["broker_hedge_ask"] == 100.9
    assert round(row["entry_premium_bps"], 2) == 165.0
    assert round(row["pullback_bps"], 2) == 35.0
    assert round(row["net_locked_bps"], 2) == 74.33
    assert row["realized_pnl_bps"] is None
    assert row["oracle_note"] == "事后高点，不可交易"


def test_weekend_basis_backtest_keeps_anchor_observation_when_stock_opening_bars_missing() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=1), 100.5, 100.5, 100.5, 100.5),
        _kline(window.start_et + timedelta(hours=2), 101.0, 101.0, 101.0, 101.0),
        _kline(window.start_et + timedelta(hours=4), 102.0, 102.0, 102.0, 102.0),
        _kline(window.start_et + timedelta(hours=5), 101.65, 101.65, 101.65, 101.65),
    ]

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        weeks=1,
        now=now,
    )

    assert rows[0]["status"] == "OBSERVE"
    assert rows[0]["data_quality"] == "OBSERVE_ANCHOR_ONLY"
    assert rows[0]["stock_bar_reason"] == "OVERNIGHT_PROVIDER_MISSING"
    assert rows[0]["transmission_data_quality"] == "OVERNIGHT_PROVIDER_MISSING"
    assert rows[0]["stock_bar_returned_count"] == 0
    assert rows[0]["oracle_weekend_high_bid"] == 102.0
    assert rows[0]["binance_weekend_max_price"] == 102.0
    assert summarize_backtest_results(rows)["sample_weeks"] == 0
    assert summarize_backtest_results(rows)["observe_sample_count"] == 1

    review_rows = weekend_spread._weekend_review_rows(rows)
    review_summary = weekend_spread._weekend_review_summary(review_rows)

    assert review_rows[0]["data_quality"] == "OVERNIGHT_PROVIDER_MISSING"
    assert review_rows[0]["friday_afterhours_close"] == 100
    assert review_rows[0]["binance_price"] == 102.0
    assert review_rows[0]["overnight_provider"] == "美股夜盘数据源未配置"
    assert review_summary["summary_quality"] == "NONE"
    assert review_summary["sample_count"] == 0
    frame = weekend_spread._weekend_review_frame(review_rows)
    assert frame.iloc[0]["P2 来源"] == "美股夜盘数据源未配置"
    assert "anchor_source" not in frame.to_string()
    assert weekend_spread._display_weekend_review_rows(review_rows)


def test_weekend_basis_backtest_uses_binance_high_and_broker_first_1m_close() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=1), 100.0, 101.0, 99.5, 100.2),
        _kline(window.start_et + timedelta(hours=10), 102.0, 106.0, 101.5, 102.5),
        _kline(window.end_et, 99.0, 110.0, 98.0, 109.0),
    ]
    broker_provider = FakeBrokerBarProvider({"1m": [_broker_bar(window.end_et, 105.4, 105.8, close=105.6)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        broker_provider=broker_provider,
        weeks=1,
        now=now,
        opening_anchor="overnight",
        allow_anchor_fallback=False,
        require_exact_broker_open=True,
    )

    row = rows[0]
    assert row["binance_weekend_max_price"] == 106.0
    assert row["binance_kline_count"] == 2
    assert row["broker_first_1m_close"] == 105.6

    review = weekend_spread._weekend_review_rows(rows)[0]
    assert round(review["binance_premium_pct"], 2) == 6.0
    assert round(review["overnight_vs_binance_pct"], 2) == -0.38
    assert round(review["overnight_vs_afterhours_pct"], 2) == 5.6
    assert round(review["capture_pct"], 2) == 93.33
    assert review["data_quality"] == "OK"


def test_weekend_basis_backtest_rejects_non_exact_broker_first_minute_for_formal_sample() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [_kline(window.start_et + timedelta(hours=2), 100.0, 102.0, 99.0, 101.0)]
    broker_provider = FakeBrokerBarProvider({"1m": [_broker_bar(window.end_et + timedelta(minutes=1), 101.0, 101.2, close=101.1)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        broker_provider=broker_provider,
        weeks=1,
        now=now,
        opening_anchor="overnight",
        allow_anchor_fallback=False,
        require_exact_broker_open=True,
    )

    row = rows[0]
    assert row["binance_weekend_max_price"] == 102.0
    assert row["broker_first_1m_close"] is None
    assert row["stock_bar_reason"] == "MISSING_STOCK_FIRST_BAR"

    review = weekend_spread._weekend_review_rows(rows)[0]
    assert review["data_quality"] == "MISSING_OVERNIGHT_FIRST_1M"
    assert review["status"] == "仅观察"


def test_first_valid_stock_bar_falls_back_from_overnight_to_premarket() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    premarket = datetime.combine(window.end_et.date() + timedelta(days=1), datetime.min.time(), window.end_et.tzinfo)
    premarket = premarket.replace(hour=4, minute=3)
    provider = FakeBrokerBarProvider({"1m": [_broker_bar(premarket, 100.8, 100.9)]})

    result = get_first_valid_stock_bar_after_weekend(
        "NVDA",
        window.end_et.date() + timedelta(days=1),
        "overnight",
        30,
        broker_provider=provider,
    )

    assert result["ok"] is True
    assert result["price"] == 100.9
    assert result["anchor"] == "premarket"
    assert result["quality"] == "OK"
    assert result["provider"] == "broker"
    assert result["bar_size"] == "1m"


def test_first_valid_stock_bar_can_disable_anchor_fallback() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    premarket = datetime.combine(window.end_et.date() + timedelta(days=1), datetime.min.time(), window.end_et.tzinfo)
    premarket = premarket.replace(hour=4, minute=3)
    provider = FakeBrokerBarProvider({"1m": [_broker_bar(premarket, 100.8, 100.9)]})

    result = get_first_valid_stock_bar_after_weekend(
        "NVDA",
        window.end_et.date() + timedelta(days=1),
        "overnight",
        30,
        broker_provider=provider,
        allow_anchor_fallback=False,
    )

    assert result["ok"] is False
    assert result["anchor"] == "overnight"
    assert result["returned_bar_count"] == 0


def test_first_valid_stock_bar_requires_close_for_formal_sample() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    overnight = datetime.combine(window.end_et.date(), datetime.min.time(), window.end_et.tzinfo).replace(hour=20, minute=1)
    provider = FakeBrokerBarProvider(
        {
            "1m": [
                {
                    "ts": overnight.astimezone(timezone.utc).isoformat(),
                    "bid": 100.8,
                    "ask": 100.9,
                    "quote_age_seconds": 10,
                }
            ]
        }
    )

    result = get_first_valid_stock_bar_after_weekend(
        "NVDA",
        window.end_et.date() + timedelta(days=1),
        "overnight",
        30,
        broker_provider=provider,
        allow_anchor_fallback=False,
    )

    assert result["ok"] is False
    assert result["returned_bar_count"] == 1
    assert result["reason"] == "MISSING_STOCK_FIRST_BAR"


def test_overnight_anchor_skips_memorial_day_to_next_session() -> None:
    memorial_day = datetime(2026, 5, 25, tzinfo=ZoneInfo("America/New_York")).date()
    next_overnight = datetime(2026, 5, 25, 20, 1, tzinfo=ZoneInfo("America/New_York"))
    provider = FakeBrokerBarProvider({"1m": [_broker_bar(next_overnight, 100.8, 100.9, close=100.85)]})

    result = get_first_valid_stock_bar_after_weekend(
        "NVDA",
        memorial_day,
        "overnight",
        30,
        broker_provider=provider,
        allow_anchor_fallback=False,
    )

    assert result["ok"] is True
    assert result["price"] == 100.85
    assert result["requested_start"] == next_overnight.replace(minute=0).astimezone(timezone.utc).isoformat()


def test_weekend_basis_backtest_does_not_use_premarket_as_formal_p2() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    premarket = datetime.combine(window.end_et.date() + timedelta(days=1), datetime.min.time(), window.end_et.tzinfo)
    premarket = premarket.replace(hour=4, minute=3)
    bars = [
        _kline(window.start_et + timedelta(minutes=1), 100.5, 100.5, 100.5, 100.5),
        _kline(window.start_et + timedelta(hours=2), 101.0, 102.0, 101.0, 101.0),
    ]
    broker = FakeBrokerBarProvider({"1m": [_broker_bar(premarket, 100.8, 100.9)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        broker_provider=broker,
        weeks=1,
        now=now,
        opening_anchor="overnight",
        open_window_minutes=30,
    )

    assert rows[0]["status"] == "OBSERVE"
    assert rows[0]["transmission_data_quality"] == "MISSING_OVERNIGHT_FIRST_1M"
    assert rows[0]["broker_first_1m_close"] is None


def test_weekend_basis_backtest_does_not_use_5m_or_regular_open_as_formal_p2() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    regular_open = datetime.combine(window.end_et.date() + timedelta(days=1), datetime.min.time(), window.end_et.tzinfo)
    regular_open = regular_open.replace(hour=9, minute=31)
    bars = [
        _kline(window.start_et + timedelta(minutes=1), 100.5, 100.5, 100.5, 100.5),
        _kline(window.start_et + timedelta(hours=2), 101.0, 102.0, 101.0, 101.0),
    ]
    broker = FakeBrokerBarProvider({"5m": [_broker_bar(regular_open, 100.8, 100.9)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        broker_provider=broker,
        weeks=1,
        now=now,
        opening_anchor="regular_open",
        open_window_minutes=30,
    )

    assert rows[0]["status"] == "OBSERVE"
    assert rows[0]["transmission_data_quality"] == "MISSING_OVERNIGHT_FIRST_1M"
    assert rows[0]["broker_first_1m_close"] is None
    assert summarize_backtest_results(rows)["sample_weeks"] == 0


def test_weekend_basis_backtest_marks_unconfirmed_mapping_observe_only() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    quotes = [
        _basis_quote(window.start_et + timedelta(minutes=1), 100.5, 100.55),
        _basis_quote(window.start_et + timedelta(hours=2), 101.0, 101.05),
        _basis_quote(window.start_et + timedelta(hours=4), 102.0, 102.05),
        _basis_quote(window.start_et + timedelta(hours=5), 101.65, 101.7),
        _basis_quote(window.end_et, 101.1, 101.2),
    ]
    broker = FakeBrokerBarProvider({"1m": [_broker_bar(window.end_et, 100.8, 100.9)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        anchors=_anchors(afterhours=100),
        provider=FakeBasisQuoteProvider(quotes),
        broker_provider=broker,
        weeks=1,
        now=now,
        opening_anchor="overnight",
        open_window_minutes=2,
    )

    assert rows[0]["status"] == "HEDGE_LOCKED"
    assert rows[0]["data_quality"] == "OBSERVE_ONLY"
    assert rows[0]["mapping_status"] == "CANDIDATE_OBSERVATION"
    assert summarize_backtest_results(rows)["sample_weeks"] == 0


def test_weekend_basis_backtest_marks_kline_execution_as_estimated() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=1), 100, 100.5, 99.8, 100.5),
        _kline(window.start_et + timedelta(hours=2), 101, 101.2, 100.8, 101),
        _kline(window.start_et + timedelta(hours=4), 102, 102.2, 101.8, 102),
        _kline(window.start_et + timedelta(hours=5), 101.65, 101.7, 101.5, 101.65),
        _kline(window.end_et, 101.1, 101.2, 101.0, 101.1),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "afterhours_reference_time": "2026-07-03T19:59:00-04:00",
        }
    }
    broker = FakeBrokerBarProvider({"1m": [_broker_bar(window.end_et, 100.8, 100.9)]})

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeKlineProvider(bars),
        broker_provider=broker,
        weeks=1,
        now=now,
        opening_anchor="overnight",
    )

    assert rows[0]["status"] == "HEDGE_LOCKED"
    assert rows[0]["data_quality"] == "ESTIMATED_EXECUTION"
    assert summarize_backtest_results(rows)["sample_weeks"] == 0


def test_weekend_basis_backfill_replays_complete_weekend_with_first_threshold_next_minute() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    sunday = window.end_et.date()
    quotes = [
        _basis_quote(datetime.combine(sunday, datetime.min.time(), ZoneInfo("America/New_York")), 100.3, 100.34),
        _basis_quote(window.end_et - timedelta(hours=3, minutes=2), 100.7, 100.74),
        _basis_quote(window.end_et - timedelta(hours=3, minutes=1), 100.9, 100.94),
        _basis_quote(window.end_et - timedelta(hours=3), 101.0, 101.04),
        _basis_quote(window.end_et - timedelta(hours=2), 101.2, 101.5),
        _basis_quote(window.end_et, 100.8, 100.84),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "broker_overnight_bars": [_broker_bar(window.end_et, 100.65, 100.7)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
    )

    first_80 = next(row for row in rows if row["rule_name"] == "FIRST_THRESHOLD_80")
    assert first_80["status"] == "HEDGE_LOCKED"
    assert first_80["data_mode"] == "STRICT"
    assert first_80["entry_ts"] == quotes[3]["ts"]
    assert first_80["broker_hedge_price"] == 100.7
    assert round(first_80["net_locked_bps"], 2) == 29.79
    assert round(first_80["max_adverse_bps"], 2) == 49.5
    assert first_80["time_unhedged_minutes"] == 180
    assert first_80["oracle_note"] == "事后高点，不可交易"


def test_weekend_basis_backfill_allows_candidate_observation_but_not_trade_grade() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    quotes = [
        _basis_quote(window.end_et - timedelta(hours=4), 100.9, 100.94),
        _basis_quote(window.end_et - timedelta(hours=3, minutes=1), 101.0, 101.04),
        _basis_quote(window.end_et - timedelta(hours=3), 101.25, 101.29),
        _basis_quote(window.end_et, 100.9, 100.94),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "broker_overnight_bars": [_broker_bar(window.end_et, 100.7, 100.75)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
    )

    assert any(row["data_mode"] == "OBSERVATION" for row in rows)
    assert any(row["mapping_status"] == "CANDIDATE_OBSERVATION" for row in rows)
    assert not any(row["status"] == "BLOCK_MAPPING" for row in rows)
    summary = summarize_backfill_audit_results(rows)
    assert summary["observation_sample_count"] > 0
    assert summary["trade_grade_sample_count"] == 0


def test_weekend_basis_mapping_audit_marks_ratio_stable_candidate_verified_ready() -> None:
    broker_history = {
        "NVDA": _audit_broker_history(
            [
                ("2026-06-08", 100.0),
                ("2026-06-09", 101.0),
                ("2026-06-10", 102.0),
                ("2026-06-11", 103.0),
                ("2026-06-12", 104.0),
            ]
        )
    }
    provider = FakeMappingAuditProvider(
        _audit_binance_daily_bars(
            [
                ("2026-06-08", 100.2),
                ("2026-06-09", 101.1),
                ("2026-06-10", 102.1),
                ("2026-06-11", 103.1),
                ("2026-06-12", 104.1),
            ]
        )
        + _audit_weekend_bars(105.0)
    )

    rows = audit_weekend_basis_mappings(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        binance_provider=provider,
        broker_history_provider=broker_history,
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        min_samples=5,
    )

    row = rows[0]
    assert row["audit_status"] == "verified_ready"
    assert row["current_confidence"] == "candidate"
    assert row["weekend_data_ok"] is True
    assert row["sample_count"] == 5
    assert "USDT_ASSUMED_1_0" in row["warning"]


def test_verified_ready_mapping_still_requires_manual_confirm_for_strict_backfill() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    quotes = [
        _basis_quote(window.end_et - timedelta(hours=3, minutes=1), 101.0, 101.04),
        _basis_quote(window.end_et - timedelta(hours=3), 101.25, 101.29),
        _basis_quote(window.end_et, 100.9, 100.94),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "broker_overnight_bars": [_broker_bar(window.end_et, 100.7, 100.75)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="verified_ready"),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
    )

    assert any(row["data_mode"] == "OBSERVATION" for row in rows)
    assert summarize_backfill_audit_results(rows)["trade_grade_sample_count"] == 0


def test_confirmed_mapping_can_enter_strict_backfill_after_manual_confirm(tmp_path) -> None:
    mapping_path = tmp_path / "binance_symbol_mapping.local.json"
    audit_result = {
        "ticker": "NVDA",
        "broker_symbol": "NVDA",
        "binance_symbol": "NVDAUSDT",
        "market_type": "usdm_futures",
        "audit_status": "verified_ready",
        "median_ratio": 1.001,
        "median_abs_deviation_bps": 20,
        "max_abs_deviation_bps": 40,
        "sample_count": 5,
        "weekend_data_ok": True,
    }

    updated = confirm_weekend_basis_mapping(
        "NVDA",
        audit_result,
        path=mapping_path,
        confirmed_by="pytest",
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert updated["NVDA"]["mapping_confidence"] == "confirmed"
    assert updated["NVDA"]["confirmed_by"] == "pytest"
    assert updated["NVDA"]["audit_summary"]["median_ratio"] == 1.001
    loaded = load_binance_symbol_mapping(mapping_path, local_path=None)
    assert loaded["NVDA"]["mapping_confidence"] == "confirmed"
    assert loaded["NVDA"]["confirmed_by"] == "pytest"
    assert loaded["NVDA"]["audit_summary"]["median_ratio"] == 1.001


def test_mapping_audit_flags_multiplier_mismatch_and_missing_weekend_data() -> None:
    broker_history = {
        "NVDA": _audit_broker_history(
            [
                ("2026-06-08", 100.0),
                ("2026-06-09", 100.0),
                ("2026-06-10", 100.0),
                ("2026-06-11", 100.0),
                ("2026-06-12", 100.0),
            ]
        )
    }
    provider = FakeMappingAuditProvider(
        _audit_binance_daily_bars(
            [
                ("2026-06-08", 1000.0),
                ("2026-06-09", 1000.0),
                ("2026-06-10", 1000.0),
                ("2026-06-11", 1000.0),
                ("2026-06-12", 1000.0),
            ]
        )
    )

    rows = audit_weekend_basis_mappings(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        binance_provider=provider,
        broker_history_provider=broker_history,
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
        min_samples=5,
    )

    row = rows[0]
    assert row["audit_status"] == "rejected"
    assert row["weekend_data_ok"] is False
    assert "MULTIPLIER_MISMATCH" in row["warning"]
    assert "NO_RECENT_WEEKEND_BINANCE_DATA" in row["warning"]


def test_weekend_basis_backfill_marks_candidate_ratio_warning_but_keeps_observation() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    quotes = [
        _basis_quote(window.end_et - timedelta(hours=3, minutes=1), 108.0, 108.04),
        _basis_quote(window.end_et - timedelta(hours=3), 108.5, 108.54),
        _basis_quote(window.end_et, 107.5, 107.54),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "broker_overnight_bars": [_broker_bar(window.end_et, 101.0, 101.1)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(
            mapping_confidence="candidate",
            audit_summary={"median_ratio": 1.08, "sample_count": 5},
        ),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
    )

    assert any(row["mapping_status"] == "CANDIDATE_OBSERVATION" for row in rows)
    assert any("PRICE_RATIO_WARNING" in str(row.get("warning") or "") for row in rows)
    summary = summarize_backfill_audit_results(rows)
    assert summary["observation_sample_count"] > 0
    assert summary["trade_grade_sample_count"] == 0


def test_reject_weekend_basis_mapping_writes_rejected_without_confirming(tmp_path) -> None:
    mapping_path = tmp_path / "binance_symbol_mapping.local.json"

    updated = reject_weekend_basis_mapping(
        "NVDA",
        {"ticker": "NVDA", "binance_symbol": "NVDAUSDT", "audit_status": "rejected"},
        path=mapping_path,
        rejected_by="pytest",
        now=datetime(2026, 6, 15, tzinfo=timezone.utc),
    )

    assert updated["NVDA"]["mapping_confidence"] == "rejected"
    assert updated["NVDA"]["rejected_by"] == "pytest"
    assert updated["NVDA"]["binance_symbol"] == "NVDAUSDT"


def test_weekend_basis_backfill_does_not_fallback_to_regular_open() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    regular_open = datetime.combine(window.end_et.date() + timedelta(days=1), datetime.min.time(), ZoneInfo("America/New_York")).replace(hour=9, minute=30)
    quotes = [
        _basis_quote(window.end_et - timedelta(hours=3, minutes=1), 100.9),
        _basis_quote(window.end_et - timedelta(hours=3), 101.0),
        _basis_quote(window.end_et, 100.8),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "broker_overnight_bars": [_broker_bar(regular_open, 100.6, 100.7)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
    )

    threshold_rows = [row for row in rows if row["rule_name"].startswith("FIRST_THRESHOLD")]
    assert threshold_rows
    assert {row["data_quality"] for row in threshold_rows} == {"NO_BROKER_OVERNIGHT_BAR"}
    assert all(row["status"] == "WAIT_BROKER_OPEN" for row in threshold_rows)


def test_weekend_basis_backfill_marks_kline_execution_estimated_and_excludes_strict() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.end_et - timedelta(hours=3, minutes=1), 100.9, 100.95, 100.8, 100.9),
        _kline(window.end_et - timedelta(hours=3), 101.0, 101.05, 100.9, 101.0),
        _kline(window.end_et, 100.8, 100.85, 100.7, 100.8),
    ]

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        weeks=1,
        now=now,
        include_estimated=False,
    )

    assert rows[0]["data_mode"] == "ESTIMATED"
    assert rows[0]["data_quality"] == "ESTIMATED_EXECUTION"
    assert summarize_backfill_audit_results(rows)["strict_sample_count"] == 0


def test_weekend_basis_backfill_relative_high_uses_only_past_window() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    quotes = [
        _basis_quote(window.end_et - timedelta(hours=8), 100.8, 100.84),
        _basis_quote(window.end_et - timedelta(hours=7, minutes=40), 100.9, 100.94),
        _basis_quote(window.end_et - timedelta(hours=7, minutes=20), 101.0, 101.04),
        _basis_quote(window.end_et - timedelta(hours=7), 101.1, 101.14),
        _basis_quote(window.end_et - timedelta(hours=6, minutes=40), 101.2, 101.24),
        _basis_quote(window.end_et - timedelta(hours=6, minutes=20), 101.3, 101.34),
        _basis_quote(window.end_et - timedelta(hours=6), 101.8, 101.84),
        _basis_quote(window.end_et - timedelta(hours=5, minutes=59), 101.65, 101.69),
        _basis_quote(window.end_et, 101.1, 101.14),
    ]
    anchors = {
        "NVDA": {
            "afterhours_reference_price": 100,
            "broker_overnight_bars": [_broker_bar(window.end_et, 100.8, 100.9)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
        strategy_config=BasisStrategyConfig(min_entry_premium_bps=120, min_percentile=85),
    )

    relative_rows = [row for row in rows if row["rule_name"].startswith("RELATIVE_HIGH_PULLBACK")]
    assert relative_rows
    assert all(datetime.fromisoformat(row["entry_ts"]).astimezone(timezone.utc) >= datetime.fromisoformat(quotes[2]["ts"]) for row in relative_rows)


def test_weekend_basis_backfill_supports_weekly_anchor_and_low_risk_window() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    early_quote = _basis_quote(window.end_et - timedelta(hours=10), 101.0, 101.04)
    low_risk_signal = _basis_quote(window.end_et - timedelta(hours=3, minutes=1), 101.2, 101.24)
    low_risk_entry = _basis_quote(window.end_et - timedelta(hours=3), 101.3, 101.34)
    quotes = [early_quote, low_risk_signal, low_risk_entry, _basis_quote(window.end_et, 101.1, 101.14)]
    anchors = {
        "NVDA": {
            "weekly_anchors": {
                window.week_id: {
                    "regular_close_price": 99,
                    "regular_close_date": "2026-07-03",
                }
            },
            "broker_overnight_bars": [_broker_bar(window.end_et, 100.8, 100.9)],
        }
    }

    rows = run_weekend_basis_backfill_audit(
        ["NVDA"],
        mapping=_mapping(),
        anchors=anchors,
        provider=FakeBasisQuoteProvider(quotes),
        weeks=1,
        now=now,
        low_risk_window_only=True,
    )

    first_row = next(row for row in rows if row["rule_name"].startswith("FIRST_THRESHOLD"))
    assert first_row["anchor_price"] == 99
    assert first_row["data_quality"] == "ANCHOR_REGULAR_CLOSE_ONLY"
    assert first_row["entry_ts"] == low_risk_entry["ts"]
    assert first_row["entry_window"] == "LOW_RISK"
    assert summarize_backfill_audit_results(rows)["strict_sample_count"] > 0


def test_basis_opportunity_blocks_unconfirmed_mapping() -> None:
    now = datetime(2026, 7, 5, 18, tzinfo=timezone.utc)
    opportunity = build_basis_opportunity(
        ticker="NVDA",
        mapping=_mapping(mapping_confidence="candidate")["NVDA"],
        broker_anchor_price=100,
        binance_quotes=[_basis_quote(now, 102.0, 102.05)],
        now=now,
    )

    assert opportunity["status"] == "BLOCK_MAPPING"
    assert opportunity["data_quality"] == "UNCONFIRMED_MAPPING"


def test_basis_opportunity_allows_short_only_when_signal_liquidity_and_mapping_pass() -> None:
    now = datetime(2026, 7, 5, 18, tzinfo=timezone.utc)
    quotes = [
        _basis_quote(now - timedelta(hours=4), 100.5, 100.55),
        _basis_quote(now - timedelta(hours=3), 101.0, 101.05),
        _basis_quote(now - timedelta(hours=2), 102.0, 102.05),
        _basis_quote(now, 101.65, 101.7, depth_usd=500_000),
    ]

    opportunity = build_basis_opportunity(
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_quotes=quotes,
        now=now,
    )

    assert opportunity["status"] == "ALLOW_SHORT"
    assert opportunity["binance_entry_bid"] == 101.65
    assert round(opportunity["entry_premium_bps"], 2) == 165.0
    assert round(opportunity["pullback_bps"], 2) == 35.0
    assert round(opportunity["min_binance_short_price"], 2) == 101.2


def test_basis_opportunity_blocks_wide_binance_spread() -> None:
    now = datetime(2026, 7, 5, 18, tzinfo=timezone.utc)
    opportunity = build_basis_opportunity(
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_quotes=[
            _basis_quote(now - timedelta(hours=1), 102.0, 102.05),
            _basis_quote(now, 101.65, 102.5),
        ],
        now=now,
    )

    assert opportunity["status"] == "BLOCK_LIQUIDITY"
    assert opportunity["data_quality"] == "WIDE_SPREAD"


def test_paper_trade_short_entry_uses_bid_or_manual_fill_price() -> None:
    trade = create_weekend_basis_trade(
        week_id="2026-W27",
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_entry_bid=101.8,
        binance_entry_ask=101.9,
        binance_short_qty=2,
        entry_ts=datetime(2026, 7, 5, 18, tzinfo=timezone.utc),
    )

    assert trade["status"] == "SHORT_OPEN"
    assert trade["binance_entry_price"] == 101.8
    assert trade["binance_entry_bid"] == 101.8
    assert trade["entry_notional"] == 203.6
    assert round(trade["entry_premium_bps"], 2) == 180.0


def test_paper_trade_hedge_uses_broker_ask_and_does_not_realize_pnl() -> None:
    trade = create_weekend_basis_trade(
        week_id="2026-W27",
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_entry_bid=101.8,
        binance_short_qty=2,
    )

    locked = record_broker_hedge(
        trade,
        broker_hedge_bid=100.7,
        broker_hedge_ask=100.8,
        broker_shares=2,
        binance_same_min_bid=101.0,
        binance_same_min_ask=101.1,
        hedge_ts=datetime(2026, 7, 6, 0, tzinfo=timezone.utc),
    )

    assert locked["status"] == "HEDGE_LOCKED"
    assert locked["broker_hedge_price"] == 100.8
    assert round(locked["net_locked_bps"], 2) == 99.21
    assert locked["realized_pnl"] is None
    assert locked["realized_pnl_bps"] is None


def test_paper_trade_realized_pnl_only_after_both_legs_exit() -> None:
    trade = create_weekend_basis_trade(
        week_id="2026-W27",
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_entry_bid=101.8,
        binance_short_qty=2,
    )
    locked = record_broker_hedge(trade, broker_hedge_ask=100.8, broker_shares=2)

    closed = close_weekend_basis_trade(
        locked,
        binance_exit_ask=100.6,
        broker_exit_bid=101.2,
        fees=0.1,
        funding=0.0,
        slippage=0.0,
        exit_ts=datetime(2026, 7, 6, 2, tzinfo=timezone.utc),
    )

    assert closed["status"] == "CLOSED"
    assert round(closed["realized_pnl"], 2) == 3.1
    assert round(closed["realized_pnl_bps"], 2) == 152.26


def test_max_broker_buy_price_blocks_unpriced_hedge_prompt() -> None:
    cfg = BasisStrategyConfig(required_net_locked_bps=100)
    trade = create_weekend_basis_trade(
        week_id="2026-W27",
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_entry_bid=101.0,
    )
    max_buy = max_broker_buy_price(trade, cfg)

    evaluated = evaluate_open_trade(
        trade,
        broker_bar=_broker_bar(datetime(2026, 7, 6, 0, tzinfo=timezone.utc), max_buy + 0.2, max_buy + 0.3),
        config=cfg,
    )

    assert round(max_buy, 2) == 100.0
    assert evaluated["status"] == "HEDGE_DUE"
    assert evaluated["hedge_ok"] is False
    assert evaluated["warning"] == "BROKER_PRICE_ABOVE_LIMIT"


def test_residual_basis_convergence_moves_hedged_trade_to_exit_ready() -> None:
    trade = create_weekend_basis_trade(
        week_id="2026-W27",
        ticker="NVDA",
        mapping=_mapping()["NVDA"],
        broker_anchor_price=100,
        binance_entry_bid=101.8,
    )
    locked = record_broker_hedge(trade, broker_hedge_bid=100.8, broker_hedge_ask=100.9)

    evaluated = evaluate_open_trade(
        locked,
        current_binance_quote=_basis_quote(datetime(2026, 7, 6, 1, tzinfo=timezone.utc), 100.95, 101.0),
        broker_bar=_broker_bar(datetime(2026, 7, 6, 1, tzinfo=timezone.utc), 100.9, 100.95),
        config=BasisStrategyConfig(exit_basis_threshold_bps=10),
    )

    assert evaluated["status"] == "EXIT_READY"
    assert abs(evaluated["residual_basis_bps"]) <= 10


def test_weekend_spread_paper_opportunity_blocks_unconfirmed_mapping_in_ui_frame() -> None:
    rows = [
        {
            "ticker": "NVDA",
            "binance_symbol": "NVDAUSDT",
            "binance_bid": 101.65,
            "binance_ask": 101.7,
            "afterhours_reference_price": 100,
            "friday_close_date": "2026-07-03",
            "updated_at": "2026-07-05T18:00:00+00:00",
        }
    ]

    opportunities = weekend_spread._paper_opportunities(rows, _mapping(mapping_confidence="candidate"))
    frame = weekend_spread._paper_opportunity_frame(opportunities)

    assert opportunities[0]["status"] == "BLOCK_MAPPING"
    assert "映射未确认" in frame.loc[0, "status"]


def test_weekend_spread_ui_exposes_paper_trade_area() -> None:
    source = inspect.getsource(weekend_spread)

    assert "手动交易记录（可选） / Paper Trade" in source
    assert "create_weekend_basis_trade" in source
    assert "record_broker_hedge" in source
    assert "close_weekend_basis_trade" in source


def test_weekend_peak_short_backtest_calculates_premium_decay_from_open_window_vwap(tmp_path) -> None:
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
        kline_cache_path=tmp_path / "klines.json",
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
    assert round(row["net_short_return_pct"], 2) == 10.22
    assert round(row["short_return_at_open_pct"], 2) == 10.22
    assert row["data_quality"] == "OK"
    assert row["kline_cache_status"] == "API_LIVE"


def test_weekend_peak_short_backtest_uses_cached_klines_when_provider_fails(tmp_path) -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=10), 100, 110, 99, 108),
        _kline(window.end_et, 105, 106, 103, 104, 1),
    ]
    cache_path = tmp_path / "weekend_backtest_klines.json"

    first = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(bars),
        weeks=1,
        kline_cache_path=cache_path,
        now=now,
    )
    assert first[0]["kline_cache_status"] == "API_LIVE"
    assert cache_path.exists()

    second = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(),
        anchors=_anchors(afterhours=100),
        provider=FakeKlineProvider(error=TimeoutError("futures timeout")),
        weeks=1,
        kline_cache_path=cache_path,
        now=now,
    )

    assert second[0]["data_quality"] == "OK"
    assert second[0]["kline_cache_status"] == "CACHE_FALLBACK"
    assert second[0]["weekend_peak_binance_price"] == 110
    assert "缓存 K 线" in second[0]["result_note"]


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


def test_backtest_preflight_blocks_no_mapping() -> None:
    preflight = build_weekend_backtest_preflight(
        ["NVDA"],
        mapping={},
        anchors=_anchors(),
        include_unconfirmed=False,
    )

    assert preflight["can_run"] is False
    assert preflight["primary_block_reason"] == "NO_MAPPING"
    assert preflight["excluded"][0]["exclusion_reason"] == "NO_MAPPING"


def test_backtest_preflight_excludes_candidate_by_default() -> None:
    preflight = build_weekend_backtest_preflight(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate", risk_note="manual candidate"),
        anchors=_anchors(),
        include_unconfirmed=False,
    )

    assert preflight["can_run"] is False
    assert preflight["primary_block_reason"] == "UNCONFIRMED_EXCLUDED"
    assert preflight["excluded"][0]["symbol"] == "NVDAUSDT"


def test_backtest_preflight_excludes_auto_candidate_until_observation_mode() -> None:
    preflight = build_weekend_backtest_preflight(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate", risk_note="候选 symbol 按 ticker+USDT 自动生成"),
        anchors=_anchors(),
        include_unconfirmed=False,
    )

    assert preflight["can_run"] is False
    assert preflight["primary_block_reason"] == "AUTO_CANDIDATE_NOT_ALLOWED"


def test_backtest_preflight_allows_candidate_when_include_unconfirmed() -> None:
    preflight = build_weekend_backtest_preflight(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="candidate"),
        anchors=_anchors(),
        include_unconfirmed=True,
    )

    assert preflight["can_run"] is True
    assert preflight["eligible_tickers"] == ["NVDA"]


def test_backtest_preflight_allows_missing_price_anchor_for_diagnostics() -> None:
    preflight = build_weekend_backtest_preflight(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="confirmed"),
        anchors=_anchors(afterhours=None, regular=None),
        include_unconfirmed=False,
    )

    assert preflight["can_run"] is True
    assert preflight["eligible_tickers"] == ["NVDA"]


def test_weekend_peak_short_backtest_normalizes_legacy_spot_mapping_to_usdm_futures() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [
        _kline(window.start_et + timedelta(minutes=10), 100, 110, 99, 108),
        _kline(window.end_et, 105, 106, 103, 104),
    ]
    provider = FakeKlineProvider(bars)

    rows = run_weekend_peak_short_backtest(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        anchors=_anchors(),
        provider=provider,
        weeks=1,
        now=now,
    )

    assert rows[0]["market_type"] == "usdm_futures"
    assert rows[0]["data_quality"] == "OK"
    assert provider.calls[0]["market_type"] == "usdm_futures"


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
    frame = weekend_spread._backtest_frame(rows)
    assert "Binance K 线不可用" in frame.loc[0, "排除 / 提醒"]
    assert "futures timeout" in frame.loc[0, "排除 / 提醒"]


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
        {"net_locked_bps": 200, "theoretical_short_return_pct": 2.2, "premium_decay_ratio": 80, "premium_decay_pct": 4, "open_remaining_premium_pct": 1, "data_quality": "OK"},
        {"net_locked_bps": 900, "theoretical_short_return_pct": 9.2, "premium_decay_ratio": 90, "premium_decay_pct": 9, "open_remaining_premium_pct": 1, "data_quality": "UNCONFIRMED_MAPPING"},
    ]

    summary = summarize_backtest_results(rows)

    assert summary["sample_weeks"] == 1
    assert summary["positive_weeks"] == 1
    assert summary["win_rate"] == 1.0
    assert summary["avg_net_return_pct"] == 2.0
    assert summary["avg_net_locked_bps"] == 200


def test_backtest_results_are_persisted_and_reloadable(tmp_path) -> None:
    rows = [
        {
            "ticker": "NVDA",
            "week_id": "2026-W24",
            "net_locked_bps": 120,
            "premium_decay_ratio": 50,
            "theoretical_short_return_pct": 1.4,
            "premium_decay_pct": 2.0,
            "open_remaining_premium_pct": 1.0,
            "data_quality": "OK",
        }
    ]
    path = tmp_path / "backtest.json"

    saved = save_backtest_results(rows, preflight={"can_run": True}, params={"weeks": 4}, path=path)
    loaded = load_backtest_results(path=path)

    assert saved["last_run_at"]
    assert loaded["rows"][0]["ticker"] == "NVDA"
    assert loaded["preflight"]["can_run"] is True
    assert loaded["params"]["weeks"] == 4
    assert loaded["summary"]["sample_weeks"] == 1


def test_clear_backtest_view_state_does_not_delete_saved_cache(tmp_path) -> None:
    path = tmp_path / "backtest.json"
    save_backtest_results([{"ticker": "NVDA", "data_quality": "OK"}], path=path)

    cleared = clear_backtest_view_state()
    loaded = load_backtest_results(path=path)

    assert cleared["rows"] == []
    assert loaded["rows"][0]["ticker"] == "NVDA"


def test_backtest_summary_and_empty_ui_frame_do_not_crash() -> None:
    assert summarize_backtest_results([])["sample_weeks"] == 0
    assert weekend_spread._backtest_frame([]).empty


def test_weekend_review_backtest_writes_regular_close_fallback_into_p0() -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    bars = [_kline(window.start_et + timedelta(hours=8), 100.0, 102.0, 99.0, 101.0)]
    overnight_provider = FakeBrokerBarProvider(
        {"1m": [_broker_bar(window.end_et, 101.4, 101.5, close=101.45)]}
    )

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="confirmed"),
        anchors=_anchors(afterhours=None, regular=99.0),
        provider=FakeKlineProvider(bars),
        overnight_provider=overnight_provider,
        weeks=1,
        now=now,
    )

    row = rows[0]
    assert row["friday_afterhours_close"] == 99.0
    assert row["friday_afterhours_provider"] == "REGULAR_CLOSE_FALLBACK"
    assert row["friday_afterhours_quality"] == "REGULAR_CLOSE_FALLBACK"
    assert row["transmission_data_quality"] == "REGULAR_CLOSE_FALLBACK"
    review = weekend_spread._weekend_review_rows(rows)[0]
    assert review["friday_afterhours_close"] == 99.0
    assert review["data_quality"] == "REGULAR_CLOSE_FALLBACK"
    assert review["failure_reason"] == "常规收盘回退，仅观察"
    assert review["status"] == "仅观察"


def test_default_overnight_price_provider_uses_configured_alpaca_boats(monkeypatch) -> None:
    monkeypatch.setenv("OVERNIGHT_PRICE_PROVIDER", "ALPACA_BOATS")
    monkeypatch.setenv("ALPACA_API_KEY_ID", "key")
    monkeypatch.setenv("ALPACA_API_SECRET_KEY", "secret")

    provider = default_overnight_price_provider()

    assert isinstance(provider, AlpacaBoatsOvernightProvider)
    assert provider.provider_name == "ALPACA_BOATS"


def test_default_overnight_price_provider_supports_ibkr_overnight_json(monkeypatch, tmp_path) -> None:
    bars_path = tmp_path / "ibkr_overnight.json"
    bars_path.write_text(json.dumps({"NVDA": [{"ts": "2026-06-14T20:00:00-04:00", "close": 101.2}]}))
    monkeypatch.setenv("OVERNIGHT_PRICE_PROVIDER", "IBKR_OVERNIGHT")
    monkeypatch.setenv("IBKR_OVERNIGHT_BARS_PATH", str(bars_path))

    provider = default_overnight_price_provider()

    assert isinstance(provider, JsonFileOvernightProvider)
    assert provider.get_overnight_bars(
        "NVDA",
        start_time_ms=0,
        end_time_ms=2**63 - 1,
        interval="1m",
    )[0]["close"] == 101.2


def test_overnight_provider_self_check_reports_missing_config(monkeypatch) -> None:
    monkeypatch.setattr("data.overnight_price_provider.get_secret", lambda _name: "")

    result = build_overnight_provider_self_check(now=datetime(2026, 7, 6, 1, tzinfo=timezone.utc))

    assert result["ok"] is False
    assert result["provider_display"] == "未配置"
    assert result["reason"] == "美股夜盘数据源未配置"
    assert result["returned_bar_count"] == 0
    assert "None" not in str(result)
    assert "anchor_source" not in str(result)


def test_overnight_provider_self_check_reads_first_minute_close(monkeypatch) -> None:
    secrets = {
        "OVERNIGHT_PRICE_PROVIDER": "ALPACA_BOATS",
        "ALPACA_API_KEY_ID": "key",
        "ALPACA_API_SECRET_KEY": "secret",
    }
    monkeypatch.setattr("data.overnight_price_provider.get_secret", lambda name: secrets.get(name))
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]

    class NamedFakeBrokerBarProvider(FakeBrokerBarProvider):
        provider_name = "ALPACA_BOATS"

    provider = NamedFakeBrokerBarProvider({"1m": [_broker_bar(window.end_et, 101.1, 101.3, close=101.2)]})

    result = build_overnight_provider_self_check(provider=provider, now=now)

    assert result["ok"] is True
    assert result["provider_display"] == "ALPACA_BOATS"
    assert result["provider"] == "ALPACA_BOATS"
    assert result["returned_bar_count"] == 1
    assert result["first_bar_close"] == 101.2
    assert result["requested_start"].startswith("2026-07-06T00:00:00")
    assert result["requested_end"].startswith("2026-07-06T00:01:00")


def test_tradingview_webhook_writes_price_cache(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TRADINGVIEW_WEBHOOK_SECRET", "secret")
    cache_path = tmp_path / "tv_cache.json"

    rejected = record_tradingview_webhook(
        {
            "secret": "bad",
            "symbol": "NVDA",
            "event_type": EVENT_OVERNIGHT_FIRST_1M_CLOSE,
            "timestamp_et": "2026-06-14 20:01:00",
            "close": 205.42,
            "source": "TradingView",
        },
        path=cache_path,
    )
    assert rejected["ok"] is False
    assert load_price_cache(cache_path) == []

    result = record_tradingview_webhook(
        {
            "secret": "secret",
            "symbol": "NVDA",
            "event_type": EVENT_OVERNIGHT_FIRST_1M_CLOSE,
            "timestamp_et": "2026-06-14 20:01:00",
            "close": 205.42,
            "source": "TradingView",
        },
        path=cache_path,
    )

    assert result["ok"] is True
    records = load_price_cache(cache_path)
    assert len(records) == 1
    assert records[0]["provider"] == "TRADINGVIEW_WEBHOOK"
    assert records[0]["event_type"] == EVENT_OVERNIGHT_FIRST_1M_CLOSE


def test_tradingview_cache_matches_overnight_20_00_and_20_01(tmp_path) -> None:
    from data.tradingview_price_cache import upsert_price_event

    cache_path = tmp_path / "tv_cache.json"
    for timestamp, close in [("2026-06-14 20:00:00", 208.88), ("2026-06-21 20:01:00", 209.12)]:
        upsert_price_event(
            symbol="NVDA",
            event_type=EVENT_OVERNIGHT_FIRST_1M_CLOSE,
            timestamp_et=timestamp,
            close=close,
            provider="TRADINGVIEW_WEBHOOK",
            source_type="TV_ALERT",
            path=cache_path,
        )

    first = find_overnight_first_1m_close(
        "NVDA",
        datetime(2026, 6, 14, 20, 0, tzinfo=ZoneInfo("America/New_York")),
        path=cache_path,
    )
    second = find_overnight_first_1m_close(
        "NVDA",
        datetime(2026, 6, 21, 20, 0, tzinfo=ZoneInfo("America/New_York")),
        path=cache_path,
    )

    assert first.ok is True
    assert first.close == 208.88
    assert second.ok is True
    assert second.close == 209.12


def test_tradingview_csv_import_recognizes_nvda_and_writes_1m_bars(tmp_path) -> None:
    csv_path = tmp_path / "NVDA_1m.csv"
    csv_path.write_text(
        "time,open,high,low,close,volume\n"
        "2026-06-14 20:01:00,204,206,203,205.42,1000\n",
        encoding="utf-8",
    )
    cache_path = tmp_path / "tv_cache.json"

    result = import_tradingview_csv_file(csv_path, cache_path=cache_path)

    assert result["symbol"] == "NVDA"
    assert result["imported_rows"] == 1
    records = load_price_cache(cache_path)
    assert records[0]["provider"] == "TRADINGVIEW_CSV"


def test_weekend_basis_backtest_reads_p0_p2_from_tradingview_cache(monkeypatch, tmp_path) -> None:
    now = datetime(2026, 7, 6, 1, tzinfo=timezone.utc)
    window = recent_weekend_windows(weeks=1, now=now)[0]
    cache_path = tmp_path / "tv_cache.json"
    for payload in [
        {
            "symbol": "NVDA",
            "event_type": EVENT_FRIDAY_AFTERHOURS_CLOSE,
            "timestamp_et": window.start_et.isoformat(),
            "close": 100.0,
            "provider": "TRADINGVIEW_WEBHOOK",
            "source_type": "TV_ALERT",
        },
        {
            "symbol": "NVDA",
            "event_type": EVENT_OVERNIGHT_FIRST_1M_CLOSE,
            "timestamp_et": (window.end_et + timedelta(minutes=1)).isoformat(),
            "close": 102.0,
            "provider": "TRADINGVIEW_WEBHOOK",
            "source_type": "TV_ALERT",
        },
    ]:
        from data.tradingview_price_cache import upsert_price_event

        upsert_price_event(path=cache_path, **payload)

    monkeypatch.setattr(
        "data.weekend_spread_backtest.find_friday_afterhours_close",
        lambda symbol, friday_date: find_friday_afterhours_close(symbol, friday_date, path=cache_path),
    )
    monkeypatch.setattr(
        "data.weekend_spread_backtest.find_overnight_first_1m_close",
        lambda symbol, session_start: find_overnight_first_1m_close(symbol, session_start, path=cache_path),
    )
    bars = [_kline(window.start_et + timedelta(hours=8), 100.0, 103.0, 99.0, 101.0)]

    rows = run_weekend_basis_backtest(
        ["NVDA"],
        mapping=_mapping(mapping_confidence="confirmed"),
        anchors={},
        provider=FakeKlineProvider(bars),
        overnight_provider=None,
        weeks=1,
        now=now,
    )

    row = rows[0]
    assert row["friday_afterhours_close"] == 100.0
    assert row["overnight_first_1m_close"] == 102.0
    assert row["transmission_data_quality"] == "TRADINGVIEW_WEBHOOK_SAMPLE"
    assert row["binance_premium_pct"] == pytest.approx(3.0)
    assert row["overnight_vs_binance_pct"] == pytest.approx(-0.9708737864)




def test_weekend_review_frame_keeps_homepage_columns_simple() -> None:
    rows = [
        {
            "week_id": "2026-W24",
            "ticker": "NVDA",
            "afterhours_reference_price": 100.0,
            "afterhours_reference_time": "2026-06-12T19:59:00-04:00",
            "binance_weekend_max_price": 102.0,
            "binance_weekend_max_time": "2026-06-14T19:58:00-04:00",
            "broker_first_1m_close": 101.5,
            "broker_first_1m_time": "2026-06-14T20:00:00-04:00",
            "binance_symbol": "NVDAUSDT",
            "binance_kline_count": 3120,
            "binance_provider": "BINANCE_USDT_M",
            "data_quality": "OK",
        },
        {
            "week_id": "2026-W24",
            "ticker": "NVDA",
            "afterhours_reference_price": 100.0,
            "binance_weekend_max_price": 101.0,
            "data_quality": "OK",
        },
        {
            "week_id": "2026-W23",
            "ticker": "ADBE",
            "afterhours_reference_price": 200.0,
            "binance_weekend_max_price": 198.0,
            "binance_weekend_max_time": "2026-06-07T19:58:00-04:00",
            "broker_first_1m_close": 197.5,
            "broker_first_1m_time": "2026-06-07T20:00:00-04:00",
            "data_quality": "OK",
        },
    ]

    review_rows = weekend_spread._weekend_review_rows(rows)
    frame = weekend_spread._weekend_review_frame(review_rows)

    assert list(frame.columns) == [
        "周次",
        "股票",
        "Binance 合约",
        "周五盘后收盘价",
        "P0 来源",
        "盘后收盘时间",
        "Binance 周末最高价",
        "Binance 高点时间",
        "美股夜盘首分钟收盘",
        "P2 来源",
        "夜盘首分钟时间",
        "Binance 周末冲高%",
        "夜盘相对 Binance 高点%",
        "夜盘相对周五盘后%",
        "周末高点兑现率%",
        "样本状态",
        "状态",
        "失败原因",
    ]
    assert "锁结收益" not in frame.columns
    assert "剩余基差" not in frame.columns
    assert len(frame) == 2
    nvda = frame[frame["股票"] == "NVDA"].iloc[0]
    assert nvda["周五盘后收盘价"] == 100.0
    assert nvda["盘后收盘时间"] == "2026-06-12 19:59 ET"
    assert nvda["美股夜盘首分钟收盘"] == 101.5
    assert nvda["夜盘首分钟时间"] == "2026-06-14 20:00 ET"
    assert nvda["Binance 合约"] == "NVDAUSDT"
    assert nvda["Binance 高点时间"] == "2026-06-14 19:58 ET"
    assert nvda["Binance 周末最高价"] == 102.0
    assert round(nvda["Binance 周末冲高%"], 2) == 2.0
    assert round(nvda["夜盘相对 Binance 高点%"], 2) == -0.49
    assert round(nvda["夜盘相对周五盘后%"], 2) == 1.5
    assert nvda["周末高点兑现率%"] == 75.0
    assert nvda["状态"] == "正式样本"

def test_weekend_review_summary_uses_latest_four_weeks() -> None:
    rows = [
        {
            "week_id": f"2026-W{week:02d}",
            "ticker": "NVDA",
            "afterhours_reference_price": 90.0,
            "binance_weekend_max_price": 100.0,
            "broker_first_1m_close": 100.0 + week,
            "data_quality": "OK",
        }
        for week in range(20, 25)
    ]

    summary = weekend_spread._weekend_review_summary(weekend_spread._weekend_review_rows(rows))

    assert summary["sample_count"] == 4
    assert round(summary["avg_binance_premium_pct"], 2) == 11.11
    assert round(summary["avg_overnight_vs_binance_pct"], 2) == 22.5
    assert round(summary["latest_week_capture_pct"], 2) == 340.0



def test_weekend_review_summary_counts_only_ok_samples() -> None:
    rows = [
        {
            "week_id": "2026-W24",
            "ticker": "NVDA",
            "afterhours_reference_price": 99.0,
            "binance_weekend_max_price": 100.0,
            "broker_first_1m_close": 104.0,
            "data_quality": "OK",
        },
        {
            "week_id": "2026-W24",
            "ticker": "NOW",
            "afterhours_reference_price": 100.0,
            "binance_weekend_max_price": 120.0,
            "data_quality": "NO_MAPPING",
        },
    ]

    summary = weekend_spread._weekend_review_summary(weekend_spread._weekend_review_rows(rows))
    frame = weekend_spread._weekend_review_frame(
        weekend_spread._ok_weekend_review_rows(weekend_spread._weekend_review_rows(rows))
    )

    assert summary["sample_count"] == 1
    assert round(summary["avg_binance_premium_pct"], 2) == 1.01
    assert round(summary["avg_overnight_vs_binance_pct"], 2) == 4.0
    assert list(frame["股票"]) == ["NVDA"]


def test_weekend_review_formats_epoch_stock_reference_date() -> None:
    rows = [
        {
            "week_id": "2026-W24",
            "ticker": "NVDA",
            "afterhours_reference_price": 205.42,
            "afterhours_reference_time": "1781308799",
            "binance_weekend_max_price": 209.79,
            "binance_weekend_max_time": "2026-06-14T19:58:00-04:00",
            "data_quality": "OBSERVE_ANCHOR_ONLY",
        }
    ]

    frame = weekend_spread._weekend_review_frame(weekend_spread._weekend_review_rows(rows))

    assert frame.iloc[0]["盘后收盘时间"] == "2026-06-12 19:59 ET"


def test_weekend_review_marks_missing_price_as_incomplete() -> None:
    rows = [
        {
            "week_id": "2026-W24",
            "ticker": "NOW",
            "data_quality": "BINANCE_KLINE_UNAVAILABLE",
            "warning": "Binance K 线不可用",
        }
    ]

    review_rows = weekend_spread._weekend_review_rows(rows)
    frame = weekend_spread._weekend_review_frame(review_rows)

    assert frame.iloc[0]["状态"] == "排除"
    assert frame.iloc[0]["周五盘后收盘价"] is None
    assert frame.iloc[0]["Binance 周末最高价"] is None
    assert frame.iloc[0]["失败原因"] == "缺少 Binance 周末 1m K 线"

def test_weekend_review_style_renders_with_current_pandas() -> None:
    frame = weekend_spread._weekend_review_frame(
        [
            {
                "week_id": "2026-W24",
                "ticker": "NVDA",
                "friday_afterhours_close": 100.0,
                "binance_price": 102.0,
                "broker_open_close": 104.0,
                "binance_premium_pct": 2.0,
                "overnight_vs_binance_pct": 1.96,
                "overnight_vs_afterhours_pct": 4.0,
                "capture_pct": 200.0,
                "status": "????",
            }
        ]
    )

    html = weekend_spread._style_weekend_review_frame(frame).to_html()

    assert "NVDA" in html
    assert "+2.00%" in html

def test_backtest_results_do_not_use_cache_when_preflight_has_no_eligible_mapping() -> None:
    cached = {
        "rows": [
            {
                "week_id": "2026-W24",
                "ticker": "NVDA",
                "binance_symbol": "NVDAUSDT",
                "data_quality": "OK",
            }
        ]
    }

    rows = weekend_spread._current_backtest_results(
        None,
        cached,
        preflight={"can_run": False, "eligible_tickers": []},
        mapping={},
        include_unconfirmed=False,
    )

    assert rows == []


def test_backtest_anchor_mapping_uses_distinct_historical_weekly_closes() -> None:
    history = pd.DataFrame(
        [
            {"date": "2026-06-12", "close": 100.0},
            {"date": "2026-06-19", "close": 110.0},
            {"date": "2026-06-26", "close": 120.0},
            {"date": "2026-07-03", "close": 130.0},
        ]
    )

    anchors = weekend_spread._backtest_anchor_mapping(
        ["NVDA"],
        weeks=4,
        cache=FakeCache(history),
        now=datetime(2026, 7, 8, 12, tzinfo=timezone.utc),
    )
    weekly = anchors["NVDA"]["weekly_anchors"]

    closes = [float(anchor["regular_close_price"]) for anchor in weekly.values()]
    dates = {anchor["regular_close_date"] for anchor in weekly.values()}

    assert len(weekly) == 4
    assert sorted(closes) == [100.0, 110.0, 120.0, 130.0]
    assert dates == {"2026-06-12", "2026-06-19", "2026-06-26", "2026-07-03"}


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

    assert "provider=_CacheOnlyBinanceProvider()" in source
    assert "CachedAfterhoursProvider(NullAfterhoursProvider())" in source


def test_cache_only_binance_provider_reads_recent_last_good_price(tmp_path) -> None:
    cache_path = tmp_path / "binance_price_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "usdm_futures:NVDAUSDT": {
                    "symbol": "NVDAUSDT",
                    "market_type": "usdm_futures",
                    "last_price": 207.08,
                    "bid": 207.1,
                    "ask": 207.2,
                    "volume_24h": 43573.98,
                    "funding_rate": 0.0,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "source": "binance_usdm_futures",
                    "error": "",
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = weekend_spread._CacheOnlyBinanceProvider(cache_path=cache_path).get_last_price(
        "NVDAUSDT",
        market_type="usdm_futures",
    )

    assert snapshot["last_price"] == 207.08
    assert snapshot["bid"] == 207.1
    assert snapshot["ask"] == 207.2
    assert snapshot["volume_24h"] == 43573.98
    assert snapshot["error"] == ""


def test_cache_only_binance_provider_ignores_stale_price_cache(tmp_path) -> None:
    cache_path = tmp_path / "binance_price_cache.json"
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    cache_path.write_text(
        json.dumps({"usdm_futures:NVDAUSDT": {"symbol": "NVDAUSDT", "last_price": 207.08, "updated_at": stale_time}}),
        encoding="utf-8",
    )

    snapshot = weekend_spread._CacheOnlyBinanceProvider(cache_path=cache_path).get_last_price(
        "NVDAUSDT",
        market_type="usdm_futures",
    )

    assert snapshot["last_price"] is None
    assert snapshot["error"] == "price_not_loaded"


def test_cache_only_binance_provider_can_use_stale_last_good_price_for_failure_fallback(tmp_path) -> None:
    cache_path = tmp_path / "binance_price_cache.json"
    stale_time = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
    cache_path.write_text(
        json.dumps(
            {
                "usdm_futures:NVDAUSDT": {
                    "symbol": "NVDAUSDT",
                    "last_price": 207.08,
                    "bid": 207.1,
                    "ask": 207.2,
                    "volume_24h": 43573.98,
                    "updated_at": stale_time,
                }
            }
        ),
        encoding="utf-8",
    )

    snapshot = weekend_spread._CacheOnlyBinanceProvider(cache_path=cache_path, allow_stale=True).get_last_price(
        "NVDAUSDT",
        market_type="usdm_futures",
    )

    assert snapshot["last_price"] == 207.08
    assert snapshot["bid"] == 207.1
    assert snapshot["ask"] == 207.2
    assert snapshot["source"] == "stale_binance_price_cache"
    assert snapshot["cache_status"] == "STALE"
    assert snapshot["error"] == ""


def test_refresh_failure_falls_back_to_stale_last_good_price_cache() -> None:
    source = inspect.getsource(weekend_spread._build_weekend_spread_rows_with_feedback)

    assert "_CacheOnlyBinanceProvider(allow_stale=True)" in source


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
    assert "build_weekend_backtest_preflight" in source
    assert "run_weekend_basis_backtest" in source
    assert "run_weekend_peak_short_backtest" not in source
    assert "disabled=not bool(preflight.get(\"can_run\"))" in source


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
    assert frame.loc[0, "价格锚点"] == "盘后 $102.88（已更新）"
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
    assert "回退参考" in frame.iloc[0, 6]


def test_live_frame_does_not_render_nan_afterhours_anchor_when_one_row_is_missing() -> None:
    rows = [
        {
            "ticker": "NVDA",
            "regular_close_price": 205.19,
            "afterhours_reference_price": 205.4238,
            "afterhours_cache_status": "CACHE_HIT",
            "afterhours_missing_reason": "",
            "binance_symbol": "NVDAUSDT",
            "binance_last_price": 207.08,
            "spread_vs_afterhours_pct": 0.81,
            "spread_vs_regular_close_pct": 0.92,
            "alert_level_cn": "观察",
            "mapping_confidence": "candidate",
            "updated_at": "2026-06-14T17:17:11+00:00",
        },
        {
            "ticker": "NOW",
            "regular_close_price": 102.15,
            "afterhours_reference_price": None,
            "afterhours_cache_status": "CACHE_DATE_MISMATCH",
            "afterhours_missing_reason": "CACHE_DATE_MISMATCH",
            "binance_symbol": "NOWUSDT",
            "binance_last_price": 104.32,
            "spread_vs_afterhours_pct": None,
            "spread_vs_regular_close_pct": 2.12,
            "alert_level_cn": "重点关注",
            "mapping_confidence": "candidate",
            "updated_at": "2026-06-14T17:16:45+00:00",
        },
    ]

    frame = weekend_spread._live_frame(rows)

    assert "$nan" not in frame.to_string()
    assert frame.loc[0, "价格锚点"] == "盘后 $205.42（已缓存）"
    assert frame.loc[1, "价格锚点"] == "收盘 $102.15｜盘后缺失：盘后缓存日期不匹配"
    assert frame.loc[1, "vs 盘后"] in {"—", "鈥?"}
    assert "当前使用周五收盘作为回退参考" in frame.loc[1, "风险"]


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
    assert "_data_quality_text(row.get('afterhours_data_quality')" in source
    assert "确认时间：" in source
    assert "finalized_at：" not in source


def test_refresh_error_fallback_is_localized() -> None:
    assert weekend_spread._refresh_error_text([{}]) == "Binance 刷新失败"


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
