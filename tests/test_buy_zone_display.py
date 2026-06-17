from __future__ import annotations

from data.buy_zone_display import build_buy_zone_display


def test_now_position_sizing_zero_converges_to_single_main_action() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "ALLOW_SMALL_BUY",
            "primary_zone": "PULLBACK_BUY",
            "primary_zone_text": "回踩买区",
            "current_price": 102.15,
            "pullback_zone_low": 98.0,
            "pullback_zone_high": 105.0,
            "volume_acceptance_score": 54.0,
            "zone_selection_reason": "价格回到技术回踩买区，买区由技术结构和量价承接决定。",
        },
        {
            "current_shares": 100,
            "currentAddLimitPercent": 0,
        },
        mode="test",
    )

    assert display["main_action_text"] == "持有观察 / 当前不建议新增"
    assert display["technical_action_text"] == "技术回踩带内，可观察"
    assert display["account_action_text"] == "已有 100 股，当前新增额度为 0，系统不建议新增"
    assert display["volume_confirmation_text"] == "初步承接，尚未确认"


def test_high_volume_unconfirmed_copy_is_more_cautious() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "REPAIR_WATCH",
            "primary_zone_text": "修复观察区",
            "current_price": 204.02,
            "pullback_zone_low": 192.85,
            "pullback_zone_high": 204.97,
            "volume_acceptance_score": 27.0,
            "volume_ratio": 3.58,
        },
        {
            "volumePriceStatus": "UNCONFIRMED",
            "volumePriceScore": 27.0,
            "current_shares": 10,
            "currentAddLimitPercent": 0,
        },
        mode="test",
    )

    assert display["main_action_text"] == "持有观察 / 当前不建议新增"
    assert display["volume_confirmation_text"] == "放量未确认，等收盘确认 / 事件复核"
    assert display["entry_context_status"] == "WAIT_CONFIRMATION"


def test_low_confirmation_forming_acceptance_does_not_claim_confirmed_support() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "PULLBACK_BUY",
            "primary_zone_text": "技术回踩带",
            "current_price": 98.5,
            "pullback_zone_low": 97.5,
            "pullback_zone_high": 105.0,
            "volume_price_gate": "FORMING_ACCEPTANCE",
            "confirmation_score": 48,
            "volume_ratio": 0.55,
        },
        {"currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["volume_confirmation_text"] == "缩量回踩，但承接未确认"
    assert "承接K线" not in display["next_step_text"]


def test_display_exposes_acceptance_state_and_quality() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "PULLBACK_BUY",
            "primary_zone_text": "技术回踩带",
            "current_price": 104.15,
            "pullback_zone_low": 97.5,
            "pullback_zone_high": 108.0,
            "acceptance_state": "WEAK_ACCEPTANCE",
            "acceptance_state_text": "承接不足",
            "entry_quality": "EDGE_OBSERVE",
            "falling_knife_risk": "LOW",
            "acceptance_reasons": ["缩量回踩，但承接未确认"],
            "missing_confirmation": ["量价确认分低于60"],
            "required_confirmation_price": 105.12,
            "setup_score": 64,
            "technical_structure_score": 68,
            "volume_acceptance_score": 48,
            "risk_reward_score": 72,
        },
        {"current_shares": 160, "currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["acceptance_state"] == "WEAK_ACCEPTANCE"
    assert display["acceptance_state_text"] == "承接不足"
    assert display["entry_quality"] == "EDGE_OBSERVE"
    assert display["entry_quality_text"] == "边缘观察"
    assert display["falling_knife_risk"] == "LOW"
    assert display["acceptance_action_text"] == "承接不足 / 持有观察 / 当前不建议新增"
    assert display["required_confirmation_price"] == 105.12
    assert any("缩量回踩" in reason for reason in display["acceptance_reasons"])
    assert display["setup_score"] == 64
    assert display["technical_structure_score"] == 68
    assert display["volume_acceptance_score"] == 48
    assert display["risk_reward_score"] == 72
    assert display["setup_quality_status"] == "SETUP_WATCH"
    assert display["setup_quality_text"] == "观察级 Setup"
    assert "Setup 综合 64" in display["setup_quality_note"]
    assert "量价未确认 / 承接不足" in display["setup_quality_note"]


def test_display_exposes_momentum_context_note() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "PULLBACK_BUY",
            "primary_zone_text": "技术回踩带",
            "current_price": 104.15,
            "pullback_zone_low": 97.5,
            "pullback_zone_high": 108.0,
            "momentum_context": {
                "rsi14": 74,
                "rsi_state": "OVERHEATED",
                "bb_position": "NEAR_UPPER",
                "bb_width_state": "NORMAL",
                "momentum_bias": "CHASE_RISK",
                "momentum_score_adjustment": -8,
                "momentum_note": "RSI 74，价格贴近布林上轨，追高风险升高。",
                "momentum_reasons": ["RSI 过热且靠近布林上轨，新增语气需降级。"],
            },
            "risk_flags": ["MOMENTUM_OVERHEATED"],
        },
        {},
        mode="test",
    )

    assert display["momentum_context"]["rsi_state"] == "OVERHEATED"
    assert display["momentum_note"] == "RSI 74，价格贴近布林上轨，追高风险升高。"
    assert display["momentum_score_adjustment"] == -8
    assert display["risk_flags"] == ["MOMENTUM_OVERHEATED"]


