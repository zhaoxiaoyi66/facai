from __future__ import annotations

import pandas as pd

from data.buy_zone_display import build_buy_zone_display
from data.buy_zone_engine import (
    ALLOW_SMALL_BUY,
    BLOCK_CHASE,
    DATA_INSUFFICIENT,
    PAUSE_BUY,
    RISK_REVIEW,
    WAIT_CONFIRMATION,
    backtest_buy_zone_snapshots,
    build_buy_zone_context,
    build_buy_zone_snapshot,
    save_buy_zone_snapshot,
)
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
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
        "ma20": 108,
        "ma50": 103,
        "ma200": 92,
        "atr_14": 4.2,
        "recent_swing_high": 119,
        "resistance_zone_high": 119,
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


def _daily_bars(count: int = 220, *, latest_volume: int = 2_400_000) -> list[dict[str, float]]:
    bars = []
    for index in range(count):
        close = 82.0 + index * 0.1
        bars.append(
            {
                "open": close - 0.25,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": latest_volume if index == count - 1 else 1_800_000 + (index % 7) * 25_000,
            }
        )
    return bars


def test_good_company_in_chase_zone_blocks_chase() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=130, final_score=92),
        volume_snapshot=_volume(volume_price_status="ACCEPTANCE_CONFIRMED", volume_price_score=82, volume_ratio=1.4),
    )

    assert context.primary_zone == "CHASE_RISK"
    assert context.current_action == BLOCK_CHASE
    assert context.action_text == "追高风险提醒"
    assert "好公司" not in context.zone_selection_reason


def test_pullback_zone_with_shrink_volume_and_good_risk_reward_allows_small_buy() -> None:
    context = build_buy_zone_context(_base_source(current_price=100.5), volume_snapshot=_volume())

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.current_action == ALLOW_SMALL_BUY
    assert context.setup_score >= 62
    assert context.left_probe_zone_high == 102.1
    assert context.no_position_action_text == "未持仓：小仓观察参考，后续加仓仍需确认。"


def test_pullback_probe_zone_with_weak_volume_waits_confirmation_not_pullback() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=101),
        volume_snapshot=_volume(volume_price_score=48, volume_ratio=0.9),
    )

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.current_price <= context.left_probe_zone_high
    assert context.current_action == WAIT_CONFIRMATION
    assert context.current_action != ALLOW_SMALL_BUY


def test_left_probe_upper_edge_waits_confirmation_even_with_good_scores() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=102.05),
        volume_snapshot=_volume(
            volume_price_status="ACCEPTANCE_CONFIRMED",
            volume_price_score=76,
            volume_ratio=1.1,
            confirmation_score=76,
        ),
    )

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.left_probe_position_label == "UPPER_EDGE"
    assert context.left_side_position_pct is not None
    assert context.current_action == WAIT_CONFIRMATION
    assert "价格在左侧试仓区中上部" in context.execution_gate_reason


def test_left_probe_lower_edge_requires_quality_and_allows_small_buy() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=100.5, technical_resistance_price=122),
        volume_snapshot=_volume(
            volume_price_status="ACCEPTANCE_CONFIRMED",
            volume_price_score=78,
            volume_ratio=1.15,
            confirmation_score=78,
        ),
    )

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.left_probe_position_label == "LOWER_EDGE"
    assert context.volume_price_gate in {"CONFIRMED_ACCEPTANCE", "FORMING_ACCEPTANCE"}
    assert context.target_quality == "TECH_RESISTANCE_HIGH"
    assert context.risk_reward_score >= 65
    assert context.current_action == ALLOW_SMALL_BUY
    assert context.zone_action_quality == "LOW_RISK_OBSERVATION"
    assert context.advisory_level == "INFO"
    assert context.acceptance_state == "CLEAR_ACCEPTANCE"
    assert context.acceptance_state_text == "明显承接"
    assert context.entry_quality == "GOOD_LEFT_SIDE"
    assert context.falling_knife_risk == "LOW"


