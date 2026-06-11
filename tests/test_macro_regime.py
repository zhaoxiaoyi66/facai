from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect

from data.macro_regime import (
    FEAR_GREED,
    HY_OAS,
    REGIME_DATA_GAP,
    REGIME_PANIC,
    REGIME_RISK_OFF,
    REGIME_STRESS,
    VIX,
    MacroIndicatorSnapshot,
    MacroRegimeStore,
    evaluate_macro_regime,
    macro_regime_status_text,
    macro_regime_trade_hint_text,
    refresh_macro_indicators,
)


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


def test_refresh_macro_indicators_uses_fred_when_vix_provider_fails(tmp_path) -> None:
    class FailingProvider:
        def get_quote(self, ticker: str, force_refresh: bool = False):
            raise RuntimeError("provider down")

        def get_price_history(self, ticker: str, force_refresh: bool = False):
            raise RuntimeError("provider down")

    def fred_fetcher(series_id: str) -> str:
        return "\n".join(
            [
                f"observation_date,{series_id}",
                "2026-06-05,18.0",
                "2026-06-08,20.8",
                "2026-06-09,22.4",
                "2026-06-10,22.9",
            ]
        )

    result = refresh_macro_indicators(
        tmp_path / "macro.sqlite",
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: {"fear_and_greed": {"score": 35, "timestamp": "2026-06-10T20:00:00+00:00"}},
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    store = MacroRegimeStore(tmp_path / "macro.sqlite")
    vix = store.load_indicator(VIX, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))
    hy = store.load_indicator(HY_OAS, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "success"
    assert vix is not None
    assert vix.value == 22.9
    assert vix.source == "FRED VIXCLS"
    assert hy is not None
    assert hy.value == 22.9


def test_vix_refresh_failure_does_not_block_hy_oas_update(tmp_path) -> None:
    class FailingProvider:
        def get_quote(self, ticker: str, force_refresh: bool = False):
            raise RuntimeError("provider down")

        def get_price_history(self, ticker: str, force_refresh: bool = False):
            raise RuntimeError("provider down")

    def fred_fetcher(series_id: str) -> str:
        if series_id == "VIXCLS":
            raise RuntimeError("fred vix unavailable")
        return "\n".join(
            [
                "observation_date,BAMLH0A0HYM2",
                "2026-06-05,3.8",
                "2026-06-08,3.9",
                "2026-06-09,4.0",
                "2026-06-10,4.2",
            ]
        )

    result = refresh_macro_indicators(
        tmp_path / "macro.sqlite",
        provider=FailingProvider(),
        fred_fetcher=fred_fetcher,
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("cnn unavailable")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )

    hy = MacroRegimeStore(tmp_path / "macro.sqlite").load_indicator(HY_OAS, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["status"] == "partial"
    assert result["indicators"][VIX]["status"] == "failed"
    assert hy is not None
    assert hy.value == 4.2


def test_fear_greed_refresh_failure_uses_recent_cache(tmp_path) -> None:
    class FailingProvider:
        def get_quote(self, ticker: str, force_refresh: bool = False):
            raise RuntimeError("provider down")

        def get_price_history(self, ticker: str, force_refresh: bool = False):
            raise RuntimeError("provider down")

    path = tmp_path / "macro.sqlite"
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
        fred_fetcher=lambda series_id: "\n".join([f"observation_date,{series_id}", "2026-06-10,18.5"]),
        fear_greed_fetcher=lambda url: (_ for _ in ()).throw(RuntimeError("cnn unavailable")),
        now=datetime(2026, 6, 10, 21, tzinfo=timezone.utc),
    )
    loaded = MacroRegimeStore(path).load_indicator(FEAR_GREED, now=datetime(2026, 6, 10, 22, tzinfo=timezone.utc))

    assert result["indicators"][FEAR_GREED]["status"] == "cached_fallback"
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


def test_dashboard_refresh_buttons_call_macro_refresh() -> None:
    from ui import dashboard

    header_source = inspect.getsource(dashboard._render_dashboard_header)

    assert "_refresh_macro_cache_for_dashboard()" in header_source
    assert "refresh_macro_indicators" in inspect.getsource(dashboard._refresh_macro_cache_for_dashboard)


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
