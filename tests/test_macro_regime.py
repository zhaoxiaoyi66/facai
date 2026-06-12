from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
import sqlite3
import time

import pandas as pd

import data.macro_regime as macro_regime
from data.macro_regime import (
    DOLLAR_INDEX,
    FEAR_GREED,
    HYG_CREDIT_PROXY,
    HY_OAS,
    MARKET_BREADTH,
    MARKET_TREND,
    REGIME_DATA_GAP,
    REGIME_PANIC,
    REGIME_RISK_OFF,
    REGIME_STRESS,
    TEN_YEAR_YIELD,
    VIX,
    YIELD_CURVE_10Y2Y,
    SENTIMENT_PROXY,
    MacroIndicatorSnapshot,
    MacroRegimeStore,
    evaluate_macro_regime,
    load_macro_regime,
    macro_regime_sentiment_status_text,
    macro_regime_status_text,
    macro_regime_trade_hint_text,
    refresh_macro_indicators,
)
from data.prices import PriceCache


def _indicator(
    indicator: str,
    value: float,
    *,
    change_1d: float | None = None,
    change_5d: float | None = None,
    change_20d: float | None = None,
    is_stale: bool = False,
) -> MacroIndicatorSnapshot:
    return MacroIndicatorSnapshot(
        indicator=indicator,
        value=value,
        change_1d=change_1d,
        change_5d=change_5d,
        change_20d=change_20d,
        source="test",
        updated_at=datetime.now(timezone.utc).isoformat(),
        is_stale=is_stale,
    )


class FailingProvider:
    def get_quote(self, ticker: str, force_refresh: bool = False):
        raise RuntimeError("provider down")

    def get_price_history(self, ticker: str, force_refresh: bool = False):
        raise RuntimeError("provider down")


class SystemIndexHistoryProvider(FailingProvider):
    def __init__(self, path) -> None:
        self.path = path
        self.history_calls: list[tuple[str, bool]] = []

    def get_price_history(self, ticker: str, force_refresh: bool = False):
        self.history_calls.append((ticker.upper(), force_refresh))
        if ticker.upper() not in {"SPY", "QQQ"}:
            raise RuntimeError("only system indices are supported")
        closes = [100.0] * 219 + [110.0]
        _seed_history(self.path, ticker.upper(), closes)
        return PriceCache(self.path).get_history(ticker.upper(), max_age_hours=24 * 3650, min_rows=20)


class VixQuoteProvider(FailingProvider):
    def get_quote(self, ticker: str, force_refresh: bool = False):
        if ticker.upper() in {"^VIX", "VIX", "AVIX"}:
            return {"symbol": ticker.upper(), "price": 21.6, "date": "2026-06-10"}
        raise RuntimeError("only vix is supported")

    def get_price_history(self, ticker: str, force_refresh: bool = False):
        if ticker.upper() in {"^VIX", "VIX", "AVIX"}:
            return pd.DataFrame(
                {
                    "date": pd.date_range("2026-06-01", periods=10, freq="D"),
                    "close": [18.0, 18.5, 19.0, 20.0, 20.5, 21.0, 21.2, 21.4, 21.5, 21.6],
                }
            )
        raise RuntimeError("only vix is supported")


class TreasuryAndVixProvider(VixQuoteProvider):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int, int, bool]] = []

    def _get_json(
        self,
        endpoint: str,
        params: dict,
        timeout_seconds: int = 20,
        retries: int = 2,
        force_refresh: bool = False,
    ):
        self.calls.append((endpoint, dict(params), timeout_seconds, retries, force_refresh))
        if endpoint != "treasury-rates":
            raise RuntimeError(f"unsupported endpoint {endpoint}")
        return [
            {"date": "2026-06-08", "year2": 4.5, "year10": 4.2},
            {"date": "2026-06-09", "year2": 4.55, "year10": 4.35},
            {"date": "2026-06-10", "year2": 4.6, "year10": 4.42},
        ]


class ZeroAvixThenValidVixProvider(VixQuoteProvider):
    def __init__(self) -> None:
        self.quote_calls: list[str] = []

    def get_quote(self, ticker: str, force_refresh: bool = False):
        self.quote_calls.append(ticker.upper())
        if ticker.upper() == "AVIX":
            return {"symbol": ticker.upper(), "price": 0.0, "date": "2026-06-10"}
        if ticker.upper() in {"^VIX", "VIX"}:
            return {"symbol": ticker.upper(), "price": 22.2, "date": "2026-06-10"}
        raise RuntimeError("only vix is supported")


class ZeroVixProvider(VixQuoteProvider):
    def get_quote(self, ticker: str, force_refresh: bool = False):
        if ticker.upper() in {"AVIX", "^VIX", "VIX"}:
            return {"symbol": ticker.upper(), "price": 0.0, "date": "2026-06-10"}
        raise RuntimeError("only vix is supported")


def _fred_fetcher(series_values: dict[str, list[float]]):
    def fetch(series_id: str) -> str:
        values = series_values.get(series_id)
        if values is None:
            raise RuntimeError(f"{series_id} unavailable")
        rows = [f"observation_date,{series_id}"]
        for index, value in enumerate(values):
            rows.append(f"2026-06-{index + 1:02d},{value}")
        return "\n".join(rows)

    return fetch