def test_pullback_upper_half_does_not_allow_small_buy() -> None:
    context = build_buy_zone_context(_base_source(current_price=104), volume_snapshot=_volume())

    assert context.primary_zone == "PULLBACK_WATCH"
    assert context.primary_zone_text == "技术回踩带内，可观察"
    assert context.current_action == WAIT_CONFIRMATION
    assert context.left_probe_zone_high == 102.1
    assert context.observe_zone_high == 104.5


def test_confirmation_line_target_quality_blocks_small_buy() -> None:
    source = _base_source(current_price=100.5)
    for key in ("resistance_zone_high", "recent_swing_high"):
        source.pop(key)
    source.pop("chase_above_price")

    context = build_buy_zone_context(
        source,
        volume_snapshot=_volume(
            volume_price_status="ACCEPTANCE_CONFIRMED",
            volume_price_score=82,
            volume_ratio=1.2,
            confirmation_score=82,
        ),
    )

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.left_probe_position_label == "LOWER_EDGE"
    assert context.target_quality == "CONFIRMATION_LINE"
    assert context.current_action == WAIT_CONFIRMATION
    assert "收益目标质量不足" in context.execution_gate_reason


def test_high_volume_unconfirmed_gate_waits_confirmation() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=100.8, technical_resistance_price=122),
        volume_snapshot=_volume(
            volume_price_status="UNCONFIRMED",
            volume_price_score=32,
            volume_ratio=2.4,
            confirmation_score=70,
        ),
    )

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.volume_price_gate == "HIGH_VOLUME_UNCONFIRMED"
    assert context.acceptance_state == "HIGH_VOLUME_UNCONFIRMED"
    assert context.acceptance_state_text == "放量未确认"
    assert context.entry_quality == "HIGH_RISK"
    assert context.current_action == WAIT_CONFIRMATION
    assert "放量未确认" in context.execution_gate_reason


def test_low_confirmation_score_marks_weak_acceptance() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=100.8, technical_resistance_price=122),
        volume_snapshot=_volume(volume_price_status="FORMING", volume_price_score=54, volume_ratio=0.68, confirmation_score=48),
    )

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.acceptance_state == "WEAK_ACCEPTANCE"
    assert context.acceptance_state_text == "承接不足"
    assert context.entry_quality in {"EDGE_OBSERVE", "WAIT_CONFIRMATION"}
    assert "量价确认分低于60" in context.missing_confirmation
    assert context.required_confirmation_price == 118


def test_price_below_invalidation_marks_structure_broken() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=95.5, technical_resistance_price=122),
        volume_snapshot=_volume(volume_price_status="FAILED", volume_price_score=20, volume_ratio=1.6),
    )

    assert context.primary_zone == "INVALIDATION"
    assert context.acceptance_state == "STRUCTURE_BROKEN"
    assert context.acceptance_state_text == "结构破坏"
    assert context.entry_quality == "INVALID"
    assert context.falling_knife_risk == "HIGH"


def test_fast_selloff_near_invalidation_marks_falling_knife_risk() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=97.2,
            effective_technical_entry_zone_low=96.8,
            effective_technical_entry_zone_high=104.0,
            invalidation_price=96.0,
            daily_return_pct=-4.8,
            technical_resistance_price=122,
        ),
        volume_snapshot=_volume(volume_price_status="FORMING", volume_price_score=42, volume_ratio=1.55, confirmation_score=42),
    )

    assert context.primary_zone == "DEEP_ACCEPTANCE"
    assert context.acceptance_state == "FALLING_KNIFE_RISK"
    assert context.acceptance_state_text == "飞刀风险"
    assert context.entry_quality == "HIGH_RISK"
    assert context.falling_knife_risk == "HIGH"


