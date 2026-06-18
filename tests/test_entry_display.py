from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from data.entry_display import build_entry_display, format_buy_zone, format_zone_status
from ui.dashboard_tables import _dashboard_compact_entry_text, _dashboard_entry_display, _decision_table_cell_html, _entry_rating_cell_html


BUY_ZONE = {"lower": 90, "upper": 100}
CHASE_ZONE = {"lower": 120}
LEGACY_DISPLAY_CONTEXT = {"unit_test_context": True}


def test_entry_display_without_buy_zone_context_shows_technical_insufficient() -> None:
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

    assert result["entry_display_label"] == "技术承接数据不足"
    assert result["entry_context_status"] == "DATA_INSUFFICIENT"
    assert result["missing_entry_fields"] == ["buy_zone_context"]


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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "等待回落 $90.00 - $100.00"
    assert result["entry_reference_low"] == 90
    assert result["entry_reference_high"] == 100
    assert result["chase_above_price"] == 120
    assert result["current_vs_entry_pct"] == 10.0
    assert "追高风险区 >$120.00" in result["entry_display_reason"]


def test_entry_display_stale_data_reason_is_chinese() -> None:
    result = build_entry_display(
        current_price=95,
        buy_zone=BUY_ZONE,
        chase_zone=CHASE_ZONE,
        data_status="STALE",
        price_position="IN_BUY_ZONE",
        decision="WAIT",
        final_score=70,
        valuation_score=60,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["missing_entry_fields"] == ["数据过期"]
    assert "数据过期" in result["entry_display_reason"]
    assert "stale" not in result["entry_display_reason"].lower()


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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "等待技术回踩 $108.00 - $116.00"
    assert result["entry_action_hint"] == "只观察，等待技术回踩或基本面复核"
    assert result["technical_position"] == "ABOVE_TECHNICAL_PULLBACK_ZONE"
    assert result["entry_context_status"] == "ABOVE_TECHNICAL_PULLBACK_ZONE"
    assert result["valuation_deep_zone_label"] == "$30.00 - $50.00"
    assert "技术回踩区 $108.00 - $116.00" in result["entry_display_reason"]
    assert "深度估值区 $30.00 - $50.00" in result["entry_display_reason"]


def test_entry_display_marks_price_inside_technical_pullback_zone() -> None:
    result = build_entry_display(
        current_price=113,
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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "回踩区内 $108.00 - $116.00"
    assert result["entry_action_hint"] == "需复核，不自动买入"
    assert result["technical_position"] == "IN_TECHNICAL_PULLBACK_ZONE"
    assert result["entry_context_status"] == "IN_TECHNICAL_PULLBACK_ZONE"
    assert "当前价已进入技术回踩区上沿" in result["entry_display_reason"]
    assert "深度估值区 $30.00 - $50.00" in result["entry_display_reason"]


def test_entry_display_treats_nan_technical_zone_as_missing() -> None:
    result = build_entry_display(
        current_price=120,
        buy_zone={"lower": 30, "upper": 50},
        chase_zone={"lower": 145},
        technical_entry_zone={
            "low": float("nan"),
            "high": float("nan"),
            "source": "missing_technical_data",
            "reason": "技术回踩区暂缺：缺 K 线历史 / EMA，不能生成技术回踩区",
            "missing_fields": ["ema20", "ema50"],
            "missing_reason": "技术回踩区暂缺：缺 K 线历史 / EMA，不能生成技术回踩区",
            "confidence": "missing",
        },
        data_status="OK",
        price_position="ABOVE_BUY_ZONE",
        decision="WAIT",
        final_score=78,
        valuation_score=45,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["technical_entry_zone_low"] is None
    assert result["technical_entry_zone_high"] is None
    assert result["entry_display_label"] == "等待回落 $30.00 - $50.00"
    assert "$nan" not in result["entry_display_label"]
    assert "$nan" not in result["entry_display_reason"]
    assert result["technical_entry_missing_fields"] == ["ema20", "ema50"]
    assert result["technical_entry_missing_reason"].startswith("技术回踩区暂缺")


def test_entry_display_preserves_technical_structure_map_when_pullback_missing() -> None:
    result = build_entry_display(
        current_price=138,
        buy_zone={"lower": 130, "upper": 144},
        chase_zone={"lower": 170},
        technical_entry_zone={
            "source": "trend_review",
            "reason": "技术结构：弱趋势修复中；价格或 EMA50 低于 EMA200，不自动生成技术买点",
            "missing_reason": "技术结构：弱趋势修复中；价格或 EMA50 低于 EMA200，不自动生成技术买点",
            "technical_structure_status": "WEAK_TREND_REPAIR",
            "technical_structure_label": "弱趋势修复中",
            "technical_repair_zone_low": 131.4,
            "technical_repair_zone_high": 151.8,
            "near_term_repair_zone_low": 100.19,
            "near_term_repair_zone_high": 108.92,
            "trend_reclaim_zone_low": 120.24,
            "trend_reclaim_zone_high": 132.97,
            "deep_support_zone_low": 82.39,
            "deep_support_zone_high": 86.97,
            "support_watch_zone_low": 129.84,
            "support_watch_zone_high": 132.54,
            "confirmation_price": 142,
            "invalidation_price": 132,
            "technical_structure_reason": "当前不是技术买点，等待重新站回关键均线",
            "next_technical_steps": ["收盘重新站回 EMA20 / EMA50 / EMA200。"],
            "confidence": "review",
        },
        data_status="OK",
        price_position="IN_BUY_ZONE",
        decision="WAIT",
        final_score=78,
        valuation_score=65,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["technical_entry_zone_low"] is None
    assert result["technical_entry_zone_high"] is None
    assert result["technical_structure_status"] == "WEAK_TREND_REPAIR"
    assert result["technical_structure_label"] == "弱趋势修复中"
    assert result["technical_repair_zone_low"] == 131.4
    assert result["technical_repair_zone_high"] == 151.8
    assert result["near_term_repair_zone_low"] == 100.19
    assert result["near_term_repair_zone_high"] == 108.92
    assert result["trend_reclaim_zone_low"] == 120.24
    assert result["trend_reclaim_zone_high"] == 132.97
    assert result["deep_support_zone_low"] == 82.39
    assert result["deep_support_zone_high"] == 86.97
    assert result["confirmation_price"] == 142
    assert result["invalidation_price"] == 132
    assert result["next_technical_steps"] == ["收盘重新站回 EMA20 / EMA50 / EMA200。"]


def test_weak_trend_below_near_valuation_reference_shows_review_not_below_reference() -> None:
    result = build_entry_display(
        current_price=103.08,
        buy_zone={"lower": 105.99, "upper": 126.82},
        chase_zone={"lower": 175.01},
        technical_entry_zone={
            "source": "trend_review",
            "technical_structure_status": "WEAK_TREND_REPAIR",
            "technical_structure_label": "弱趋势修复中",
            "near_term_repair_zone_low": 100.19,
            "near_term_repair_zone_high": 108.92,
            "trend_reclaim_zone_low": 120.24,
            "trend_reclaim_zone_high": 132.97,
            "deep_support_zone_low": 82.39,
            "deep_support_zone_high": 86.97,
            "confirmation_price": 105.28,
            "invalidation_price": 85.44,
            "confidence": "review",
        },
        data_status="OK",
        price_position="BELOW_BUY_ZONE",
        decision="WAIT",
        final_score=80,
        valuation_score=70,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "估值可复核 $105.99 - $126.82"
    assert result["entry_action_hint"] == "技术待确认"
    assert result["entry_context_status"] == "VALUATION_REVIEW_TECHNICAL_UNCONFIRMED"
    assert result["primary_entry_interpretation"] == "估值可复核，技术待确认"
    assert result["zone_semantic_label"] == "估值参考区"
    assert result["valuation_reference_zone_low"] == 105.99
    assert result["valuation_reference_zone_high"] == 126.82


def test_high_quality_value_review_inside_near_term_repair_zone() -> None:
    result = build_entry_display(
        current_price=202.0,
        buy_zone={"lower": 210.0, "upper": 240.0},
        chase_zone={"lower": 310.0},
        technical_entry_zone={
            "source": "trend_review",
            "technical_structure_status": "WEAK_TREND_REPAIR",
            "technical_structure_label": "弱趋势修复中",
            "near_term_repair_zone_low": 192.85,
            "near_term_repair_zone_high": 203.29,
            "trend_reclaim_zone_low": 230.0,
            "trend_reclaim_zone_high": 245.0,
            "confirmation_price": 241.15,
            "invalidation_price": 196.90,
            "confidence": "review",
        },
        data_status="OK",
        price_position="BELOW_BUY_ZONE",
        decision="WAIT",
        final_score=84,
        quality_score=86,
        valuation_score=78,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "价值复核 $210.00 - $240.00"
    assert result["entry_action_hint"] == "结构待确认"
    assert result["entry_context_status"] == "VALUE_REVIEW_NEAR_TERM_REPAIR"
    assert result["primary_entry_interpretation"] == "价值复核，结构待确认"
    assert "近端修复观察区 $192.85 - $203.29" in result["entry_display_reason"]
    assert "禁止追高" not in result["entry_display_label"]
    assert "低于估值参考" not in result["entry_display_label"]


def test_dashboard_value_review_action_does_not_show_chase_block() -> None:
    display = {
        "entry_display_label": "价值复核 $210.00 - $240.00",
        "entry_action_hint": "结构待确认",
        "entry_context_status": "VALUE_REVIEW_NEAR_TERM_REPAIR",
    }
    row = pd.Series(
        {
            **display,
            "finalAction": "需要复核或禁止追高，技术面不转买点",
            "decisionLane": "blocked",
            "dataConfidence": "high",
        }
    )

    assert _dashboard_compact_entry_text(display, row) == ("价值复核", "结构待确认")
    html = _decision_table_cell_html(row, {"key": "actionSummary"}, "ADBE")
    assert "禁止新增" not in html
    assert "禁止追高" not in html
    assert "待复核" in html


def test_entry_display_prioritizes_technical_pullback_even_when_value_zone_is_near() -> None:
    result = build_entry_display(
        current_price=372.10,
        buy_zone={"lower": 264.22, "upper": 304.80},
        chase_zone={"lower": 423.99},
        technical_entry_zone={
            "low": 355.02,
            "high": 377.98,
            "source": "ema_pullback",
            "reason": "强趋势结构下，技术回踩区参考 EMA20 / EMA50 / 近期支撑，并用 ATR 做缓冲",
        },
        data_status="OK",
        price_position="ABOVE_BUY_ZONE",
        decision="WAIT",
        final_score=68,
        valuation_score=35,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "回踩区内 $355.02 - $377.98"
    assert result["entry_action_hint"] == "需复核，不自动买入"
    assert result["technical_position"] == "IN_TECHNICAL_PULLBACK_ZONE"
    assert result["entry_context_status"] == "IN_TECHNICAL_PULLBACK_ZONE"
    assert "深度估值区 $264.22 - $304.80" in result["entry_display_reason"]


def test_chase_zone_still_has_priority_over_technical_pullback_status() -> None:
    result = build_entry_display(
        current_price=113,
        buy_zone={"lower": 30, "upper": 50},
        chase_zone={"lower": 110},
        technical_entry_zone={"low": 108, "high": 116, "source": "ema_pullback"},
        data_status="OK",
        price_position="IN_CHASE_ZONE",
        decision="BLOCK_CHASE",
        final_score=78,
        valuation_score=45,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "追高风险提醒，技术回踩参考 $108.00 - $110.00"
    assert result["entry_action_hint"] == "进入追高风险区，系统不建议新增"
    assert result["technical_position"] == "IN_TECHNICAL_PULLBACK_ZONE"
    assert result["entry_context_status"] == "IN_CHASE_ZONE"
    assert result["technical_chase_overlap"] is True
    assert result["effective_technical_entry_zone_high"] == 110
    assert "超过追高线部分不作为新增参考" in result["entry_display_reason"]


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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert chase["entry_display_label"].startswith("追高风险提醒")
    assert chase["entry_action_hint"] == "进入追高风险区，系统不建议新增"
    assert below["entry_display_label"] == "低于估值参考 $90.00 - $100.00"
    assert "低于估值参考不等于结构破坏" in below["entry_display_reason"]
    assert below["entry_action_hint"] == "待复核，等结构确认"
    assert below["entry_context_status"] == "BELOW_VALUATION_REFERENCE"


def test_below_deep_valuation_without_technical_break_is_not_broken_structure() -> None:
    result = build_entry_display(
        current_price=390,
        buy_zone={"lower": 394.12, "upper": 425.99},
        chase_zone={"lower": 520},
        technical_entry_zone={
            "source": "missing_technical_data",
            "missing_reason": "技术回踩区暂缺：缺 EMA / ATR / swing low",
            "missing_fields": ["ema50", "atr14", "recent_swing_low"],
            "confidence": "missing",
        },
        data_status="OK",
        price_position="BELOW_BUY_ZONE",
        decision="WAIT",
        final_score=84,
        valuation_score=72,
        risk_score=80,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "低于估值参考 $394.12 - $425.99"
    assert result["entry_context_status"] == "BELOW_VALUATION_REFERENCE"
    assert "当前低于估值参考 1%" in result["entry_display_reason"]
    assert "技术回踩区暂缺" in result["entry_display_reason"]
    assert "跌破买区" not in result["entry_display_label"]
    assert "跌破结构" not in result["entry_display_label"]


def test_below_technical_pullback_zone_is_broken_structure() -> None:
    result = build_entry_display(
        current_price=88,
        buy_zone=BUY_ZONE,
        chase_zone=CHASE_ZONE,
        technical_entry_zone={
            "low": 92,
            "high": 105,
            "source": "ema_pullback",
            "reason": "跌破 recent swing low / EMA50 参考区",
        },
        data_status="OK",
        price_position="BELOW_BUY_ZONE",
        decision="WAIT",
        final_score=82,
        valuation_score=60,
        risk_score=70,
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "跌破结构区 $92.00 - $105.00"
    assert result["entry_context_status"] == "BELOW_TECHNICAL_PULLBACK_ZONE"
    assert result["entry_action_hint"] == "跌破结构区，先复核"


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
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
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
        missing_entry_fields=["暂无专属买区模型", "无法生成主击球区"],
        buy_zone_context=LEGACY_DISPLAY_CONTEXT,
    )

    assert result["entry_display_label"] == "暂无参考买区：暂无专属买区模型、无法生成主击球区"
    assert result["missing_entry_fields"] == ["暂无专属买区模型", "无法生成主击球区"]


def test_zone_formatters_are_shared() -> None:
    assert format_buy_zone(BUY_ZONE) == "$90.00 - $100.00"
    assert format_zone_status("IN_BUY_ZONE") == "买区内"
    assert format_zone_status("BELOW_BUY_ZONE") == "低于估值参考，待复核"
    assert format_zone_status("ZONE_MISSING") == "无法判断"


def test_dashboard_watchlist_entry_cell_shows_price_position_without_action_text() -> None:
    row = pd.Series(
        {
            "symbol": "NVDA",
            "price": 110,
            "entryRating": "B - 等回踩",
            "entry_display_label": "等待回落 $90.00 - $100.00",
            "entry_action_hint": "只观察，等待回到主击球区",
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

    assert "<strong>买区上方</strong>" in html
    assert "<em>等待回踩至 $90.00 - $100.00</em>" in html
    assert "等待回落 $90.00 - $100.00" not in html
    assert "追高禁区 &gt;$120.00" not in html
    assert "<em>只观察" not in html


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
    assert "<em>暂无有效区间</em>" in html
    assert "暂无参考买区：缺估值指标" not in html
    assert "暂无专属买区模型" not in html


def test_dashboard_watchlist_entry_cell_simplifies_price_position_statuses() -> None:
    cases = [
        ("IN_BUY_ZONE", "买区内 $90.00 - $100.00", "承接观察区内", "$90.00 - $100.00"),
        ("ABOVE_BUY_ZONE", "等待回落 $90.00 - $100.00", "买区上方", "等待回踩至 $90.00 - $100.00"),
        ("ABOVE_BUY_ZONE", "等待技术回踩 $90.00 - $100.00", "买区上方", "等待回踩至 $90.00 - $100.00"),
        ("ABOVE_BUY_ZONE", "回踩区内 $90.00 - $100.00", "承接观察区内", "$90.00 - $100.00"),
        ("IN_CHASE_ZONE", "禁止追高，参考买区 $90.00 - $100.00", "追高风险区", "$90.00 - $100.00"),
        ("BELOW_BUY_ZONE", "低于估值参考 $90.00 - $100.00", "低于观察区", "$90.00 - $100.00"),
        ("BELOW_BUY_ZONE", "跌破结构区 $90.00 - $100.00", "结构失效风险区", "$90.00 - $100.00"),
        ("ABOVE_BUY_ZONE", "观察区上沿｜当前价不新增", "观察区上沿", "暂无有效区间"),
    ]

    for price_position, display_label, position_label, position_range in cases:
        row = pd.Series(
            {
                "symbol": "TEST",
                "entryRating": "B - 等回踩",
                "entry_display_label": display_label,
                "entry_action_hint": (
                    "进入追高区，禁止新增"
                    if price_position == "IN_CHASE_ZONE"
                    else "需复核，不自动买入"
                    if "回踩区内" in display_label
                    else "只观察，等待技术回踩或基本面复核"
                    if "技术回踩" in display_label
                    else "只观察，等待回到主击球区"
                ),
                "entry_display_reason": "当前位于主击球区",
                "radar_price_position": price_position,
            }
        )

        html = _entry_rating_cell_html(row)

        assert f"<strong>{position_label}</strong>" in html
        assert f"<em>{position_range}</em>" in html
        assert "<em>禁止新增</em>" not in html
        assert "<em>待复核</em>" not in html


def test_dashboard_watchlist_entry_cell_prefers_radar_status_over_legacy_zone() -> None:
    row = pd.Series(
        {
            "symbol": "MSFT",
            "price": 397,
            "entryRating": "B - 等回踩",
            "entry_display_label": "买区内 $394.12 - $425.99",
            "entry_action_hint": "买区内但总分低于 70，需复核",
            "entry_display_reason": "当前位于主击球区",
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

    assert "<strong>承接观察区内</strong>" in html
    assert "$394.12 - $425.99" in html
    assert "$241.88 - $300.37" not in html


def test_dashboard_watchlist_entry_cell_prefers_unified_buy_zone_context_over_stale_entry_display() -> None:
    row = pd.Series(
        {
            "symbol": "NOW",
            "entryRating": "B+ - 击球区附近",
            "entry_display_label": "技术承接数据不足 | 技术承接数据不足",
            "entry_action_hint": "技术承接数据不足",
            "entry_context_status": "DATA_INSUFFICIENT",
            "missing_entry_fields": ["volume_acceptance", "volume_ratio"],
            "buyZoneContext": {
                "current_action": "ALLOW_SMALL_BUY",
                "primary_zone_text": "回踩买区",
                "action_text": "允许小仓观察",
                "zone_selection_reason": "量价承接已恢复。",
                "missing_fields": [],
            },
        }
    )

    display = _dashboard_entry_display(row)
    html = _entry_rating_cell_html(row)

    assert display["entry_context_status"] == "ALLOW_SMALL_BUY"
    assert display["missing_entry_fields"] == []
    assert "<strong>承接观察区内</strong>" in html
    assert "<em>暂无有效区间</em>" in html
    assert "补数据" not in html


def test_dashboard_watchlist_entry_cell_uses_specific_price_position_language() -> None:
    cases = [
        (
            "WAIT_PULLBACK",
            {"current_price": 390, "pullback_zone_low": 377, "pullback_zone_high": 384},
            "买区上方",
            "等待回踩至 $377.00 - $379.10",
        ),
        (
            "WAIT_CONFIRMATION",
            {"current_price": 380, "pullback_zone_low": 377, "pullback_zone_high": 384},
            "承接观察区内",
            "$377.00 - $384.00",
        ),
        (
            "WAIT_CONFIRMATION",
            {"current_price": 405, "pullback_zone_low": 377, "pullback_zone_high": 384, "confirmation_price": 413.71},
            "买区上方",
            "等待回踩至 $377.00 - $384.00",
        ),
        ("ALLOW_SMALL_BUY", {"current_price": 380, "pullback_zone_low": 377, "pullback_zone_high": 384}, "承接观察区内", "$377.00 - $384.00"),
        ("BLOCK_CHASE", {"current_price": 500, "pullback_zone_low": 377, "pullback_zone_high": 384}, "追高风险区", "$377.00 - $384.00"),
        ("DATA_INSUFFICIENT", {"missing_fields": ["daily_ohlcv"]}, "数据不足", "暂无有效区间"),
        ("RISK_REVIEW", {"current_price": 370, "pullback_zone_low": 377, "pullback_zone_high": 384}, "结构失效风险区", "$377.00 - $384.00"),
    ]

    for action, context, label, detail in cases:
        row = pd.Series(
            {
                "symbol": "TEST",
                "price": context.get("current_price", 100),
                "buyZoneContext": {
                    "current_action": action,
                    "primary_zone_text": "回踩买区",
                    "action_text": "等待确认",
                    **context,
                },
            }
        )

        html = _entry_rating_cell_html(row)

        assert f"<strong>{label}</strong>" in html
        assert f"<em>{detail}</em>" in html
        assert "<strong>等待确认</strong>" not in html
        assert "<strong>只观察</strong>" not in html


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
            "entry_action_hint": "只观察，等待回到主击球区",
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
    assert "<strong>数据不足</strong>" in html
    assert "<em>暂无有效区间</em>" in html
    assert "$90.00 - $100.00" not in html
