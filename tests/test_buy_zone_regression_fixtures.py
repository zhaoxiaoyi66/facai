from __future__ import annotations

from data.buy_zone_display import build_buy_zone_display
from data.buy_zone_engine import build_buy_zone_context


def _source(**overrides):
    data = {
        "ticker": "FIXTURE",
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


def test_now_regression_pullback_watch_hold_no_add() -> None:
    context = build_buy_zone_context(
        _source(
            ticker="NOW",
            current_price=102.15,
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
    display = build_buy_zone_display(context.to_dict(), {"current_shares": 100, "currentAddLimitPercent": 0})

    assert context.primary_zone == "PULLBACK_WATCH"
    assert context.current_action == "WAIT_CONFIRMATION"
    assert display["main_action_text"] == "持有观察 / 当前不新增"
    assert display["account_action_text"] == "已有 100 股，当前新增额度为 0"


def test_vst_regression_pullback_buy_waits_for_confirmation() -> None:
    context = build_buy_zone_context(
        _source(
            ticker="VST",
            current_price=148.02,
            effective_technical_entry_zone_low=145.74,
            effective_technical_entry_zone_high=153.72,
            confirmation_price=162.5,
            invalidation_price=141.5,
            chase_above_price=167.4,
            ma20=153.72,
            ma50=145.74,
            ma200=128,
            atr_14=6.4,
            recent_swing_high=167.4,
            resistance_zone_high=167.4,
        ),
        volume_snapshot=_volume(volume_price_score=48, volume_ratio=0.9),
    )
    display = build_buy_zone_display(context.to_dict(), {"currentAddLimitPercent": 0})

    assert context.primary_zone == "PULLBACK_BUY"
    assert context.current_action == "WAIT_CONFIRMATION"
    assert display["main_action_text"] == "区内看承接 / 当前不新增"
    assert display["entry_display_label"] == "区内看承接"


def test_adbe_regression_high_volume_unconfirmed_event_review_copy() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "REPAIR_WATCH",
            "current_price": 204.02,
            "pullback_zone_low": 192.85,
            "pullback_zone_high": 204.97,
            "volume_acceptance_score": 27.0,
            "volume_ratio": 3.58,
        },
        {"volumePriceStatus": "UNCONFIRMED", "volumePriceScore": 27.0, "currentAddLimitPercent": 0},
    )

    assert display["main_action_text"] == "暂停买入 / 当前不新增"
    assert display["volume_confirmation_text"] == "放量未确认，等收盘确认 / 事件复核"


def test_chase_regression_blocks_chase() -> None:
    context = build_buy_zone_context(
        _source(current_price=130),
        volume_snapshot=_volume(volume_price_score=70),
    )
    display = build_buy_zone_display(context.to_dict(), {})

    assert context.primary_zone == "CHASE_RISK"
    assert context.current_action == "BLOCK_CHASE"
    assert display["main_action_text"] == "禁止追高"


def test_invalidation_regression_pauses_buy_or_add() -> None:
    context = build_buy_zone_context(
        _source(current_price=94),
        volume_snapshot=_volume(volume_price_score=70),
    )
    display = build_buy_zone_display(context.to_dict(), {})

    assert context.primary_zone == "INVALIDATION"
    assert context.current_action == "RISK_REVIEW"
    assert display["main_action_text"] == "风控复核 / 暂停买入"


def test_rr_target_quality_regression_samples() -> None:
    technical = build_buy_zone_context(
        _source(technical_resistance_price=125, recent_swing_high=140, resistance_zone_high=None),
        volume_snapshot=_volume(),
    )
    chase = _source(confirmation_price=96, chase_above_price=139.2)
    chase.pop("resistance_zone_high")
    chase.pop("recent_swing_high")
    chase_context = build_buy_zone_context(chase, volume_snapshot=_volume())
    manual = build_buy_zone_context(
        _source(manual_target_price=150, technical_resistance_price=112),
        volume_snapshot=_volume(),
    )

    assert technical.target_quality == "TECH_RESISTANCE_HIGH"
    assert technical.risk_reward_score == 82
    assert chase_context.target_quality == "CHASE_LINE"
    assert chase_context.risk_reward_score == 55
    assert manual.target_quality == "EXPLICIT_MANUAL_TARGET"
    assert manual.risk_reward_score == 88
