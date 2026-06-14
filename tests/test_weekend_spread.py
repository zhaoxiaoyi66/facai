from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
from urllib.error import HTTPError

import pandas as pd

from data.binance_provider import BinanceHTTPPriceProvider
from data.weekend_spread import (
    build_mapping_diagnostics,
    build_weekend_spread_rows,
    classify_spread,
    discover_binance_symbol_candidates,
    load_binance_symbol_mapping,
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


def test_mapping_diagnostics_reports_missing_mapping_without_price_request() -> None:
    provider = FakeProvider()

    rows = build_mapping_diagnostics(["MSFT"], mapping={}, provider=provider, validate=True, include_candidates=False)

    assert rows[0]["validation_status"] == "暂无映射"
    assert rows[0]["configured_symbol"] == ""
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


def test_spot_mapping_requests_spot_price_from_provider() -> None:
    provider = FakeProvider(price=101.5, funding_rate=None)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        provider=provider,
        cache=FakeCache(),
    )

    assert rows[0]["status"] == "OK"
    assert rows[0]["funding_rate"] is None
    assert provider.calls == ["spot:NVDAUSDT"]


def test_valid_spot_mapping_fetches_price_when_full_candidate_scan_times_out() -> None:
    provider = SpotSymbolSpecificProvider()

    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping=_mapping(market_type="spot"),
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["status"] == "OK"
    assert row["binance_last_price"] == 207
    assert row["binance_bid"] == 206.8
    assert row["binance_ask"] == 207.2
    assert row["binance_volume_24h"] == 123456
    assert not any(path.endswith("exchangeInfo") and not params.get("symbol") for _, path, params in provider.calls)


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

    rows = build_mapping_diagnostics(["NVDA"], mapping={}, provider=provider, validate=False, include_candidates=True)

    assert rows[0]["validation_status"] == "暂无映射"
    assert rows[0]["candidates"][0]["symbol"] == "NVDAUSDT"
    assert rows[0]["candidates"][0]["status"] == "candidate"


def test_discover_candidates_searches_markets_without_confirming_mapping() -> None:
    provider = FakeProvider()

    result = discover_binance_symbol_candidates("NVDA", provider=provider)
    candidates = result["candidates"]

    assert result["data_source_status"] == "OK"
    assert {item["market_type"] for item in candidates} == {"spot", "usdm_futures"}
    assert all(item["status"] == "candidate" for item in candidates)
    assert all(item.get("mapping_confidence") != "confirmed" for item in candidates)
    assert "candidates:spot:NVDA" in provider.calls
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

    rows = build_mapping_diagnostics(["NVDA"], mapping={}, provider=provider, validate=False, include_candidates=True)

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
    assert counts["universe_total"] == 2
    assert default_rows == []


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
    assert counts["universe_total"] == 2
    assert [row["ticker"] for row in default_rows] == ["NVDA"]


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
    assert samples[0]["data_quality"] == "OK"


def test_no_mapping_rows_do_not_pollute_weekly_summary(tmp_path) -> None:
    path = tmp_path / "weekend_spread_log.json"
    rows = build_weekend_spread_rows(["MSFT"], mapping={}, provider=FakeProvider(), cache=FakeCache())

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


def test_weekend_spread_log_handles_empty_store(tmp_path) -> None:
    snapshot = get_weekly_log_snapshot(path=tmp_path / "missing.json", week_id="2026-W24")

    assert snapshot["sample_count"] == 0
    assert snapshot["summaries"] == []
    assert weekend_spread._summary_frame([]).empty
    assert weekend_spread._history_frame([]).empty


def test_weekend_spread_ui_does_not_allow_manual_realtime_price_input() -> None:
    source = inspect.getsource(weekend_spread)

    assert "manual_override_price" not in source
    assert "Binance 手动价格" not in source
    assert "周一验证价" in source
