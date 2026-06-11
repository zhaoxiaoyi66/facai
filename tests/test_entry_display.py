from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from data.entry_display import build_entry_display, format_buy_zone, format_zone_status
from ui.dashboard_tables import _entry_rating_cell_html


BUY_ZONE = {"lower": 90, "upper": 100}
CHASE_ZONE = {"lower": 120}


def test_entry_display_above_buy_zone_is_consistent() -> None:
    result = build_entry_display(
        current_price=110,
        buy_zone=BUY_ZONE,
        chase_zone=CHASE_ZONE,
        data_status="OK",
        price_position="ABOVE_BUY_ZONE",
        decision="WAIT",
        final_score=82,
        valuation_score=60,
        risk_score=70,
    )

    assert result["entry_display_label"] == "等待回落 $90.00 - $100.00"
    assert result["entry_reference_low"] == 90
    assert result["entry_reference_high"] == 100
    assert result["chase_above_price"] == 120
    assert result["current_vs_entry_pct"] == 10.0
    assert "追高禁区 >$120.00" in result["entry_display_reason"]


def test_entry_display_inside_buy_zone_preserves_wait_hint() -> None:
    result = build_entry_display(
        current_price=95,
        buy_zone=BUY_ZONE,
        chase_zone=CHASE_ZONE,
        data_status="OK",
        price_position="IN_BUY_ZONE",
        decision="WAIT",
        final_score=65,
        valuation_score=60,
        risk_score=70,
    )

    assert result["entry_display_label"] == "买区内 $90.00 - $100.00"
    assert result["entry_action_hint"] == "买区内但总分低于 70，需复核"


def test_entry_display_chase_and_below_buy_zone_are_explicit() -> None:
    chase = build_entry_display(
        current_price=125,
        buy_zone=BUY_ZONE,
        chase_zone=CHASE_ZONE,
        data_status="OK",
        price_position="IN_CHASE_ZONE",
        decision="BLOCK_CHASE",
        final_score=82,
        valuation_score=60,
        risk_score=70,
    )
    below = build_entry_display(
        current_price=80,
        buy_zone=BUY_ZONE,
        chase_zone=CHASE_ZONE,
        data_status="OK",
        price_position="BELOW_BUY_ZONE",
        decision="WAIT",
        final_score=82,
        valuation_score=60,
        risk_score=70,
    )

    assert chase["entry_display_label"].startswith("禁止追高")
    assert chase["entry_action_hint"] == "进入追高区，禁止新增"
    assert below["entry_display_label"] == "低于买区 $90.00 - $100.00"
    assert "不等于自动更便宜" in below["entry_display_reason"]


def test_entry_display_missing_data_shows_specific_reason() -> None:
    result = build_entry_display(
        current_price=None,
        buy_zone={},
        chase_zone={},
        data_status="MISSING_PRICE",
        price_position="ZONE_MISSING",
        decision="DATA_MISSING",
        final_score=82,
        valuation_score=60,
        risk_score=70,
    )

    assert result["entry_display_label"] == "暂无参考买区：缺当前价格"
    assert result["missing_entry_fields"] == ["缺当前价格"]
    assert result["entry_action_hint"] == "补齐数据后再复核"


def test_entry_display_uses_explicit_missing_fields() -> None:
    result = build_entry_display(
        current_price=110,
        buy_zone={},
        data_status="MISSING_BUY_ZONE",
        price_position="ZONE_MISSING",
        missing_entry_fields=["暂无专属买区模型", "无法生成纪律买区"],
    )

    assert result["entry_display_label"] == "暂无参考买区：暂无专属买区模型、无法生成纪律买区"
    assert result["missing_entry_fields"] == ["暂无专属买区模型", "无法生成纪律买区"]


def test_zone_formatters_are_shared() -> None:
    assert format_buy_zone(BUY_ZONE) == "$90.00 - $100.00"
    assert format_zone_status("IN_BUY_ZONE") == "买区内"
    assert format_zone_status("ZONE_MISSING") == "无法判断"


