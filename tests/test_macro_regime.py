from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
import json
import sqlite3

import pandas as pd

from data.macro_regime import (
    DOLLAR_INDEX,
    FEAR_GREED,
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
    MacroIndicatorSnapshot,
    MacroRegimeStore,
    evaluate_macro_regime,
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
            _indicator(FEAR_GREED, 28),
        ]
    )

    text = macro_regime_status_text(snapshot)

    assert "大盘环境" in text
    assert "恐惧与贪婪" in text
    assert "VIX 22.4" in text
    assert "高收益债利差 4.1%" in text
    assert "纪律提示" in text


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
    assert dollar.value == 120.7


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

    assert result["status"] == "partial"
    assert result["overall_status"] == "partial"
    assert result["indicators"][FEAR_GREED]["status"] == "cached_fallback"
    assert result["indicators"][FEAR_GREED]["used_cache"] is True
    assert loaded is not None
    assert loaded.value == 28
    assert loaded.error is not None


def test_fear_greed_stale_does_not_invalidate_vix_and_credit_regime() -> None:
    snapshot = evaluate_macro_regime(
        [
            _indicator(VIX, 27.0),
            _indicator(HY_OAS, 4.6),
            _indicator(FEAR_GREED, 18.0, is_stale=True),
        ]
    )

    assert snapshot.regime == REGIME_STRESS
    assert snapshot.data_status == "部分可用"
    assert snapshot.confidence == "中"


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
    assert result["failed_count"] == 0
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
    assert row[2] == 0
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

    assert "刷新大盘环境" in header_source
    assert "dashboard_refresh_macro_regime_cache" in header_source
    assert "_refresh_macro_cache_for_dashboard()" in header_source
    assert "refresh_macro_indicators" in inspect.getsource(dashboard._refresh_macro_cache_for_dashboard)
    assert "indicator_results" in refresh_result_source
    assert "大盘环境刷新完成" in refresh_result_source


def test_dashboard_portfolio_and_sell_pages_only_render_macro_hints() -> None:
    from ui import dashboard, portfolio, trade_journal

    dashboard_render = inspect.getsource(dashboard.render)
    dashboard_status = inspect.getsource(dashboard._render_dashboard_status_bar)
    dashboard_system = inspect.getsource(dashboard._render_dashboard_system_status)
    portfolio_hint = inspect.getsource(portfolio._render_macro_regime_buy_hint)
    trade_hint = inspect.getsource(trade_journal._render_macro_regime_sell_hint)

    assert "load_macro_regime" in dashboard_render
    assert "macro_regime_status_html" in dashboard_status
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