def test_ibm_upper_pullback_zone_is_repair_watch_not_main_batting_area() -> None:
    context = build_buy_zone_context(
        _base_source(
            ticker="IBM",
            current_price=272.0,
            effective_technical_entry_zone_low=253.17,
            effective_technical_entry_zone_high=273.56,
            support_zone_low=242.0,
            support_zone_high=250.0,
            deep_support_zone_low=210.0,
            deep_support_zone_high=220.0,
            trend_critical_zone_low=242.0,
            trend_critical_zone_high=250.0,
            confirmation_price=332.46,
            fifty_two_week_high=332.46,
            resistance_zone_low=276.0,
            resistance_zone_high=290.0,
            recent_swing_high=286.0,
            invalidation_price=249.0,
            chase_above_price=310.0,
        ),
        volume_snapshot=_volume(volume_price_status="FORMING", volume_price_score=52, volume_ratio=0.65),
    )

    assert context.primary_zone == "PULLBACK_UPPER_WATCH"
    assert context.primary_zone_text == "买区上沿 / 修复观察区"
    assert context.current_action == WAIT_CONFIRMATION
    assert context.zone_position is not None
    assert context.zone_position > 0.75
    assert context.zone_position_text == "买区上沿 / 修复观察区，不主动新增"
    assert context.left_probe_zone_low == 253.17
    assert round(context.left_probe_zone_high or 0, 2) == 260.31
    assert round(context.observe_zone_high or 0, 2) == 268.46
    assert context.confirmation_price == 276.0
    assert context.breakout_reevaluation_price == 332.46
    assert "52周高点 $332.46 仅作为突破重估线" not in context.add_trigger_condition_text
    assert "近端确认线 $276.00" in context.add_trigger_condition_text
    assert "跌破买区下沿 $253.17：暂停新增" in context.pause_new_condition_text
    assert "跌破 $249.00：买区失效" in context.pause_new_condition_text
    assert "$242.00 - $250.00：趋势恶化" in context.pause_new_condition_text
    assert "$210.00 - $220.00：极端风险" in context.pause_new_condition_text


def test_left_probe_zone_is_clipped_by_invalidation_risk() -> None:
    context = build_buy_zone_context(
        _base_source(
            ticker="NOW",
            current_price=105.81,
            effective_technical_entry_zone_low=94.60,
            effective_technical_entry_zone_high=108.09,
            support_zone_low=94.60,
            support_zone_high=99.32,
            confirmation_price=113.0,
            resistance_zone_low=113.0,
            resistance_zone_high=126.0,
            recent_swing_high=126.0,
            invalidation_price=97.50,
            chase_above_price=135.0,
        ),
        volume_snapshot=_volume(volume_price_score=48, volume_ratio=0.55, confirmation_score=48),
    )

    assert context.primary_zone == "PULLBACK_UPPER_WATCH"
    assert context.current_action == WAIT_CONFIRMATION
    assert context.left_probe_zone_low == 97.50
    assert round(context.left_probe_zone_high or 0, 2) == 99.32
    assert context.invalidation_risk_zone_low == 94.60
    assert context.invalidation_risk_zone_high == 97.50
    assert context.left_side_position_pct is None
    assert context.left_probe_position_label == "OUTSIDE"


def test_52_week_high_is_breakout_reevaluation_not_buy_confirmation() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=272.0,
            effective_technical_entry_zone_low=253.17,
            effective_technical_entry_zone_high=273.56,
            support_zone_low=242.0,
            support_zone_high=250.0,
            confirmation_price=332.46,
            fifty_two_week_high=332.46,
            resistance_zone_low=276.0,
            resistance_zone_high=290.0,
            invalidation_price=249.0,
        ),
        volume_snapshot=_volume(volume_price_status="FORMING", volume_price_score=52, volume_ratio=0.8),
    )

    assert context.confirmation_price == 276.0
    assert context.breakout_reevaluation_price == 332.46


def test_support_cluster_and_atr_width_build_primary_zone_without_explicit_buy_zone() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "V2",
            "current_price": 104.0,
            "final_score": 82,
            "recent_swing_low": 100.2,
            "ma50": 100.8,
            "anchored_vwap": 101.0,
            "volume_profile_poc": 100.5,
            "ma20": 106.0,
            "ma200": 91.0,
            "atr_20": 3.0,
            "resistance_zone_low": 108.0,
            "resistance_zone_high": 114.0,
        },
        volume_snapshot=_volume(volume_price_score=58, volume_ratio=0.9),
    )

    assert context.support_clusters
    assert context.selected_support_cluster["candidate_count"] >= 3
    assert context.zone_width == 2.4
    assert context.primary_buy_zone_low is not None
    assert context.primary_buy_zone_high is not None
    assert context.primary_buy_zone_high - context.primary_buy_zone_low <= context.current_price * 0.06
    assert context.support_score > 0


