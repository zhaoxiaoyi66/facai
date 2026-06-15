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

    assert display["main_action_text"] == "持有观察 / 当前不新增"
    assert display["technical_action_text"] == "技术回踩带内，可观察"
    assert display["account_action_text"] == "已有 100 股，当前新增额度为 0"
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

    assert display["main_action_text"] == "持有观察 / 当前不新增"
    assert display["volume_confirmation_text"] == "放量未确认，等收盘确认 / 事件复核"
    assert display["entry_context_status"] == "WAIT_CONFIRMATION"


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

    assert display["main_action_text"] == "区内看承接 / 当前不新增"
    assert display["entry_display_label"] == "区内看承接"
    assert display["entry_action_hint"] == "当前不新增"
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
        },
        {"current_shares": 100, "currentAddLimitPercent": 0},
        mode="test",
    )

    assert display["badge_label"] == "买区上沿"
    assert display["main_action_text"] == "持有观察 / 当前不新增"
    assert display["technical_action_text"] == "当前价位于买区上沿 / 修复观察区，持有观察，不主动新增。"
    assert "主击球区" not in display["technical_action_text"]
    assert display["volume_confirmation_text"] == "缩量调整，尚不构成承接"


def test_data_insufficient_position_pauses_add_without_legacy_buy_copy() -> None:
    display = build_buy_zone_display(
        {
            "current_action": "DATA_INSUFFICIENT",
            "missing_fields": ["daily_ohlcv", "volume_ratio"],
        },
        {"current_shares": 100},
    )

    assert display["main_action_text"] == "持有观察 / 暂停加仓"
    assert display["account_action_text"] == "已有 100 股，持有观察，暂停加仓"
    assert display["zone_text"] == "暂不生成"
    assert display["badge_label"] == "数据不足"