def _seed_history(path, symbol: str, closes: list[float]) -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-10-01", periods=len(closes), freq="D"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )
    PriceCache(path).set_history(symbol, frame)


def _seed_macro_market_cache(path, *, spy_below_200: bool = False, qqq_below_50: bool = False) -> None:
    spy_closes = [100.0] * 220
    qqq_closes = [100.0] * 220
    spy_closes[-1] = 80.0 if spy_below_200 else 110.0
    qqq_closes[-1] = 90.0 if qqq_below_50 else 110.0
    _seed_history(path, "SPY", spy_closes)
    _seed_history(path, "QQQ", qqq_closes)
    _seed_history(path, "AAA", [100.0] * 219 + [110.0])
    _seed_history(path, "BBB", [100.0] * 219 + [90.0])
    _seed_history(path, "HYG", [100.0] * 220)
    _seed_history(path, "LQD", [100.0] * 220)
    _seed_history(path, "IEF", [100.0] * 220)


def test_high_vix_marks_risk_off() -> None:
    snapshot = evaluate_macro_regime([_indicator(VIX, 22.4)])

    assert snapshot.regime == REGIME_RISK_OFF
    assert "不追涨" in macro_regime_trade_hint_text(snapshot, context="buy")


def test_high_vix_and_widening_credit_spread_marks_stress() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 27.0),
            _indicator(HY_OAS, 4.2, change_5d=0.25),
        ]
    )

    assert snapshot.regime == REGIME_STRESS
    assert "C类暂停新增" in macro_regime_trade_hint_text(snapshot, context="buy")


def test_high_vix_and_extreme_fear_marks_panic() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 32.0),
            _indicator(HY_OAS, 4.8, change_5d=0.3),
            _indicator(FEAR_GREED, 18.0),
        ]
    )

    assert snapshot.regime == REGIME_PANIC
    assert "只做计划内核心仓" in macro_regime_trade_hint_text(snapshot, context="sell")


def test_stale_data_cannot_show_risk_on() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 12.5, is_stale=True),
            _indicator(HY_OAS, 2.9, change_5d=-0.2, is_stale=True),
            _indicator(FEAR_GREED, 55, is_stale=True),
        ]
    )

    assert snapshot.regime != "风险偏好"
    assert snapshot.regime in {"中性", REGIME_DATA_GAP}
    assert any("过期" in reason for reason in snapshot.reasons)


def test_macro_status_text_is_chinese_and_contains_three_indicators() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 22.4),
            _indicator(HY_OAS, 4.1, change_5d=0.2),
            MacroIndicatorSnapshot(indicator=FEAR_GREED, value=28, rating="fear", source="CNN Fear & Greed graphdata"),
        ]
    )

    text = macro_regime_status_text(snapshot)

    assert "大盘环境" in text
    assert "VIX 22.4" in text
    assert "高收益债利差" not in text
    assert "CNN恐惧与贪婪：28｜恐惧" in text
    assert "纪律提示" in text


def test_cnn_fear_greed_sentiment_text_shows_live_rating() -> None:
    snapshot = evaluate_macro_regime(
        [
            MacroIndicatorSnapshot(
                indicator=FEAR_GREED,
                value=34,
                rating="fear",
                source="CNN Fear & Greed graphdata",
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        ]
    )

    assert macro_regime_sentiment_status_text(snapshot) == "CNN恐惧与贪婪：34｜恐惧"


def test_cnn_fear_greed_sentiment_text_shows_cache_age() -> None:
    cached_at = datetime.now(timezone.utc) - timedelta(days=2, minutes=5)
    snapshot = evaluate_macro_regime(
        [
            MacroIndicatorSnapshot(
                indicator=FEAR_GREED,
                value=34,
                rating="fear",
                source="CNN Fear & Greed cache",
                updated_at=cached_at.isoformat(),
                fetched_at=cached_at.isoformat(),
                error="CNN HTTP 418",
            )
        ]
    )

    text = macro_regime_sentiment_status_text(snapshot)

    assert "CNN恐惧与贪婪：34｜恐惧｜缓存 2天前" in text
    assert "CNN HTTP 418" not in text


def test_cnn_fear_greed_missing_uses_internal_sentiment_proxy_label() -> None:
    snapshot = evaluate_macro_regime(
        [
            MacroIndicatorSnapshot(indicator=FEAR_GREED, value=None, error="CNN HTTP 418"),
            MacroIndicatorSnapshot(indicator=SENTIMENT_PROXY, value=35, source="internal sentiment proxy"),
        ]
    )

    text = macro_regime_sentiment_status_text(snapshot)

    assert text == "CNN恐惧与贪婪：暂缺｜情绪代理：偏恐惧"
    assert "CNN HTTP 418" not in text


def test_macro_store_supports_manual_cache_values(tmp_path) -> None:
    path = tmp_path / "macro.sqlite"
    store = MacroRegimeStore(path)
    updated_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()

    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator="BAMLH0A0HYM2",
            value=4.1,
            change_5d=0.2,
            source="manual FRED BAMLH0A0HYM2",
            updated_at=updated_at,
        )
    )

    loaded = store.load_indicator(HY_OAS)
    assert loaded is not None
    assert loaded.indicator == HY_OAS
    assert loaded.value == 4.1
    assert loaded.change_5d == 0.2
    assert loaded.source == "manual FRED BAMLH0A0HYM2"