def test_zone_position_above_one_waits_pullback_or_chase_defense() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=117, near_term_repair_zone_low=None, near_term_repair_zone_high=None),
        volume_snapshot=_volume(volume_price_score=58, volume_ratio=0.9),
    )

    assert context.zone_position is not None
    assert context.zone_position > 1.0
    assert context.zone_position_text == "高于买区，等待回踩/防追高"


def test_high_volume_breakdown_below_support_enters_risk_review() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=89, invalidation_price=80),
        volume_snapshot=_volume(volume_price_score=80, volume_ratio=1.4),
    )

    assert context.volume_acceptance_score == 0.0
    assert context.current_action == PAUSE_BUY
    assert context.volume_price_gate == "FAILED_ACCEPTANCE"


def test_buy_zone_snapshot_and_backtest_metrics_are_generated() -> None:
    bars = _daily_bars(130)
    snapshot = build_buy_zone_snapshot(
        "MSFT",
        "2026-06-12",
        _base_source(current_price=101, daily_ohlcv=bars),
        volume_snapshot=_volume(),
    )
    rows = backtest_buy_zone_snapshots(
        "MSFT",
        bars + [
            {"date": "future-1", "open": 105, "high": 107, "low": 100, "close": 106, "volume": 2_500_000}
            for _ in range(65)
        ],
        min_history=80,
    )

    assert snapshot.symbol == "MSFT"
    assert snapshot.zone_low is not None
    assert snapshot.action_new_cash
    assert snapshot.acceptance_state
    assert snapshot.entry_quality
    assert snapshot.current_subzone
    assert snapshot.main_advisory
    assert snapshot.rr_score == snapshot.context["risk_reward_score"]
    assert snapshot.target_quality == snapshot.context["target_quality"]
    assert rows
    assert {
        "return_5d",
        "return_20d",
        "MAE_20",
        "MFE_20",
        "false_buy_rate",
        "acceptance_state",
        "entry_quality",
        "current_subzone",
        "main_advisory",
        "rr_score",
        "target_quality",
    }.issubset(rows[0])


def test_buy_zone_snapshot_save_upserts_symbol_date(tmp_path) -> None:
    path = tmp_path / "snapshots.json"
    first = build_buy_zone_snapshot(
        "MSFT",
        "2026-06-12",
        _base_source(current_price=101),
        volume_snapshot=_volume(),
    )
    second = build_buy_zone_snapshot(
        "MSFT",
        "2026-06-12",
        _base_source(current_price=102),
        volume_snapshot=_volume(),
    )

    save_buy_zone_snapshot(first, path)
    save_buy_zone_snapshot(second, path)

    import json

    records = json.loads(path.read_text(encoding="utf-8"))
    assert len(records) == 1
    assert records[0]["symbol"] == "MSFT"
    assert records[0]["price"] == 102
    assert records[0]["acceptance_state"] == second.acceptance_state
    assert records[0]["main_advisory"] == second.main_advisory


def test_price_at_reevaluation_line_inside_pullback_enters_confirmation_review() -> None:
    context = build_buy_zone_context(
        _base_source(current_price=104.5, confirmation_price=104),
        volume_snapshot=_volume(),
    )

    assert context.primary_zone == "CONFIRMATION_REVIEW"
    assert context.current_action == WAIT_CONFIRMATION


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
    assert context.current_action == PAUSE_BUY
    assert context.existing_position_action_text == "已有持仓：暂停新增，复核失效线和放量破位风险。"
    assert context.no_position_action_text == "未持仓：系统不建议新增，等待买区重新生成。"


def test_low_final_score_does_not_auto_block_good_small_setup() -> None:
    context = build_buy_zone_context(_base_source(current_price=100.5, final_score=65), volume_snapshot=_volume())

    assert context.current_action == ALLOW_SMALL_BUY
    assert context.core_position_allowed is False
    assert "买入时机仍以 setup_score" in context.core_position_reason
    assert "系统不建议作为核心仓" not in context.core_position_reason
    assert "禁止核心仓买入" not in context.core_position_reason
    assert "小仓观察" in context.no_position_action_text


