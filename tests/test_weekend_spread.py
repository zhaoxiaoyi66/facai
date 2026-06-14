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
    def __init__(self, price: float | None = 101.5, error: str = "") -> None:
        self.price = price
        self.error = error
        self.calls: list[str] = []

    def get_last_price(self, symbol: str, *, force_refresh: bool = False) -> dict:
        self.calls.append(symbol)
        return {
            "symbol": symbol,
            "last_price": self.price,
            "updated_at": "2026-06-14T12:00:00+00:00",
            "error": self.error,
        }


def _history() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": "2026-06-11", "close": 98.0},
            {"date": "2026-06-12", "close": 100.0},
            {"date": "2026-06-13", "close": 100.8},
        ]
    )


def test_spread_pct_and_alert_level_are_calculated_from_friday_close() -> None:
    provider = FakeProvider(price=101.5)
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping={"NVDA": "NVDAUSDT"},
        provider=provider,
        cache=FakeCache(),
    )

    row = rows[0]
    assert row["friday_close"] == 100.0
    assert row["friday_close_date"] == "2026-06-12"
    assert row["binance_last_price"] == 101.5
    assert round(row["spread_pct"], 2) == 1.5
    assert row["alert_level"] == "FOCUS"
    assert row["spread_direction"] == "Binance 高于周五收盘"
    assert provider.calls == ["NVDAUSDT"]


def test_alert_level_thresholds() -> None:
    assert classify_spread(0.49)["level"] == "IGNORE"
    assert classify_spread(0.8)["level"] == "OBSERVE"
    assert classify_spread(1.5)["level"] == "FOCUS"
    assert classify_spread(2.1)["level"] == "ABNORMAL"


def test_missing_symbol_mapping_does_not_call_provider() -> None:
    provider = FakeProvider(price=101.5)
    rows = build_weekend_spread_rows(["MSFT"], mapping={}, provider=provider, cache=FakeCache())

    assert rows[0]["status"] == "NO_MAPPING"
    assert rows[0]["alert_level_cn"] == "暂无映射"
    assert provider.calls == []


def test_binance_data_failure_is_unavailable_not_fake_price() -> None:
    provider = FakeProvider(price=None, error="timeout")
    rows = build_weekend_spread_rows(
        ["NVDA"],
        mapping={"NVDA": "NVDAUSDT"},
        provider=provider,
        cache=FakeCache(),
    )

    assert rows[0]["status"] == "BINANCE_UNAVAILABLE"
    assert rows[0]["binance_last_price"] is None
    assert rows[0]["spread_pct"] is None
    assert rows[0]["alert_level_cn"] == "数据不可用"


def test_missing_friday_close_does_not_call_provider() -> None:
    provider = FakeProvider(price=101.5)
    cache = FakeCache(pd.DataFrame([{"date": "2026-06-11", "close": 98.0}]))

    rows = build_weekend_spread_rows(["NVDA"], mapping={"NVDA": "NVDAUSDT"}, provider=provider, cache=cache)

    assert rows[0]["status"] == "MISSING_FRIDAY_CLOSE"
    assert rows[0]["friday_close"] is None
    assert provider.calls == []


def test_mapping_config_loads_normalized_symbols(tmp_path) -> None:
    path = tmp_path / "mapping.json"
    path.write_text(json.dumps({"mappings": {"nvda": "nvdausdt", "": "bad"}}), encoding="utf-8")

    assert load_binance_symbol_mapping(path) == {"NVDA": "NVDAUSDT"}
