from __future__ import annotations

import pandas as pd

from data.pullback_acceptance import (
    ACCEPTANCE_CONFIRMED,
    ACCEPTANCE_FAILED,
    ACCEPTANCE_FORMING,
    ACCEPTANCE_UNCONFIRMED,
    DATA_MISSING,
    PullbackAcceptanceSnapshot,
    evaluate_pullback_acceptance,
    pullback_acceptance_context_lines,
    pullback_acceptance_hint_html,
)


def test_price_in_repair_zone_without_confirmation_is_unconfirmed() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 103,
            "open": 104,
            "low": 101,
            "high": 106,
            "near_term_repair_zone_low": 100,
            "confirmation_price": 110,
            "invalidation_price": 98,
            "volume": 900_000,
            "avg_volume": 1_000_000,
            "relative_strength_vs_QQQ": -0.03,
        }
    )

    assert snapshot.acceptance_status == ACCEPTANCE_UNCONFIRMED
    assert snapshot.support_hold_status == "支撑守住"
    assert snapshot.close_confirmation_status != "收盘确认"
    assert snapshot.zone_source == "radar"
    assert snapshot.support_source == "near_term_repair_zone_low"


def test_support_held_close_confirmed_and_relative_strength_is_confirmed() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 112,
            "open": 106,
            "low": 101,
            "high": 113,
            "near_term_repair_zone_low": 100,
            "confirmation_price": 110,
            "invalidation_price": 98,
            "ema20": 108,
            "volume": 1_300_000,
            "avg_volume": 1_000_000,
            "relative_strength_vs_QQQ": 0.04,
            "vwap": 109,
        }
    )

    assert snapshot.acceptance_status == ACCEPTANCE_CONFIRMED
    assert snapshot.acceptance_score >= 80
    assert snapshot.close_confirmation_status == "收盘确认"
    assert snapshot.relative_strength_confirmation_status == "相对强势"


def test_support_held_but_close_confirmation_missing_is_forming_or_unconfirmed() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 105,
            "open": 102,
            "low": 100,
            "high": 106,
            "near_term_repair_zone_low": 100,
            "confirmation_price": 112,
            "invalidation_price": 98,
            "volume": 850_000,
            "avg_volume": 1_000_000,
            "relative_strength_vs_QQQ": 0.01,
        }
    )

    assert snapshot.acceptance_status in {ACCEPTANCE_FORMING, ACCEPTANCE_UNCONFIRMED}
    assert snapshot.support_hold_status == "支撑守住"
    assert snapshot.close_confirmation_status != "收盘确认"


def test_close_below_invalidation_price_is_failed() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 95,
            "open": 99,
            "low": 94,
            "high": 100,
            "invalidation_price": 98,
            "recent_swing_low": 97,
            "volume": 1_500_000,
            "avg_volume": 1_000_000,
        }
    )

    assert snapshot.acceptance_status == ACCEPTANCE_FAILED
    assert "跌破" in snapshot.support_hold_status
    assert snapshot.zone_source == "radar"
    assert snapshot.support_source == "invalidation_price"
    assert snapshot.invalid_line_source == "invalidation_price"


def test_pullback_acceptance_reports_radar_zone_source_for_upstream_entry_display() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 103,
            "low": 101,
            "high": 106,
            "near_term_repair_zone_low": 100,
            "near_term_repair_zone_high": 110,
            "confirm_line": 108,
            "invalid_line": 96,
        }
    )

    assert snapshot.zone_source == "radar"
    assert snapshot.support_source == "near_term_repair_zone_low"
    assert snapshot.confirm_line_source == "confirm_line"
    assert snapshot.invalid_line_source == "invalid_line"


def test_pullback_acceptance_reports_fallback_source_for_local_support() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 103,
            "low": 101,
            "high": 106,
            "recent_swing_low": 100,
        }
    )

    assert snapshot.zone_source == "fallback"
    assert snapshot.support_source == "recent_swing_low"


def test_high_volume_break_below_swing_low_is_failed() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 96,
            "open": 101,
            "low": 95,
            "high": 102,
            "recent_swing_low": 98,
            "volume_ratio": 1.4,
        }
    )

    assert snapshot.acceptance_status == ACCEPTANCE_FAILED
    assert snapshot.volume_confirmation_status in {"放量破位", "放量但收弱"}