def test_risk_reward_without_target_or_resistance_is_capped() -> None:
    source = _base_source(current_price=101)
    source.pop("chase_above_price")
    source.pop("recent_swing_high")
    source.pop("resistance_zone_high")

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.current_action == WAIT_CONFIRMATION
    assert context.risk_reward_score <= 60
    assert context.target_quality == "CONFIRMATION_LINE"
    assert context.rr_score_capped is True
    assert "收益目标质量不足" in context.execution_gate_reason


def test_resistance_zone_high_is_not_downgraded_to_chase_line() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=101,
            confirmation_price=104,
            chase_above_price=139.2,
            resistance_zone_high=139.2,
            recent_swing_high=139.2,
        ),
        volume_snapshot=_volume(),
    )

    assert context.raw_rr is not None
    assert context.raw_rr >= 2
    assert context.risk_reward_score == 82
    assert context.rr_score_capped is True
    assert context.target_source == "resistance_zone_high"
    assert context.target_quality == "TECH_RESISTANCE_HIGH"
    assert context.rr_cap_reason == "target uses technical resistance; rr capped"


def test_explicit_chase_price_is_chase_line_only_without_better_target() -> None:
    source = _base_source(current_price=101, confirmation_price=96, chase_above_price=139.2)
    for key in ("resistance_zone_high", "recent_swing_high"):
        source.pop(key)

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.raw_rr is not None
    assert context.raw_rr >= 2
    assert context.risk_reward_score == 55
    assert context.rr_score_capped is True
    assert context.target_source == "chase_above_price"
    assert context.target_quality == "CHASE_LINE"
    assert context.rr_cap_reason == "target equals chase line; rr capped"


def test_explicit_target_can_score_high_without_chase_cap() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=101,
            target_price=140,
            chase_above_price=125,
            resistance_zone_high=125,
            recent_swing_high=125,
        ),
        volume_snapshot=_volume(),
    )

    assert context.risk_reward_score == 88
    assert context.rr_score_capped is False
    assert context.target_source == "target_price"
    assert context.target_quality == "EXPLICIT_MANUAL_TARGET"


def test_manual_target_price_is_preferred_over_technical_resistance() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=101,
            manual_target_price=150,
            technical_resistance_price=112,
            recent_swing_high=140,
            resistance_zone_high=130,
        ),
        volume_snapshot=_volume(),
    )

    assert context.upside_target == 150
    assert context.target_source == "manual_target_price"
    assert context.target_quality == "EXPLICIT_MANUAL_TARGET"
    assert context.risk_reward_score == 88


def test_technical_resistance_price_is_preferred_over_swing_high() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=101,
            technical_resistance_price=112,
            technical_resistance_source="MA50",
            recent_swing_high=140,
            resistance_zone_high=None,
        ),
        volume_snapshot=_volume(),
    )

    assert context.upside_target == 112
    assert context.target_source == "technical_resistance_price"
    assert context.target_quality == "TECH_RESISTANCE_HIGH"
    assert context.target_source_detail == "MA50"


def test_technical_entry_model_resistance_levels_choose_nearest_valid_target() -> None:
    source = _base_source(current_price=101)
    for key in ("resistance_zone_high", "recent_swing_high", "recent_breakout_level"):
        source.pop(key, None)
    source["technical_entry_model"] = {
        "resistanceLevels": [
            {"price": 99, "label": "MA20"},
            {"price": 118, "label": "60D high"},
            {"price": 112, "label": "MA50"},
        ]
    }

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.upside_target == 112
    assert context.target_source == "resistanceLevels"
    assert context.target_quality == "TECH_RESISTANCE_HIGH"
    assert context.target_source_detail == "MA50"


def test_recent_breakout_level_is_preferred_over_recent_swing_high() -> None:
    source = _base_source(current_price=101, recent_breakout_level=130, recent_swing_high=140)
    source.pop("resistance_zone_high")

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.upside_target == 130
    assert context.target_source == "recent_breakout_level"
    assert context.target_quality == "SWING_HIGH_60D"
    assert context.risk_reward_score == 75
    assert context.rr_score_capped is True


