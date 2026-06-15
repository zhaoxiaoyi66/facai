from __future__ import annotations

import inspect

import pandas as pd

from ui import dashboard_drawer


def test_quick_decision_blocks_legacy_add_when_buy_zone_context_is_data_insufficient() -> None:
    row = pd.Series(
        {
            "symbol": "NOW",
            "price": "$102.15",
            "finalAction": "可加仓",
            "action": "可加仓",
            "entry_display_label": "价值复核 $100.00 - $110.00",
            "entry_action_hint": "可加仓",
            "current_shares": 100,
            "buyZoneContext": {
                "current_action": "DATA_INSUFFICIENT",
                "primary_zone_text": "技术承接数据不足",
                "missing_fields": ["daily_ohlcv", "volume_ratio", "ma20", "atr_14", "support_zone_low"],
            },
        }
    )

    html = dashboard_drawer._drawer_quick_decision_html(row)

    assert "不建议加仓" in html
    assert "持有观察，不建议加仓" in html
    assert "主击球区" in html
    assert "暂不生成" in html
    assert "结论冲突提示：技术承接数据不足，旧估值参考只作风险提示，不改变主结论。" in html
    assert "历史K线" in html
    assert "成交量/量比" in html
    assert "均线" in html
    assert "ATR" in html
    assert "支撑压力" in html
    assert "可加仓" not in html
    assert "DATA_INSUFFICIENT" not in html
    assert "daily_ohlcv" not in html


def test_quick_decision_uses_no_position_pause_copy_for_data_insufficient() -> None:
    row = pd.Series(
        {
            "symbol": "CRCL",
            "price": "$50.00",
            "action": "允许买入",
            "buyZoneContext": {
                "current_action": "DATA_INSUFFICIENT",
                "missing_fields": ["daily_ohlcv"],
            },
        }
    )

    html = dashboard_drawer._drawer_quick_decision_html(row)

    assert "数据不足" in html
    assert "补齐技术承接数据" in html
    assert "持有观察" not in html
    assert "允许买入" not in html


def test_quick_decision_shows_batting_zone_when_context_is_complete() -> None:
    row = pd.Series(
        {
            "symbol": "MSFT",
            "price": "$390.74",
            "buyZoneContext": {
                "current_action": "WAIT_PULLBACK",
                "pullback_zone_low": 377.5,
                "pullback_zone_high": 384.7,
                "confirmation_price": 413.71,
                "invalidation_price": 382.31,
                "zone_selection_reason": "当前高于理想击球区，等待回踩。",
                "volume_price_status": "FORMING",
                "setup_score": 68.8,
            },
        }
    )

    html = dashboard_drawer._drawer_quick_decision_html(row)

    assert "等回击球区" in html
    assert "$377.50 - $384.70" in html
    assert "等待价格回到主击球区" in html
    assert "暂不生成" not in html
    assert "数据不足" not in html


def test_quick_decision_uses_specific_wait_confirmation_copy() -> None:
    in_zone = pd.Series(
        {
            "symbol": "MSFT",
            "price": "$380.00",
            "buyZoneContext": {
                "current_action": "WAIT_CONFIRMATION",
                "current_price": 380,
                "pullback_zone_low": 377.5,
                "pullback_zone_high": 384.7,
            },
        }
    )
    breakout = pd.Series(
        {
            "symbol": "MSFT",
            "price": "$405.00",
            "buyZoneContext": {
                "current_action": "WAIT_CONFIRMATION",
                "current_price": 405,
                "pullback_zone_low": 377.5,
                "pullback_zone_high": 384.7,
                "confirmation_price": 413.71,
            },
        }
    )

    in_zone_html = dashboard_drawer._drawer_quick_decision_html(in_zone)
    breakout_html = dashboard_drawer._drawer_quick_decision_html(breakout)

    assert "区内看承接" in in_zone_html
    assert "等量价" in in_zone_html
    assert "等突破再评估" in breakout_html
    assert "不追" in breakout_html
    assert "等待确认" not in in_zone_html
    assert "等待确认" not in breakout_html