def test_dashboard_watchlist_entry_cell_shows_price_reference() -> None:
    row = pd.Series(
        {
            "symbol": "NVDA",
            "price": 110,
            "entryRating": "B - 等回踩",
            "activeZone": SimpleNamespace(
                currentPrice=110,
                trancheBuyLow=90,
                trancheBuyHigh=100,
                noChaseAbove=120,
                currentZone="fair_observation",
            ),
        }
    )

    html = _entry_rating_cell_html(row)

    assert "等待回落 $90.00 - $100.00" in html
    assert "只观察，等待回到纪律买区" in html
    assert "追高禁区 &gt;$120.00" in html


def test_dashboard_watchlist_entry_cell_uses_two_line_layout() -> None:
    import inspect
    import ui.dashboard as dashboard

    source = inspect.getsource(dashboard._render_dashboard_styles)

    assert ".entry-rating-token" in source
    assert "flex-direction:column" in source
    assert "min-height:34px" in source


def test_dashboard_watchlist_missing_buy_zone_shows_engine_reason() -> None:
    row = pd.Series(
        {
            "symbol": "NVO",
            "price": 42.81,
            "entryRating": "C - 只观察",
            "activeZone": SimpleNamespace(
                currentPrice=42.81,
                trancheBuyLow=None,
                trancheBuyHigh=None,
                noChaseAbove=None,
                currentZone="unsupported_buy_zone_model",
                explainability={"missingInputs": ["专属买区模型"]},
            ),
        }
    )

    html = _entry_rating_cell_html(row)

    assert "暂无参考买区：暂无专属买区模型、无法生成纪律买区" in html
    assert "缺 52 周高低" not in html


def test_dashboard_row_keeps_generated_buy_zone_for_entry_display(monkeypatch) -> None:
    import data.dashboard_row_builder as builder

    zone = SimpleNamespace(
        currentPrice=110,
        trancheBuyLow=90,
        trancheBuyHigh=100,
        noChaseAbove=120,
        currentZone="fair_observation",
        combinedEntry={},
    )

    class DummyPlanStore:
        def get_plan(self, _symbol: str) -> dict:
            return {}

    monkeypatch.setattr(builder, "derive_dashboard_buy_zone", lambda *_args, **_kwargs: zone)
    monkeypatch.setattr(builder, "buy_zone_with_manual_override", lambda buy_zone, _plan: buy_zone)
    monkeypatch.setattr(builder, "StockPlanStore", DummyPlanStore)
    monkeypatch.setattr(
        builder,
        "derive_dashboard_final_decision",
        lambda *_args, **_kwargs: SimpleNamespace(
            currentAddLimitPercent=0,
            maxPortfolioWeightPercent=5,
            finalAction="只观察",
            decisionLane="wait",
            displayCategory="持有观察",
            isActionable=False,
            blockReasons=[],
            reviewReasons=[],
            dataConfidence="high",
        ),
    )
    score = SimpleNamespace(
        risk_flags=[],
        trading_signals=[],
        scoring_model="SEMICONDUCTOR",
        data_quality_pct=100,
        data_confidence="high",
        missing_data=[],
        quality_rating="A",
        entry_rating="B - 等回踩",
        risk_rating="低",
        valuation_status="合理",
        action="只观察",
        proxy_confidence="high",
        missing_industry_metrics=[],
        proxy_metrics_used=[],
        key_positives=[],
        key_risks=[],
        total_score=75,
        value_zone="合理",
        rating="B",
        max_suggested_position_percent=0,
        overheat_score=0,
        overheat_status="正常",
        overheat_action="只观察",
        overheat_recommendation="等待回落",
        overheat_reasons=[],
    )

    row = builder.build_dashboard_row(
        "NVDA",
        {"ticker": "NVDA", "current_price": 110, "market_cap": 1_000_000_000},
        {"price": 110},
        score,
        {"pct": 100, "missing": []},
    )

    assert row["activeZone"] is zone
    html = _entry_rating_cell_html(pd.Series(row))
    assert "等待回落 $90.00 - $100.00" in html