def test_target_candidates_below_current_price_are_ignored() -> None:
    source = _base_source(
        current_price=101,
        technical_resistance_price=99,
        recent_breakout_level=130,
        recent_swing_high=140,
    )
    source.pop("resistance_zone_high")

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.upside_target == 130
    assert context.target_source == "recent_breakout_level"
    assert context.target_quality == "SWING_HIGH_60D"


def test_fifty_two_week_high_is_lower_quality_than_swing_or_resistance() -> None:
    source = _base_source(current_price=101, fifty_two_week_high=160)
    for key in ("resistance_zone_high", "recent_swing_high", "recent_breakout_level"):
        source.pop(key, None)

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.upside_target == 160
    assert context.target_source == "fifty_two_week_high"
    assert context.target_quality == "FIFTY_TWO_WEEK_HIGH"
    assert context.risk_reward_score == 65
    assert context.rr_score_capped is True


def test_swing_high_target_is_capped_below_explicit_target_quality() -> None:
    source = _base_source(current_price=101, recent_swing_high=119, chase_above_price=125)
    source.pop("resistance_zone_high")

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.raw_rr is not None
    assert context.raw_rr >= 2
    assert context.risk_reward_score == 70
    assert context.rr_score_capped is True
    assert context.target_source == "recent_swing_high"
    assert context.target_quality == "SWING_HIGH_20D"


def test_now_nvda_vst_like_targets_are_not_all_chase_line() -> None:
    samples = [
        ("NOW", 102.15, 139.2),
        ("NVDA", 205.19, 232.28),
        ("VST", 148.02, 167.4),
    ]
    qualities = []
    for ticker, price, resistance in samples:
        context = build_buy_zone_context(
            _base_source(
                ticker=ticker,
                current_price=price,
                resistance_zone_high=resistance,
                recent_swing_high=resistance,
                chase_above_price=resistance,
            ),
            volume_snapshot=_volume(),
        )
        qualities.append(context.target_quality)

    assert "CHASE_LINE" not in qualities
    assert set(qualities) == {"TECH_RESISTANCE_HIGH"}


def test_now_like_pullback_middle_observes_and_account_no_add() -> None:
    context = build_buy_zone_context(
        _base_source(
            ticker="NOW",
            current_price=103.0,
            final_score=68,
            deep_support_zone_low=88.92,
            deep_support_zone_high=93.5,
            effective_technical_entry_zone_low=99.29,
            effective_technical_entry_zone_high=108.33,
            confirmation_price=105.16,
            invalidation_price=91.97,
            chase_above_price=139.2,
            ma20=108.33,
            ma50=99.29,
            ma200=92,
            atr_14=5.2,
            recent_swing_high=139.2,
            resistance_zone_high=139.2,
        ),
        volume_snapshot=_volume(volume_price_score=54, volume_ratio=0.73),
    )
    display = build_buy_zone_display(
        context.to_dict(),
        {"current_shares": 100, "currentAddLimitPercent": 0},
        mode="report",
    )

    assert context.primary_zone == "PULLBACK_WATCH"
    assert context.current_action == WAIT_CONFIRMATION
    assert context.left_probe_zone_high is not None
    assert context.left_probe_zone_high < context.current_price
    assert display["main_action_text"] == "持有观察 / 当前不建议新增"
    assert display["account_action_text"] == "已有 100 股，当前新增额度为 0，系统不建议新增"
    assert "可小仓分批" not in display["main_action_text"]


def test_missing_technical_or_volume_data_is_data_insufficient() -> None:
    context = build_buy_zone_context({"ticker": "CRCL", "current_price": 80, "final_score": 90}, volume_snapshot={})

    assert context.current_action == DATA_INSUFFICIENT
    assert context.primary_zone_text == "技术承接数据不足"
    assert "daily_ohlcv" in context.missing_fields
    assert "volume_acceptance" in context.missing_fields
    assert context.support_zone_low is None
    assert context.pullback_zone_low is None


