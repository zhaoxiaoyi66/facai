from __future__ import annotations

from data.buy_zone_engine import (
    ALLOW_SMALL_BUY,
    BLOCK_CHASE,
    DATA_INSUFFICIENT,
    RISK_REVIEW,
    WAIT_CONFIRMATION,
    build_buy_zone_context,
)
from ui import ai_stock_radar as radar_ui


def _base_source(**overrides):
    data = {
        "ticker": "MSFT",
        "current_price": 104,
        "final_score": 84,
        "risk_score": 72,
        "deep_support_zone_low": 90,
        "deep_support_zone_high": 98,
        "effective_technical_entry_zone_low": 100,
        "effective_technical_entry_zone_high": 106,
        "near_term_repair_zone_low": 107,
        "near_term_repair_zone_high": 116,
        "confirmation_price": 118,
        "invalidation_price": 96,
        "chase_above_price": 125,
    }
    data.update(overrides)
    return data


def _volume(**overrides):
    data = {
        "volume_price_status": "FORMING",
        "volume_price_score": 60,
        "volume_ratio": 0.82,
    }
    data.update(overrides)
    return data


def test_good_company_in_chase_zone_blocks_chase() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=130, final_score=92),
        volume_snapshot=_volume(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=82, volume_ratio=1.4),
    )

    assert context.primary_zone == "CHASE_RISK"
    assert context.current_action == BLOCK_CHASE
    assert context.action_text == "禁止追高"
    assert "好公司" not in context.zone_selection_reason


def test_pullback_zone_with_shrink_volume_and_good_risk_reward_allows_small_buy() -> None:
    context = build_buy_zone_context(_base_source(current_price=104), volume_snapshot=_volume())

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.current_action == ALLOW_SMALL_BUY
    assert context.setup_score >= 62
    assert context.no_position_action_text == "未持仓：允许小仓观察，后续加仓必须等确认。"


def test_repair_watch_with_unconfirmed_volume_waits_confirmation() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=112),
        volume_snapshot=_volume(volume_price_status="UNCONFIRMED", volume_price_score=42, volume_ratio=1.0),
    )

    assert context.primary_zone == "REPAIR_WATCH"
    assert context.current_action == WAIT_CONFIRMATION
    assert context.action_text == "等待确认"


def test_below_invalidation_enters_risk_review_for_existing_and_no_position() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=94),
        volume_snapshot=_volume(volume_price_status="FAILED", volume_price_score=20, volume_ratio=1.8),
    )

    assert context.primary_zone == "INVALIDATION"
    assert context.current_action == RISK_REVIEW
    assert context.existing_position_action_text == "已有持仓：进入风控复核，暂停新增买入。"
    assert context.no_position_action_text == "未持仓：暂停买入，先复核失效风险。"


def test_low_final_score_does_not_auto_block_good_small_setup() -> None:
    context = build_buy_zone_context(_base_source(final_score=65), volume_snapshot=_volume())

    assert context.current_action == ALLOW_SMALL_BUY
    assert context.core_position_allowed is False
    assert "禁止核心仓买入" in context.core_position_reason
    assert "小仓观察" in context.no_position_action_text


def test_missing_technical_or_volume_data_is_data_insufficient() -> None:
    context = build_buy_zone_context({"ticker": "CRCL", "current_price": 80, "final_score": 90}, volume_snapshot={})

    assert context.current_action == DATA_INSUFFICIENT
    assert context.primary_zone_text == "技术承接数据不足"
    assert "volume_acceptance" in context.missing_fields
    assert context.support_zone_low is None
    assert context.pullback_zone_low is None


def test_report_page_does_not_expose_buy_zone_raw_enum() -> None:
    context = build_buy_zone_context(_base_source(), volume_snapshot=_volume()).to_dict()
    conclusion = radar_ui._trade_conclusion(_base_source(), buy_zone_context=context)
    html = radar_ui._research_header_html(
        _base_source(),
        {},
        {},
        {},
        "观察",
        action_result=None,
        conclusion=conclusion,
    )

    assert "允许小仓观察" in html
    assert "ALLOW_SMALL_BUY" not in html
    assert "PULLBACK_BUY" not in html