def test_missing_vwap_with_daily_data_is_not_data_missing() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 105,
            "open": 102,
            "low": 100,
            "high": 106,
            "near_term_repair_zone_low": 100,
            "confirmation_price": 110,
        }
    )

    assert snapshot.acceptance_status != DATA_MISSING
    assert "VWAP" in snapshot.vwap_confirmation_status


def test_missing_core_kline_is_data_missing() -> None:
    snapshot = evaluate_pullback_acceptance(technicals={"ticker": "MISS"})

    assert snapshot.acceptance_status == DATA_MISSING
    assert snapshot.acceptance_score == 0


def test_dashboard_drawer_renders_pullback_acceptance_card() -> None:
    from ui import dashboard_drawer

    html = dashboard_drawer._drawer_pullback_acceptance_card_html(
        pd.Series(
            {
                "pullbackAcceptance": {
                    "acceptance_status": "ACCEPTANCE_UNCONFIRMED",
                    "status_label": "承接未确认",
                    "acceptance_score": 52,
                    "support_hold_status": "支撑守住",
                    "close_confirmation_status": "收盘未确认",
                    "volume_confirmation_status": "量能缺失",
                    "relative_strength_confirmation_status": "相对弱势",
                    "vwap_confirmation_status": "VWAP 缺失",
                    "acceptance_reasons": ["支撑暂时守住"],
                    "next_acceptance_steps": ["等待收盘站回确认线"],
                }
            }
        )
    )

    assert "回踩承接确认" in html
    assert "承接未确认" in html
    assert "支撑守住" in html
    assert "只读提示" in html


def test_acceptance_context_warns_when_price_leaves_observation_zone_or_chase_context() -> None:
    snapshot = evaluate_pullback_acceptance(
        technicals={
            "close": 125,
            "open": 124,
            "low": 123,
            "high": 126,
            "near_term_repair_zone_low": 100,
            "near_term_repair_zone_high": 120,
            "confirmation_price": 130,
            "invalidation_price": 98,
        }
    )

    lines = pullback_acceptance_context_lines(
        snapshot,
        {
            "current_price": 125,
            "near_term_repair_zone_high": 120,
            "decision": "BLOCK_CHASE",
        },
    )
    html = pullback_acceptance_hint_html(snapshot, context_lines=lines)

    assert snapshot.acceptance_status in {ACCEPTANCE_FORMING, ACCEPTANCE_UNCONFIRMED}
    assert "价格已脱离回踩观察区" in html
    assert "买区提示仍为追高语境" in html


def test_acceptance_context_marks_breakdown_review_forming_as_not_trend_repair() -> None:
    snapshot = PullbackAcceptanceSnapshot(
        acceptance_status=ACCEPTANCE_FORMING,
        acceptance_score=67,
        support_hold_status="支撑守住",
        close_confirmation_status="收盘改善",
        volume_confirmation_status="量能中性",
        relative_strength_confirmation_status="相对强弱缺失",
        vwap_confirmation_status="缺 VWAP，日线位置替代",
    )

    lines = pullback_acceptance_context_lines(snapshot, {"technical_structure_status": "BREAKDOWN_REVIEW"})

    assert "破位复核结构" in "；".join(lines)


def test_dashboard_drawer_adds_acceptance_context_for_chase_price_above_zone() -> None:
    from ui import dashboard_drawer

    html = dashboard_drawer._drawer_pullback_acceptance_card_html(
        pd.Series(
            {
                "pullbackAcceptance": {
                    "acceptance_status": "ACCEPTANCE_FORMING",
                    "status_label": "承接形成中",
                    "acceptance_score": 72,
                    "support_hold_status": "支撑守住",
                    "close_confirmation_status": "收盘改善",
                    "volume_confirmation_status": "缩量守支撑",
                    "relative_strength_confirmation_status": "相对强弱缺失",
                    "vwap_confirmation_status": "VWAP 缺失",
                },
                "decision": "BLOCK_CHASE",
                "rawTechnicals": {"price": 125},
                "technical_pullback_zone_high": 120,
            }
        )
    )

    assert "价格已脱离回踩观察区" in html
    assert "买区提示仍为追高语境" in html