def test_empty_daily_ohlcv_snapshot_does_not_count_as_history() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "CRCL",
            "daily_ohlcv": {"open": None, "high": None, "low": None, "close": None, "volume": None},
            "final_score": 40,
        },
        volume_snapshot={},
    )

    assert context.current_action == DATA_INSUFFICIENT
    assert "daily_ohlcv" in context.missing_fields
    assert context.technical_data_source == ""
    assert context.setup_score == 0


def test_short_daily_ohlcv_does_not_fake_long_window_indicators() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "SHORT",
            "daily_ohlcv": _daily_bars(count=10),
            "final_score": 85,
        },
        volume_snapshot={},
    )

    assert context.current_action == DATA_INSUFFICIENT
    assert "daily_ohlcv_window" in context.missing_fields
    assert "ma20" in context.missing_fields
    assert "ma50" in context.missing_fields
    assert "ma200" in context.missing_fields
    assert "atr_14" in context.missing_fields
    assert "volume_ratio" in context.missing_fields
    assert context.technical_data_source == "daily_ohlcv_partial"
    assert context.setup_score == 0


def test_daily_ohlcv_volume_fallback_prevents_false_data_insufficient() -> None:
    bars = _daily_bars()
    context = build_buy_zone_context(
        {
            "ticker": "NVDA",
            "daily_ohlcv": bars,
            "current_price": bars[-1]["close"],
            "final_score": 88,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.setup_score > 0
    assert context.latest_volume == bars[-1]["volume"]
    assert context.avg_volume_20d is not None
    assert context.volume_ratio is not None
    assert context.volume_source == "daily_ohlcv"
    assert "volume_acceptance" not in context.missing_fields


def test_daily_ohlcv_derives_uncomputed_ma_atr_rsi_and_zones() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "MSFT",
            "daily_ohlcv": _daily_bars(),
            "final_score": 86,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.technical_structure_score > 0
    assert context.volume_acceptance_score > 0
    assert context.risk_reward_score > 0
    assert context.support_zone_low is not None
    assert context.support_zone_high is not None
    assert context.confirmation_price is not None
    assert context.chase_price is None
    assert context.momentum_context["bb_upper"] is not None
    assert context.momentum_context["bb_lower"] is not None


def test_add_technical_indicators_calculates_bollinger_bands() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=40),
            "close": [100 + index for index in range(40)],
            "high": [101 + index for index in range(40)],
            "low": [99 + index for index in range(40)],
            "volume": [1_000_000 + index * 1_000 for index in range(40)],
        }
    )

    result = add_technical_indicators(history)
    snapshot = latest_technical_snapshot(result)

    for field in ("bb_upper", "bb_middle", "bb_lower", "bb_percent_b", "bb_width"):
        assert field in result
        assert snapshot[field] is not None


def test_momentum_context_flags_overheated_upper_band_chase_risk() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=104.5,
            rsi14=74,
            bb_lower=92,
            bb_middle=99,
            bb_upper=105,
            bb_percent_b=0.96,
            bb_width=13,
        ),
        volume_snapshot=_volume(volume_price_score=58, volume_ratio=0.9),
    )

    assert context.momentum_context["rsi_state"] == "OVERHEATED"
    assert context.momentum_context["bb_position"] == "NEAR_UPPER"
    assert context.momentum_context["momentum_bias"] == "CHASE_RISK"
    assert "MOMENTUM_OVERHEATED" in context.risk_flags
    assert context.current_action != ALLOW_SMALL_BUY


def test_momentum_context_does_not_turn_oversold_lower_band_into_buy_signal() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=101,
            rsi14=26,
            bb_lower=100,
            bb_middle=108,
            bb_upper=116,
            bb_percent_b=0.06,
            bb_width=14,
        ),
        volume_snapshot=_volume(volume_price_status="UNCONFIRMED", volume_price_score=42, volume_ratio=0.6, confirmation_score=42),
    )

    assert context.momentum_context["momentum_bias"] == "OVERSOLD_OBSERVE"
    assert context.momentum_context["momentum_score_adjustment"] > 0
    assert context.current_action != ALLOW_SMALL_BUY
    assert context.acceptance_state != "CLEAR_ACCEPTANCE"