def test_build_drawer_primary_decision_ignores_action_fusion_nested_context_for_main_conclusion() -> None:
    row = pd.Series(
        {
            "symbol": "NOW",
            "price": "$102.15",
            "actionFusion": {
                "buyZoneContext": {
                    "current_action": "ALLOW_SMALL_BUY",
                    "primary_zone_text": "回踩买区",
                    "action_text": "小仓观察参考",
                },
                "action_code": "ALLOW_SMALL_BUY",
                "action_cn": "可买",
            },
        }
    )

    decision = dashboard_drawer.build_drawer_primary_decision(row)
    html = dashboard_drawer._drawer_quick_decision_html(row, decision)

    assert decision["action_text"] == "数据不足 / 等待补齐"
    assert decision["zone_text"] == "暂不生成"
    assert "小仓观察参考" not in html
    assert "ALLOW_SMALL_BUY" not in html


def test_quick_decision_blocks_chase_even_when_legacy_says_buy() -> None:
    row = pd.Series(
        {
            "symbol": "MRVL",
            "price": "$118.00",
            "entry_display_label": "买区内 $95.00 - $105.00",
            "entry_action_hint": "允许买入",
            "action": "可买",
            "buyZoneContext": {
                "current_action": "BLOCK_CHASE",
                "current_price": 118,
                "pullback_zone_low": 95,
                "pullback_zone_high": 105,
                "primary_zone_text": "追高禁区",
                "zone_selection_reason": "价格已脱离主击球区。",
            },
        }
    )

    decision = dashboard_drawer.build_drawer_primary_decision(row)
    html = dashboard_drawer._drawer_quick_decision_html(row, decision)

    assert decision["action_text"] == "追高风险提醒"
    assert "追高风险提醒" in html
    assert "允许买入" not in html
    assert "可买" not in html
    assert "BLOCK_CHASE" not in html


def test_quick_decision_allows_small_buy_copy_without_raw_enum() -> None:
    row = pd.Series(
        {
            "symbol": "MSFT",
            "price": "$380.00",
            "buyZoneContext": {
                "current_action": "ALLOW_SMALL_BUY",
                "current_price": 380,
                "pullback_zone_low": 377.5,
                "pullback_zone_high": 384.7,
                "zone_selection_reason": "价格位于主击球区且量价承接改善。",
            },
        }
    )

    html = dashboard_drawer._drawer_quick_decision_html(row)

    assert "小仓观察参考" in html
    assert "ALLOW_SMALL_BUY" not in html


def test_drawer_prefers_row_buy_zone_display_for_position_sizing_copy() -> None:
    row = pd.Series(
        {
            "symbol": "NOW",
            "buyZoneContext": {"current_action": "ALLOW_SMALL_BUY"},
            "buy_zone_display": {
                "action_code": "ALLOW_SMALL_BUY",
                "main_action_text": "持有观察 / 当前不建议新增",
                "technical_action_text": "技术回踩带内，可观察",
                "account_action_text": "已有 100 股，当前新增额度为 0，系统不建议新增",
                "next_step_text": "等待新增额度恢复或技术确认进一步增强",
                "zone_text": "$99.29 - $108.33",
                "badge_hint": "当前不建议新增",
            },
        }
    )

    decision = dashboard_drawer.build_drawer_primary_decision(row)

    assert decision["action_text"] == "持有观察 / 当前不建议新增"
    assert decision["main_reason"] == "技术回踩带内，可观察"
    assert decision["position_action"] == "已有 100 股，当前新增额度为 0，系统不建议新增"


def test_drawer_moves_legacy_reference_under_collapsed_full_basis() -> None:
    drawer_source = inspect.getsource(dashboard_drawer.drawer_html)
    detail_source = inspect.getsource(dashboard_drawer._drawer_detail_basis_html)

    assert "build_drawer_primary_decision" in drawer_source
    assert "_drawer_quick_decision_html" in drawer_source
    assert "<summary>查看完整依据</summary>" in drawer_source
    assert "旧估值参考，仅供辅助" in detail_source
    assert "该参考不改变买入权限，主击球区以技术承接 buy_zone_context 为准。" in detail_source
