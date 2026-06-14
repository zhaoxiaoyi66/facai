from __future__ import annotations

import json

import pandas as pd

from data.weekend_spread import build_weekend_spread_rows, classify_spread, load_binance_symbol_mapping


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

    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> dict:
        self.calls.append(symbol)
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
            "market_type": "futures",
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
    assert provider.calls == ["NVDAUSDT"]


def test_adbe_mock_focus_sample_keeps_observation_risk_notice() -> None:
    provider = FakeProvider(price=207, bid=206.8, ask=207.2)
    rows = build_weekend_spread_rows(
        ["ADBE"],
        mapping={
            "ADBE": {
                "enabled": True,
                "binance_symbol": "ADBEUSDT",
                "market_type": "futures",
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


def test_friday_holiday_uses_previous_trading_day_close() -> None:
    provider = FakeProvider(price=101.5)
    cache = FakeCache(pd.DataFrame([{"date": "2026-06-11", "close": 98.0}]))

    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=provider, cache=cache)

    assert rows[0]["friday_close"] == 98.0
    assert rows[0]["friday_close_date"] == "2026-06-11"
    assert rows[0]["close_source"] == "previous_trading_day_before_friday"
    assert provider.calls == ["NVDAUSDT"]


def test_bid_ask_spread_and_funding_warnings_are_exposed() -> None:
    provider = FakeProvider(price=101.5, bid=100, ask=102, volume_24h=5_000, funding_rate=0.001)

    rows = build_weekend_spread_rows(["NVDA"], mapping=_mapping(), provider=provider, cache=FakeCache())

    row = rows[0]
    assert round(row["binance_spread_pct"], 2) == 1.98
    assert "流动性不足" in row["liquidity_warning"]
    assert "成交量不足" in row["liquidity_warning"]
    assert "资金费率" in row["liquidity_warning"]


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
                        "market_type": "futures",
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

    mapping = load_binance_symbol_mapping(path)

    assert mapping["NVDA"]["binance_symbol"] == "NVDAUSDT"
    assert mapping["NVDA"]["mapping_confidence"] == "confirmed"
    assert mapping["ADBE"]["binance_symbol"] == "ADBEUSDT"
    assert mapping["ADBE"]["mapping_confidence"] == "manual_required"
