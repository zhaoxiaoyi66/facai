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