def test_momentum_context_marks_below_lower_band_volume_selloff_as_falling_knife() -> None:
    context = build_buy_zone_context(
        _base_source(
            current_price=99,
            rsi14=34,
            bb_lower=100,
            bb_middle=108,
            bb_upper=116,
            bb_percent_b=-0.06,
            bb_width=14,
            daily_return_pct=-4.2,
            close_position=0.12,
        ),
        volume_snapshot=_volume(volume_price_status="FORMING", volume_price_score=58, volume_ratio=1.55, confirmation_score=58),
    )

    assert context.momentum_context["momentum_bias"] == "FALLING_KNIFE_RISK"
    assert context.acceptance_state == "FALLING_KNIFE_RISK"
    assert context.falling_knife_risk == "HIGH"


def test_daily_ohlcv_dataframe_is_accepted_without_truth_value_error() -> None:
    context = build_buy_zone_context(
        {
            "ticker": "MSFT",
            "daily_ohlcv": pd.DataFrame(_daily_bars()),
            "final_score": 86,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.technical_data_source == "daily_ohlcv"


def test_nvda_like_missing_quote_volume_uses_daily_ohlcv_volume() -> None:
    bars = _daily_bars(latest_volume=105_422_923)
    context = build_buy_zone_context(
        {
            "ticker": "NVDA",
            "current_price": 205.19,
            "final_score": 91,
            "deep_support_zone_low": 196.34,
            "deep_support_zone_high": 200.84,
            "effective_technical_entry_zone_low": 194.34,
            "effective_technical_entry_zone_high": 211.82,
            "confirmation_price": 206.59,
            "invalidation_price": 199.34,
            "ma20": 211.15,
            "ma50": 206.59,
            "ma200": 187.24,
            "atr_14": 8.33,
            "rsi_14": 45.2,
            "recent_swing_high": 232.28,
            "resistance_zone_high": 232.28,
            "daily_ohlcv": bars,
        },
        volume_snapshot={},
    )

    assert context.current_action != DATA_INSUFFICIENT
    assert context.setup_score > 0
    assert context.technical_structure_score > 0
    assert context.volume_acceptance_score > 0
    assert context.risk_reward_score > 0
    assert context.latest_volume == 105_422_923
    assert context.volume_source == "daily_ohlcv"


def test_missing_key_technical_acceptance_fields_do_not_generate_buy_zone() -> None:
    for key, expected_missing in (
        ("ma20", "ma20"),
        ("ma50", "ma50"),
        ("ma200", "ma200"),
        ("atr_14", "atr_14"),
    ):
        source = _base_source()
        source.pop(key)

        context = build_buy_zone_context(source, volume_snapshot=_volume())

        assert context.current_action == DATA_INSUFFICIENT
        assert expected_missing in context.missing_fields
        assert context.support_zone_low is None
        assert context.pullback_zone_low is None


def test_missing_resistance_zone_does_not_generate_buy_zone() -> None:
    source = _base_source()
    source.pop("resistance_zone_high")
    source.pop("recent_swing_high")
    source.pop("confirmation_price")

    context = build_buy_zone_context(source, volume_snapshot=_volume())

    assert context.current_action == DATA_INSUFFICIENT
    assert "resistance_zone" in context.missing_fields
    assert context.support_zone_low is None
    assert context.pullback_zone_low is None


def test_report_page_does_not_expose_buy_zone_raw_enum() -> None:
    context = build_buy_zone_context(_base_source(current_price=100.5), volume_snapshot=_volume()).to_dict()
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

    assert "小仓观察参考" in html
    assert "ALLOW_SMALL_BUY" not in html
    assert "PULLBACK_BUY" not in html


def test_buy_zone_context_visible_text_has_no_mojibake() -> None:
    context = build_buy_zone_context(_base_source(current_price=100.5), volume_snapshot=_volume())
    visible_text = " ".join(
        [
            context.action_text,
            context.primary_zone_text,
            context.existing_position_action_text,
            context.no_position_action_text,
            context.zone_selection_reason,
        ]
    )

    assert "小仓观察参考" in visible_text
    assert "买区由技术结构" in visible_text
    assert not any(token in visible_text for token in ("鍏", "鎶", "涓", "瓒", "绛"))