def test_refresh_macro_indicators_uses_fred_when_vix_provider_fails(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.0, 20.8, 22.4, 22.9],
                "BAMLH0A0HYM2": [3.8, 3.9, 4.0, 4.1],
                "DGS10": [4.2, 4.3, 4.4, 4.5],
                "T10Y2Y": [-0.5, -0.4, -0.3, -0.2],
                "DTWEXBGS": [120.0, 120.4, 120.5, 120.7],
            }
        ),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 35, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    store = MacroRegimeStore(path)
    vix = store.load_indicator(VIX, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    hy = store.load_indicator(HY_OAS, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    ten_year = store.load_indicator(TEN_YEAR_YIELD, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    curve = store.load_indicator(YIELD_CURVE_10Y2Y, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    dollar = store.load_indicator(DOLLAR_INDEX, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "success"
    assert vix is not None
    assert vix.value == 22.9
    assert vix.source == "FRED VIXCLS"
    assert hy is not None
    assert hy.value == 4.1
    assert ten_year is not None
    assert ten_year.value == 4.5
    assert curve is not None
    assert curve.value == -0.2
    assert dollar is not None
    assert dollar.value is None
    assert result["indicators"][DOLLAR_INDEX]["status"] == "failed"


def test_vix_quote_success_skips_fred_vixcls(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    fred_calls: list[str] = []

    def fred_fetcher(series_id: str) -> str:
        fred_calls.append(series_id)
        return _fred_fetcher(
            {
                "BAMLH0A0HYM2": [3.8, 3.9],
                "DGS10": [4.2, 4.3],
                "T10Y2Y": [-0.3, -0.2],
                "DTWEXBGS": [120.0, 120.3],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=VixQuoteProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["indicators"][VIX]["status"] == "success"
    assert result["indicators"][VIX]["value"] == 21.6
    assert "VIXCLS" not in fred_calls


def test_zero_vix_quote_is_invalid_and_falls_back_to_next_symbol(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    provider = ZeroAvixThenValidVixProvider()
    fred_calls: list[str] = []

    result = refresh_macro_indicators(
        path,
        provider=provider,
        fred_fetcher=lambda series_id: fred_calls.append(series_id) or (_ for _ in ()).throw(RuntimeError("unexpected fred")),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["indicators"][VIX]["status"] == "success"
    assert result["indicators"][VIX]["value"] == 22.2
    assert result["indicators"][VIX]["source"].startswith("^VIX")
    assert provider.quote_calls[:2] == ["AVIX", "^VIX"]
    assert "VIXCLS" not in fred_calls


def test_zero_vix_falls_back_to_recent_cache_and_never_displays_zero(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=VIX,
            value=21.9,
            source="VIX cached",
            updated_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
        )
    )

    result = refresh_macro_indicators(
        path,
        provider=ZeroVixProvider(),
        fred_fetcher=lambda series_id: (_ for _ in ()).throw(RuntimeError("FRED timeout")),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    snapshot = load_macro_regime(path, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    status_text = macro_regime_status_text(snapshot)

    assert result["indicators"][VIX]["status"] == "cached_fallback"
    assert result["indicators"][VIX]["value"] == 21.9
    assert "VIX 0.0" not in status_text


def test_vix_recent_cache_is_used_before_fred_when_quotes_are_invalid(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=VIX,
            value=21.7,
            source="VIX cached",
            updated_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
        )
    )
    fred_calls: list[str] = []

    result = refresh_macro_indicators(
        path,
        provider=ZeroVixProvider(),
        fred_fetcher=lambda series_id: fred_calls.append(series_id) or (_ for _ in ()).throw(RuntimeError("unexpected fred")),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["indicators"][VIX]["status"] == "cached_fallback"
    assert result["indicators"][VIX]["value"] == 21.7
    assert "VIXCLS" not in fred_calls


def test_fred_zero_vix_is_invalid_and_falls_back_to_cache(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=VIX,
            value=22.8,
            source="VIX cached",
            updated_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
        )
    )

    def fred_fetcher(series_id: str) -> str:
        if series_id == "VIXCLS":
            return _fred_fetcher({"VIXCLS": [0.0]})(series_id)
        return _fred_fetcher(
            {
                "BAMLH0A0HYM2": [3.8],
                "DGS10": [4.2],
                "T10Y2Y": [-0.2],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["indicators"][VIX]["status"] == "cached_fallback"
    assert result["indicators"][VIX]["value"] == 22.8


def test_fmp_treasury_success_skips_fred_rates(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    provider = TreasuryAndVixProvider()
    fred_calls: list[str] = []

    def fred_fetcher(series_id: str) -> str:
        fred_calls.append(series_id)
        return _fred_fetcher(
            {
                "BAMLH0A0HYM2": [3.8, 3.9],
                "DTWEXBGS": [120.0, 120.3],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=provider,
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert result["indicators"][TEN_YEAR_YIELD]["value"] == 4.42
    assert result["indicators"][TEN_YEAR_YIELD]["source"] == "FMP Treasury"
    assert result["indicators"][YIELD_CURVE_10Y2Y]["value"] == -0.18
    assert result["indicators"][YIELD_CURVE_10Y2Y]["source"] == "FMP Treasury calculated"
    assert "DGS10" not in fred_calls
    assert "T10Y2Y" not in fred_calls
    assert provider.calls == [
        (
            "treasury-rates",
            {"from": "2026-04-26", "to": "2026-06-10"},
            3,
            0,
            True,
        )
    ]


def test_vix_refresh_failure_does_not_block_hy_oas_update(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    def fred_fetcher(series_id: str) -> str:
        if series_id == "VIXCLS":
            raise RuntimeError("fred vix unavailable")
        return _fred_fetcher(
            {
                "BAMLH0A0HYM2": [3.8, 3.9, 4.0, 4.2],
                "DGS10": [4.1, 4.2, 4.3, 4.4],
                "T10Y2Y": [-0.5, -0.4, -0.3, -0.2],
                "DTWEXBGS": [120, 121, 122, 123],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("cnn unavailable")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    hy = MacroRegimeStore(path).load_indicator(HY_OAS, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "partial"
    assert result["indicators"][VIX]["status"] == "failed"
    assert hy is not None
    assert hy.value == 4.2


def test_fred_refresh_runs_series_concurrently(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    starts: list[tuple[str, float]] = []

    def fred_fetcher(series_id: str) -> str:
        starts.append((series_id, time.perf_counter()))
        time.sleep(0.08)
        return _fred_fetcher(
            {
                "VIXCLS": [18.0, 19.0],
                "BAMLH0A0HYM2": [3.8, 3.9],
                "DGS10": [4.2, 4.3],
                "T10Y2Y": [-0.3, -0.2],
                "DTWEXBGS": [120.0, 120.3],
            }
        )(series_id)

    started = time.perf_counter()
    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    elapsed = time.perf_counter() - started
    fred_start_times = [moment for series, moment in starts if series != "VIXCLS"]

    assert result["status"] == "success"
    assert elapsed < 0.8
    assert max(fred_start_times) - min(fred_start_times) < 0.12


def test_fred_timeout_uses_recent_cache_for_credit_spread(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=HY_OAS,
            value=3.72,
            source="FRED cached BAMLH0A0HYM2",
            updated_at=datetime(2026, 6, 8, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 8, 19, tzinfo=timezone.utc).isoformat(),
        )
    )

    def fred_fetcher(series_id: str) -> str:
        if series_id == "BAMLH0A0HYM2":
            raise RuntimeError("FRED timeout 3.0s")
        return _fred_fetcher(
            {
                "VIXCLS": [18.0, 19.0],
                "DGS10": [4.2, 4.3],
                "T10Y2Y": [-0.3, -0.2],
                "DTWEXBGS": [120.0, 120.3],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    hy_result = result["indicators"][HY_OAS]
    assert result["status"] == "success"
    assert hy_result["status"] == "stale"
    assert hy_result["value"] == 3.72
    assert hy_result["used_cache"] is True
    assert hy_result["error"] is None
    assert result["indicators"][HYG_CREDIT_PROXY]["status"] == "success"


def test_hy_oas_timeout_uses_hyg_credit_proxy_when_no_cache(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    def fred_fetcher(series_id: str) -> str:
        if series_id == "BAMLH0A0HYM2":
            raise RuntimeError("FRED timeout 3.0s")
        return _fred_fetcher(
            {
                "VIXCLS": [18.0, 19.0],
                "DGS10": [4.2, 4.3],
                "T10Y2Y": [-0.3, -0.2],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    snapshot = load_macro_regime(path, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "success"
    assert result["indicators"][HY_OAS]["status"] == "failed"
    assert result["indicators"][HYG_CREDIT_PROXY]["status"] == "success"
    assert result["indicators"][HYG_CREDIT_PROXY]["category"] == "auxiliary"
    assert "信用proxy" in macro_regime_status_text(snapshot)


def test_fresh_hy_oas_cache_skips_foreground_fred(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=HY_OAS,
            value=3.72,
            source="FRED cached BAMLH0A0HYM2",
            updated_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
        )
    )
    fred_calls: list[str] = []

    def fred_fetcher(series_id: str) -> str:
        fred_calls.append(series_id)
        return _fred_fetcher({"DGS10": [4.2], "T10Y2Y": [-0.2], "DTWEXBGS": [120.0]})(series_id)

    result = refresh_macro_indicators(
        path,
        provider=TreasuryAndVixProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["indicators"][HY_OAS]["status"] == "cached_fallback"
    assert result["indicators"][HY_OAS]["category"] == "auxiliary"
    assert "BAMLH0A0HYM2" not in fred_calls


def test_default_fred_fetch_uses_short_foreground_timeouts(monkeypatch) -> None:
    timeouts: list[int] = []

    def fake_read_url_text(url: str, *, timeout_seconds: int):
        timeouts.append(timeout_seconds)
        raise RuntimeError("timeout")

    monkeypatch.setattr("data.macro_regime._read_url_text", fake_read_url_text)

    try:
        macro_regime._fetch_fred_payload("DGS10", fred_fetcher=None)
    except RuntimeError:
        pass

    assert timeouts == [2, 1]


def test_fred_circuit_breaker_skips_frontend_fred_refresh_after_repeated_timeouts(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    provider = TreasuryAndVixProvider()

    def timeout_fred(series_id: str) -> str:
        raise RuntimeError(f"{series_id} timeout")

    first = refresh_macro_indicators(
        path,
        provider=provider,
        fred_fetcher=timeout_fred,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    assert first["indicators"][HY_OAS]["status"] == "failed"

    second = refresh_macro_indicators(
        path,
        provider=provider,
        fred_fetcher=timeout_fred,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, 3, tzinfo=timezone.utc),
    )
    assert second["indicators"][HY_OAS]["status"] == "failed"

    fred_calls: list[str] = []

    def unexpected_fred(series_id: str) -> str:
        fred_calls.append(series_id)
        return _fred_fetcher({"BAMLH0A0HYM2": [3.9], "DTWEXBGS": [120.0]})(series_id)

    third = refresh_macro_indicators(
        path,
        provider=provider,
        fred_fetcher=unexpected_fred,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, 5, tzinfo=timezone.utc),
    )

    assert third["indicators"][HY_OAS]["status"] == "failed"
    assert "circuit" in str(third["indicators"][HY_OAS]["error"]).lower()
    assert fred_calls == []


def test_dollar_index_failure_does_not_change_overall_success(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    def fred_fetcher(series_id: str) -> str:
        if series_id == "DTWEXBGS":
            raise RuntimeError("dollar index timeout")
        return _fred_fetcher(
            {
                "VIXCLS": [18.0, 19.0],
                "BAMLH0A0HYM2": [3.8, 3.9],
                "DGS10": [4.2, 4.3],
                "T10Y2Y": [-0.3, -0.2],
            }
        )(series_id)

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert result["indicators"][DOLLAR_INDEX]["status"] == "failed"


def test_fear_greed_refresh_failure_uses_recent_cache(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=FEAR_GREED,
            value=28,
            source="CNN cached",
            updated_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
        )
    )

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 18.6],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
                "DTWEXBGS": [120.0, 120.2],
            }
        ),
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("cnn unavailable")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    loaded = MacroRegimeStore(path).load_indicator(FEAR_GREED, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "success"
    assert result["overall_status"] == "success"
    assert result["indicators"][FEAR_GREED]["status"] == "cached_fallback"
    assert result["indicators"][FEAR_GREED]["used_cache"] is True
    assert loaded is not None
    assert loaded.value == 28
    assert result["indicators"][SENTIMENT_PROXY]["status"] == "success"


def test_fear_greed_graphdata_success_writes_cnn_cache_with_rating(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 18.6],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=lambda url: {
            "fear_and_greed": {"score": 34, "rating": "fear", "timestamp": "2026-06-10T20:00:00+00:00"}
        },
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    loaded = MacroRegimeStore(path).load_indicator(FEAR_GREED, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["indicators"][FEAR_GREED]["status"] == "success"
    assert result["indicators"][FEAR_GREED]["value"] == 34
    assert result["indicators"][FEAR_GREED]["rating"] == "fear"
    assert loaded is not None
    assert loaded.source == "CNN Fear & Greed graphdata"
    assert loaded.rating == "fear"


def test_refresh_macro_indicators_attempts_cnn_provider_without_injected_fetcher(tmp_path, monkeypatch) -> None:
    from data.fear_greed_provider import FearGreedReading

    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    calls: list[str] = []

    def fake_cnn_provider(*, fetcher, url, timeout_seconds, now):
        calls.append(url)
        assert fetcher is None
        assert timeout_seconds <= 5
        return FearGreedReading(
            value=34,
            rating="fear",
            observation_date="2026-06-10",
            source="CNN Fear & Greed graphdata",
            raw_payload="{}",
        )

    monkeypatch.setattr(macro_regime, "fetch_cnn_fear_greed", fake_cnn_provider)

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 18.6],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert calls == [macro_regime.CNN_FEAR_GREED_URL]
    assert result["indicators"][FEAR_GREED]["status"] == "success"
    assert result["indicators"][FEAR_GREED]["value"] == 34
    assert result["indicators"][FEAR_GREED]["source"] == "CNN Fear & Greed graphdata"


def test_fear_greed_same_day_success_cache_skips_cnn_request(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    MacroRegimeStore(path).save_indicator(
        MacroIndicatorSnapshot(
            indicator=FEAR_GREED,
            value=34,
            rating="fear",
            source="CNN Fear & Greed graphdata",
            observation_date="2026-06-10",
            updated_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 10, 19, tzinfo=timezone.utc).isoformat(),
        )
    )
    cnn_calls: list[str] = []

    def unexpected_cnn(url: str):
        cnn_calls.append(url)
        raise AssertionError("same-day CNN cache should skip network")

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 18.6],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=unexpected_cnn,
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert cnn_calls == []
    assert result["indicators"][FEAR_GREED]["status"] == "cached_fallback"
    assert result["indicators"][FEAR_GREED]["used_cache"] is True
    assert result["indicators"][SENTIMENT_PROXY]["status"] == "success"


def test_stale_fear_greed_cache_uses_internal_sentiment_proxy(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    store = MacroRegimeStore(path)
    store.save_indicator(
        MacroIndicatorSnapshot(
            indicator=FEAR_GREED,
            value=28,
            source="CNN cached",
            updated_at=datetime(2026, 6, 4, 19, tzinfo=timezone.utc).isoformat(),
            fetched_at=datetime(2026, 6, 4, 19, tzinfo=timezone.utc).isoformat(),
        )
    )

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 22.0],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("CNN HTTP 418")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["indicators"][FEAR_GREED]["status"] == "stale"
    assert result["indicators"][SENTIMENT_PROXY]["status"] == "success"


def test_fear_greed_http_418_uses_internal_sentiment_proxy_without_cache(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 22.0],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("CNN HTTP 418")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    snapshot = load_macro_regime(path, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "success"
    assert result["indicators"][FEAR_GREED]["status"] == "failed"
    assert result["indicators"][SENTIMENT_PROXY]["status"] == "success"
    assert result["indicators"][SENTIMENT_PROXY]["category"] == "auxiliary"
    assert "情绪代理" in macro_regime_status_text(snapshot)


def test_fear_greed_http_418_opens_circuit_and_skips_next_frontend_request(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    cnn_calls: list[str] = []

    def failing_fear_greed(url: str):
        cnn_calls.append(url)
        raise RuntimeError("CNN HTTP 418")

    first = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 22.0],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=failing_fear_greed,
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    second = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.5, 22.0],
                "BAMLH0A0HYM2": [3.6, 3.7],
                "DGS10": [4.2, 4.4],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=failing_fear_greed,
        now=datetime(2026, 6, 10, 21, 5, tzinfo=timezone.utc),
    )

    assert first["indicators"][FEAR_GREED]["status"] == "failed"
    assert second["indicators"][FEAR_GREED]["status"] == "failed"
    assert "circuit" in str(second["indicators"][FEAR_GREED]["error"]).lower()
    assert second["indicators"][SENTIMENT_PROXY]["status"] == "success"
    assert len(cnn_calls) == 1


def test_fear_greed_stale_does_not_invalidate_vix_and_credit_regime() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 27.0),
            _indicator(HY_OAS, 4.6),
            _indicator(FEAR_GREED, 18.0, is_stale=True),
        ]
    )

    assert snapshot.regime == REGIME_STRESS
    assert snapshot.data_status == "核心部分可用｜辅助缺失"
    assert snapshot.confidence == "低"


def test_market_trend_and_breadth_use_local_price_cache(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path, qqq_below_50=True)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.0, 18.2],
                "BAMLH0A0HYM2": [3.7, 3.8],
                "DGS10": [4.0, 4.1],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    store = MacroRegimeStore(path)
    trend = store.load_indicator(MARKET_TREND, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    breadth = store.load_indicator(MARKET_BREADTH, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "success"
    assert trend is not None
    assert trend.value and trend.value >= 58
    assert "QQQ 跌破 50 日均线" in " ".join(trend.action_hints + trend.reasons)
    assert breadth is not None
    assert breadth.value == 50.0


def test_macro_refresh_updates_spy_qqq_system_index_cache_when_missing(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_history(path, "AAA", [100.0] * 219 + [110.0])
    _seed_history(path, "BBB", [100.0] * 219 + [90.0])
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])
    provider = SystemIndexHistoryProvider(path)

    result = refresh_macro_indicators(
        path,
        provider=provider,
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.0, 18.2],
                "BAMLH0A0HYM2": [3.7, 3.8],
                "DGS10": [4.0, 4.1],
                "T10Y2Y": [-0.3, -0.2],
            }
        ),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 45, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    trend = MacroRegimeStore(path).load_indicator(MARKET_TREND, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    assert result["indicators"][MARKET_TREND]["status"] == "success"
    assert ("SPY", True) in provider.history_calls
    assert ("QQQ", True) in provider.history_calls
    assert trend is not None
    assert trend.value is not None


def test_vix_and_market_breadth_keep_macro_regime_partially_usable() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 22.0),
            MacroIndicatorSnapshot(
                indicator=MARKET_BREADTH,
                value=35.0,
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
        ]
    )

    assert snapshot.regime != REGIME_DATA_GAP
    assert snapshot.data_status == "核心部分可用｜辅助缺失"


def test_core_macro_indicators_available_do_not_need_auxiliary_indicators() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 22.0),
            _indicator(TEN_YEAR_YIELD, 4.4),
            _indicator(YIELD_CURVE_10Y2Y, -0.2),
            MacroIndicatorSnapshot(
                indicator=MARKET_TREND,
                value=30,
                raw_payload='{"QQQ": {"above_50": true, "above_200": true}, "SPY": {"above_50": true, "above_200": true}}',
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
            MacroIndicatorSnapshot(
                indicator=MARKET_BREADTH,
                value=38.0,
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
        ]
    )

    assert snapshot.regime != REGIME_DATA_GAP
    assert snapshot.data_status == "核心完整｜辅助缺失"
    assert snapshot.confidence == "高"
    assert "VIX 22.0" in macro_regime_status_text(snapshot)
    assert "数据：核心完整｜辅助缺失" in macro_regime_status_text(snapshot)
    assert "高收益债利差" not in macro_regime_status_text(snapshot)


def test_macro_data_status_keeps_core_complete_when_auxiliary_has_proxy_only() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 19.4),
            _indicator(TEN_YEAR_YIELD, 4.5),
            _indicator(YIELD_CURVE_10Y2Y, -0.2),
            MacroIndicatorSnapshot(
                indicator=MARKET_TREND,
                value=30,
                raw_payload='{"QQQ": {"above_50": true, "above_200": true}, "SPY": {"above_50": true, "above_200": true}}',
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
            MacroIndicatorSnapshot(
                indicator=MARKET_BREADTH,
                value=41.9,
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
            MacroIndicatorSnapshot(indicator=HY_OAS, value=None, error="FRED timeout"),
            MacroIndicatorSnapshot(indicator=FEAR_GREED, value=None, error="CNN HTTP 418"),
            MacroIndicatorSnapshot(indicator=HYG_CREDIT_PROXY, value=65, source="HYG proxy"),
            MacroIndicatorSnapshot(indicator=SENTIMENT_PROXY, value=35, source="internal sentiment proxy"),
        ]
    )

    text = macro_regime_status_text(snapshot)
    assert snapshot.regime != REGIME_DATA_GAP
    assert snapshot.data_status == "核心完整｜辅助缺失"
    assert "数据：核心完整｜辅助缺失" in text
    assert "FRED timeout" not in text
    assert "CNN HTTP 418" not in text


def test_macro_indicator_detail_status_uses_cache_semantics_for_error_with_value() -> None:
    snapshot = MacroIndicatorSnapshot(
        indicator=TEN_YEAR_YIELD,
        value=4.5,
        source="FMP Treasury",
        error="FMP timeout WinError 10060",
        updated_at=datetime.now(timezone.utc).isoformat(),
    )

    assert macro_regime._indicator_cache_status_text(snapshot) == "使用缓存"
    assert (
        macro_regime._indicator_cache_status_text(
            MacroIndicatorSnapshot(
                indicator=FEAR_GREED,
                value=28,
                source="CNN Fear & Greed cache",
                is_stale=True,
            )
        )
        == "过期缓存"
    )


def test_refresh_macro_indicators_returns_observable_result_and_log(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    _seed_macro_market_cache(path)
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=_fred_fetcher(
            {
                "VIXCLS": [18.0, 19.0],
                "BAMLH0A0HYM2": [3.8, 3.9],
                "DGS10": [4.2, 4.3],
                "T10Y2Y": [-0.3, -0.2],
                "DTWEXBGS": [120.0, 120.3],
            }
        ),
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 41, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["status"] == "success"
    assert result["overall_status"] == "success"
    assert result["duration_seconds"] >= 0
    assert result["refreshed_count"] >= 7
    assert result["failed_count"] == 1
    assert result["indicator_results"]
    vix_result = result["indicators"][VIX]
    assert vix_result["status"] == "success"
    assert vix_result["source"] == "FRED VIXCLS"
    assert "duration_seconds" in vix_result
    assert "observation_date" in vix_result

    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT overall_status, refreshed_count, failed_count, result_json FROM macro_refresh_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row[0] == "success"
    assert row[1] >= 7
    assert row[2] == 1
    assert json.loads(row[3])["overall_status"] == "success"


def test_all_macro_refresh_failures_return_failed_status(tmp_path, monkeypatch) -> None:
    path = tmp_path / "macro.sqlite"
    monkeypatch.setattr("data.macro_regime.load_watchlist", lambda: ["AAA", "BBB"])

    result = refresh_macro_indicators(
        path,
        provider=FailingProvider(),
        fred_fetcher=lambda series_id: (_ for _ in ()).throw(RuntimeError(f"{series_id} timeout")),
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("cnn timeout")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    assert result["status"] == "failed"
    assert result["failed_count"] >= 7
    assert result["indicators"][VIX]["status"] == "failed"
    assert "timeout" in str(result["error"])


def test_high_vix_and_qqq_below_50_is_at_least_risk_off() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 22.0),
            MacroIndicatorSnapshot(
                indicator=MARKET_TREND,
                value=58,
                raw_payload='{"QQQ": {"above_50": false, "above_200": true}, "SPY": {"above_50": true, "above_200": true}}',
                updated_at=datetime.now(timezone.utc).isoformat(),
            ),
        ]
    )

    assert snapshot.regime == REGIME_RISK_OFF
    assert any("QQQ 跌破 50 日均线" in hint for hint in snapshot.action_hints)


def test_ten_year_fast_rise_adds_growth_valuation_pressure_hint() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 18.0),
            _indicator(TEN_YEAR_YIELD, 4.7, change_20d=0.42),
        ]
    )

    assert any("成长股估值压力" in hint for hint in snapshot.action_hints)
    assert any("10年美债快速上行" in reason for reason in snapshot.reasons)


def test_dashboard_refresh_buttons_call_macro_refresh() -> None:
    from ui import dashboard

    header_source = inspect.getsource(dashboard._render_dashboard_header)
    refresh_result_source = inspect.getsource(dashboard._render_macro_refresh_result)
    indicator_row_source = inspect.getsource(dashboard._macro_refresh_indicator_row_html)

    assert "刷新大盘环境" in header_source
    assert "dashboard_refresh_macro_regime_cache" in header_source
    assert "RefreshMode.MACRO_ONLY" in header_source
    assert "RefreshMode.MACRO_ONLY" in inspect.getsource(dashboard._refresh_macro_cache_for_dashboard)
    assert "indicator_results" in refresh_result_source
    assert "大盘环境刷新完成" in refresh_result_source
    assert "核心指标" in refresh_result_source
    assert "辅助指标" in refresh_result_source
    assert "macro-refresh-diagnostics" in indicator_row_source


def test_macro_refresh_display_states_hide_raw_errors_from_main_view() -> None:
    from ui import dashboard

    cached_html = dashboard._macro_refresh_indicator_row_html(
        {
            "indicator": TEN_YEAR_YIELD,
            "label": "10年美债收益率",
            "status": "cached_fallback",
            "value": 4.5,
            "source": "FMP Treasury 缓存",
            "observation_date": "2026-06-12",
            "duration_seconds": 0.2,
            "used_cache": True,
            "error": "FMP timeout WinError 10060",
            "category": "core",
        }
    )
    proxy_html = dashboard._macro_refresh_indicator_row_html(
        {
            "indicator": HYG_CREDIT_PROXY,
            "label": "信用风险代理",
            "status": "success",
            "value": 65,
            "source": "HYG proxy",
            "observation_date": "2026-06-12",
            "duration_seconds": 0.1,
            "category": "auxiliary",
        }
    )
    missing_html = dashboard._macro_refresh_indicator_row_html(
        {
            "indicator": HY_OAS,
            "label": "美高收益债信用利差",
            "status": "failed",
            "value": None,
            "source": "FRED",
            "duration_seconds": 1.0,
            "error": "FRED timeout 3.0s",
            "category": "auxiliary",
        }
    )
    summary_html = dashboard._macro_refresh_error_summary_html(
        [
            {
                "indicator": HY_OAS,
                "status": "failed",
                "value": None,
                "error": "FRED timeout 3.0s",
                "category": "auxiliary",
            },
            {
                "indicator": FEAR_GREED,
                "status": "failed",
                "value": None,
                "error": "CNN HTTP 418",
                "category": "auxiliary",
            },
        ],
        "hy_oas: FRED timeout 3.0s; fear_greed: CNN HTTP 418",
    )

    assert "使用缓存" in cached_html
    assert "FMP timeout WinError 10060" in cached_html
    assert cached_html.index("诊断详情") < cached_html.index("FMP timeout WinError 10060")
    assert "使用代理" in proxy_html
    assert "暂缺" in missing_html
    assert "错误：FRED timeout" not in missing_html
    assert "辅助指标缺失" in summary_html
    assert "核心判断仍可用" in summary_html
    assert summary_html.index("完整技术诊断") < summary_html.index("CNN HTTP 418")


def test_dashboard_portfolio_and_sell_pages_only_render_macro_hints() -> None:
    from ui import dashboard, portfolio, trade_journal

    dashboard_render = inspect.getsource(dashboard.render)
    dashboard_status = inspect.getsource(dashboard._render_dashboard_status_bar)
    dashboard_system = inspect.getsource(dashboard._render_dashboard_system_status)
    portfolio_hint = inspect.getsource(portfolio._render_macro_regime_buy_hint)
    trade_hint = inspect.getsource(trade_journal._render_macro_regime_sell_hint)

    assert "load_macro_regime" in dashboard_render
    assert "dashboard-command-center" in dashboard_status
    assert "macro_regime_status_html" not in dashboard_status
    assert "macro_regime_detail_html" in dashboard_system
    assert "macro_regime_trade_hint_text" in portfolio_hint
    assert "macro_regime_trade_hint_text" in trade_hint
    assert "submit_portfolio_buy_add" not in portfolio_hint
    assert "apply_trade_to_portfolio" not in trade_hint


def test_macro_module_is_not_imported_by_gate_or_sync_logic() -> None:
    import data.decision_log as decision_log
    import data.portfolio_trade_sync as portfolio_trade_sync
    import data.trade_safety_gate as trade_safety_gate

    assert "macro_regime" not in inspect.getsource(trade_safety_gate)
    assert "macro_regime" not in inspect.getsource(portfolio_trade_sync)
    assert "macro_regime" not in inspect.getsource(decision_log)
