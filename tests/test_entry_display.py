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


def test_entry_display_prefers_technical_pullback_when_value_zone_is_far() -> None:
    result = build_entry_display(
        current_price=120,
        buy_zone={"lower": 30, "upper": 50},
        chase_zone={"lower": 145},
        technical_entry_zone={
            "low": 108,
            "high": 116,
            "source": "ema_pullback",
            "reason": "强趋势结构下，技术回踩区参考 EMA20 / EMA50 / 近期支撑，并用 ATR 做缓冲",
        },
        data_status="OK",
        price_position="ABOVE_BUY_ZONE",
        decision="WAIT",
        final_score=78,
        valuation_score=45,
        risk_score=70,
    )

    assert result["entry_display_label"] == "等待技术回踩 $108.00 - $116.00"
    assert result["entry_action_hint"] == "只观察，等待技术回踩或基本面复核"
    assert result["valuation_deep_zone_label"] == "$30.00 - $50.00"
    assert "技术回踩区 $108.00 - $116.00" in result["entry_display_reason"]
    assert "深度估值区 $30.00 - $50.00" in result["entry_display_reason"]


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
    assert below["entry_display_label"] == "跌破买区 $90.00 - $100.00"
    assert "不等于更便宜" in below["entry_display_reason"]
    assert below["entry_action_hint"] == "跌破买区，先复核"


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
    assert format_zone_status("BELOW_BUY_ZONE") == "跌破买区，需复核"
    assert format_zone_status("ZONE_MISSING") == "无法判断"


def test_dashboard_watchlist_entry_cell_shows_compact_status_and_keeps_price_in_tooltip() -> None:
    row = pd.Series(
        {
            "symbol": "NVDA",
            "price": 110,
            "entryRating": "B - 等回踩",
            "entry_display_label": "等待回落 $90.00 - $100.00",
            "entry_action_hint": "只观察，等待回到纪律买区",
            "entry_display_reason": "当前高于买区 10%；追高禁区 >$120.00",
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

    assert "<strong>买区外</strong>" in html
    assert "<em>等回落</em>" in html
    assert "等待回落 $90.00 - $100.00" in html
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
            "entry_display_label": "暂无参考买区：缺估值指标",
            "entry_action_hint": "补齐数据后再复核",
            "entry_display_reason": "缺估值指标",
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

    assert "<strong>数据不足</strong>" in html
    assert "<em>补数据</em>" in html
    assert "暂无参考买区：缺估值指标" in html
    assert "暂无专属买区模型" not in html


def test_dashboard_watchlist_entry_cell_simplifies_buy_zone_statuses() -> None:
    cases = [
        ("IN_BUY_ZONE", "买区内 $90.00 - $100.00", "买区内", "可复核"),
        ("ABOVE_BUY_ZONE", "等待回落 $90.00 - $100.00", "买区外", "等回落"),
        ("ABOVE_BUY_ZONE", "等待技术回踩 $90.00 - $100.00", "买区外", "等回踩"),
        ("IN_CHASE_ZONE", "禁止追高，参考买区 $90.00 - $100.00", "追高区", "禁止新增"),
        ("BELOW_BUY_ZONE", "跌破买区 $90.00 - $100.00", "跌破买区", "先复核"),
    ]

    for price_position, display_label, compact_label, compact_hint in cases:
        row = pd.Series(
            {
                "symbol": "TEST",
                "entryRating": "B - 等回踩",
                "entry_display_label": display_label,
                "entry_action_hint": (
                    "进入追高区，禁止新增"
                    if price_position == "IN_CHASE_ZONE"
                    else "只观察，等待技术回踩或基本面复核"
                    if "技术回踩" in display_label
                    else "只观察，等待回到纪律买区"
                ),
                "entry_display_reason": "当前位于纪律买区",
                "radar_price_position": price_position,
            }
        )

        html = _entry_rating_cell_html(row)

        assert f"<strong>{compact_label}</strong>" in html
        assert f"<em>{compact_hint}</em>" in html


def test_dashboard_watchlist_entry_cell_prefers_radar_status_over_legacy_zone() -> None:
    row = pd.Series(
        {
            "symbol": "MSFT",
            "price": 397,
            "entryRating": "B - 等回踩",
            "entry_display_label": "买区内 $394.12 - $425.99",
            "entry_action_hint": "买区内但总分低于 70，需复核",
            "entry_display_reason": "当前位于纪律买区",
            "activeZone": SimpleNamespace(
                currentPrice=397,
                trancheBuyLow=241.88,
                trancheBuyHigh=300.37,
                noChaseAbove=520,
                currentZone="fair_observation",
            ),
        }
    )

    html = _entry_rating_cell_html(row)

    assert "<strong>买区内</strong>" in html
    assert "$394.12 - $425.99" in html
    assert "$241.88 - $300.37" not in html


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
        "build_ai_stock_radar_list_row",
        lambda *_args, **_kwargs: {
            "buy_zone": {"lower": 190, "upper": 210},
            "price_position": "ABOVE_BUY_ZONE",
            "decision": "WAIT",
            "data_status": "OK",
            "entry_reference_low": 190,
            "entry_reference_high": 210,
            "next_action_price": 210,
            "chase_above_price": 250,
            "current_vs_entry_pct": 4.8,
            "missing_entry_fields": [],
            "entry_display_label": "等待回落 $190.00 - $210.00",
            "entry_display_reason": "当前高于买区 4.8%",
            "entry_action_hint": "只观察，等待回到纪律买区",
        },
    )
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
    assert row["radar_buy_zone"] == {"lower": 190, "upper": 210}
    assert row["entry_reference_low"] == 190
    html = _entry_rating_cell_html(pd.Series(row))
    assert "<strong>买区外</strong>" in html
    assert "等待回落 $190.00 - $210.00" in html
    assert "$90.00 - $100.00" not in html