def test_display_splits_plan_capacity_from_current_price_add_capacity() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "PULLBACK_UPPER_WATCH",
            "primary_zone_text": "买区上沿 / 修复观察区",
            "current_price": 102.37,
            "left_probe_zone_low": 97.5,
            "left_probe_zone_high": 99.32,
            "observe_zone_high": 104.65,
            "pullback_zone_high": 108.0,
            "current_subzone": "ACCEPTANCE_OBSERVATION_ZONE",
            "acceptance_state": "WEAK_ACCEPTANCE",
            "acceptance_state_text": "承接不足",
            "entry_quality": "EDGE_OBSERVE",
            "confirmation_price": 105.12,
            "invalidation_price": 97.5,
            "volume_acceptance_score": 48,
            "risk_reward_score": 82,
        },
        {"current_shares": 200, "max_shares": 500, "currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["plan_limit_shares"] == 500
    assert display["remaining_plan_capacity_shares"] == 300
    assert display["current_price_add_capacity_shares"] == 0
    assert display["next_buy_shares"] == 50
    assert "当前价不新增" in display["trading_headline_text"]
    assert "剩余计划额度 300 股" in display["position_capacity_text"]
    assert "当前价可新增 0 股" in display["position_capacity_text"]
    assert "$99.32 - $101.19" in display["next_buy_action_text"]
    assert display["repair_observation_line_text"] == "修复观察线：站上 $105.12 后重新评估，不直接追买"
    assert "右侧确认线：放量站稳 $108.00 - $110.16 后，最多释放 +50 股右侧额度" == display["right_confirmation_line_text"]
    assert display["risk_reward_decision_text"] == "赔率较好，但承接不足。当前属于高赔率观察，不是立即买入。"


def test_display_reads_plan_limit_from_stock_plan_snapshot() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "current_price": 102.37,
            "left_probe_zone_low": 99.0,
            "left_probe_zone_high": 101.0,
            "volume_acceptance_score": 48,
            "risk_reward_score": 82,
        },
        {"current_shares": 200, "stock_plan": {"max_shares": 500}, "currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["plan_limit_shares"] == 500
    assert display["remaining_plan_capacity_shares"] == 300


def test_pullback_confirmation_with_zero_add_shows_in_zone_not_pause() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "PULLBACK_BUY",
            "primary_zone_text": "回踩买区",
            "current_price": 148.02,
            "pullback_zone_low": 145.74,
            "pullback_zone_high": 153.72,
            "volume_acceptance_score": 48.0,
            "volume_ratio": 0.9,
        },
        {"currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["main_action_text"] == "区内看承接 / 当前不建议新增"
    assert display["entry_display_label"] == "区内看承接"
    assert display["entry_action_hint"] == "当前不建议新增"
    assert display["entry_context_status"] == "WAIT_CONFIRMATION"
    assert display["action_code"] == "WAIT_CONFIRMATION"


def test_upper_pullback_zone_display_does_not_call_it_main_batting_zone() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "WAIT_CONFIRMATION",
            "primary_zone": "PULLBACK_UPPER_WATCH",
            "primary_zone_text": "买区上沿 / 修复观察区",
            "current_price": 272.0,
            "pullback_zone_low": 253.17,
            "pullback_zone_high": 273.56,
            "zone_position": 0.923,
            "confirmation_price": 276.0,
            "breakout_reevaluation_price": 332.46,
            "volume_price_status": "FORMING",
            "volume_ratio": 0.65,
            "daily_return_pct": -0.3,
            "risk_reward": 1.4,
            "risk_reward_text": "RR 1.40：观察",
            "action_new_cash": "未持仓：等待重新评估线和量价确认。",
            "action_existing_position": "已有持仓：持有观察，等待量价确认或更低回踩。",
            "entry_condition_text": "加仓触发：放量站上近端确认线 $276.00 后重新评估。",
            "invalidation_condition_text": "跌破买区下沿 $253.17：暂停新增",
            "confidence_breakdown": {"support_score": 70, "trend_score": 80, "volume_score": 42, "risk_reward_score": 55},
        },
        {"current_shares": 100, "currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["badge_label"] == "买区上沿"
    assert display["main_action_text"] == "持有观察 / 当前不建议新增"
    assert display["technical_action_text"] == "当前价位于买区上沿 / 修复观察区，持有观察，不主动新增。"
    assert "主击球区" not in display["technical_action_text"]
    assert display["volume_confirmation_text"] == "缩量调整，尚不构成承接"
    assert display["risk_reward_text"] == "RR 1.40：观察（仅作参考，量价未确认）"
    assert display["entry_condition_text"].startswith("加仓触发")
    assert display["invalidation_condition_text"].startswith("跌破买区下沿")
    assert display["confidence_breakdown"]["support_score"] == 70


def test_data_insufficient_position_pauses_add_without_legacy_buy_copy() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "DATA_INSUFFICIENT",
            "missing_fields": ["daily_ohlcv", "volume_ratio"],
        },
        {"current_shares": 100},
    )

    assert display["main_action_text"] == "持有观察 / 不建议加仓"
    assert display["account_action_text"] == "已有 100 股，持有观察，不建议加仓"
    assert display["zone_text"] == "暂不生成"
    assert display["badge_label"] == "数据不足"
