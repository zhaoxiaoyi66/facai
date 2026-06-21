from __future__ import annotations

import inspect
from dataclasses import replace

import pandas as pd
import pytest

from ui import dashboard_drawer


def _fake_drawer_deps() -> dashboard_drawer.DashboardDrawerDeps:
    return dashboard_drawer.DashboardDrawerDeps(
        badge_span_html=lambda value, tone, *args: f"<span>{value}</span>" if value else "",
        badge_color_for_cell=lambda *_args: "neutral",
        translated_join=lambda values, limit=4, **_kwargs: "、".join(str(item) for item in (values or [])[:limit]),
        quality_negative_items=lambda _row: [],
        risk_items=lambda _row: [],
        resolution_value_text=lambda item: str(item.get("value") or "待补"),
        clean_resolution_explanation=lambda text: text,
        dedupe_text=lambda items: list(dict.fromkeys(items)),
        metric_resolution_groups=lambda _value: {},
        drawer_actionable_resolution_row=lambda _item: "",
        drawer_calculated_resolution_row=lambda _item: "",
        drawer_low_priority_resolution_row=lambda _item: "",
        detail_groups=(),
    )


def test_drawer_missing_dependencies_error_is_chinese() -> None:
    original = dashboard_drawer._DRAWER_DEPS
    try:
        dashboard_drawer._DRAWER_DEPS = None
        with pytest.raises(RuntimeError) as captured:
            dashboard_drawer._drawer_deps()
        message = str(captured.value)
        assert "个股详情抽屉依赖尚未配置。" in message
        assert "not configured" not in message
    finally:
        dashboard_drawer._DRAWER_DEPS = original


def test_drawer_labels_do_not_show_raw_internal_codes() -> None:
    assert dashboard_drawer._drawer_compact_action_text("NEW_ACTION_CODE") == "待复核"
    assert dashboard_drawer._structure_thesis_label("NEW_THESIS_STATUS") == "主线待维护"
    assert dashboard_drawer._drawer_compact_action_text("人工复核") == "等突破再评估"
    assert dashboard_drawer._short_action_for_sentence("NEW_ACTION_CODE") == "等待回踩"
    assert dashboard_drawer._short_action_for_sentence("人工复核") == "等突破再评估"


def test_drawer_distinguishes_missing_data_from_insufficient_data() -> None:
    assert dashboard_drawer._drawer_technical_structure_label("DATA_MISSING") == "数据缺失"
    assert dashboard_drawer._volume_price_status_label("DATA_MISSING") == "数据缺失"
    assert dashboard_drawer._acceptance_status_label("DATA_MISSING") == "数据缺失"
    assert dashboard_drawer._structure_status_label("DATA_MISSING") == "数据缺失"


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

    assert "数据可信度低，先复核关键数据。" in html
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

    assert "数据可信度低，先复核关键数据。" in html
    assert "持有观察" not in html
    assert "允许买入" not in html


def test_low_data_confidence_quick_decision_keeps_momentum_note() -> None:
    row = pd.Series(
        {
            "symbol": "ADBE",
            "price": "$211.07",
            "buyZoneContext": {
                "current_action": "DATA_INSUFFICIENT",
                "missing_fields": ["daily_ohlcv"],
            },
            "buyZoneDisplay": {
                "action_code": "DATA_INSUFFICIENT",
                "main_action_text": "仅观察 / 当前不建议新增",
                "momentum_note": "RSI 33，布林位置中性，动能未给额外信号。",
            },
        }
    )

    decision = dashboard_drawer.build_drawer_primary_decision(row)
    html = dashboard_drawer._drawer_quick_decision_html(row, decision)

    assert "数据可信度低，先复核关键数据。" in html
    assert "动能辅助" in html
    assert "RSI 33，布林位置中性，动能未给额外信号。" in html


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

    assert "等待回踩" in html
    assert "当前子区" in html
    assert "等待价格回到技术回踩带" in html
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
    assert "等待量价和K线承接" in in_zone_html
    assert "站上 $413.71 后重新评估，不等于直接买入" in breakout_html
    assert "当前子区" in in_zone_html


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
                "primary_zone_text": "追高风险区",
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

    assert "小仓观察" in html
    assert "允许" not in html
    assert "ALLOW_SMALL_BUY" not in html


def test_drawer_auxiliary_fallback_does_not_expose_backend_trade_enums() -> None:
    html = dashboard_drawer._drawer_action_fusion_fallback_html()

    assert "ALLOW_BUY" not in html
    assert "portfolio sync" not in html
    assert "辅助依据" in html


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


def test_drawer_header_localizes_missing_market_cap() -> None:
    html = dashboard_drawer.drawer_html(
        pd.Series(
            {
                "symbol": "NOW",
                "companyName": "ServiceNow",
                "price": 102.37,
                "marketCap": "N/A",
                "buyZoneContext": {"current_action": "WAIT_CONFIRMATION"},
            }
        ),
        _fake_drawer_deps(),
    )

    assert "市值：待补" in html
    assert "市值：N/A" not in html


def test_drawer_helpers_use_chinese_missing_text_instead_of_na() -> None:
    assert dashboard_drawer._drawer_money_text(None) == "暂缺"
    assert dashboard_drawer._drawer_pct_text(None) == "暂缺"
    assert dashboard_drawer._drawer_zone_range_text(None, None) == "暂缺"
    assert dashboard_drawer._drawer_entry_zone_row_has_value(("追高风险区", "暂缺", "", "")) is False


def test_drawer_basis_panels_do_not_expose_na_placeholders() -> None:
    deps = replace(
        _fake_drawer_deps(),
        detail_groups=(("基础指标", (("marketCap", "市值"), ("fcfMargin", "FCF Margin"))),),
    )
    row = pd.Series({"symbol": "NOW", "marketCap": "N/A", "fcfMargin": "N/A"})

    summary_html = dashboard_drawer._drawer_decision_summary_html(row, deps)
    raw_html = dashboard_drawer._drawer_raw_metrics_html(row, deps)

    assert "待补" in raw_html
    assert "N/A" not in summary_html
    assert "N/A" not in raw_html


def test_drawer_shows_acceptance_state_from_canonical_display() -> None:
    row = pd.Series(
        {
            "symbol": "NOW",
            "price": "$104.15",
            "buyZoneContext": {
                "current_action": "WAIT_CONFIRMATION",
                "primary_zone": "PULLBACK_UPPER_WATCH",
                "current_price": 104.15,
                "pullback_zone_low": 97.5,
                "pullback_zone_high": 108.0,
                "acceptance_state": "WEAK_ACCEPTANCE",
                "acceptance_state_text": "承接不足",
                "entry_quality": "EDGE_OBSERVE",
                "required_confirmation_price": 105.12,
                "momentum_context": {
                    "momentum_note": "RSI 74，价格贴近布林上轨，追高风险升高。",
                    "momentum_bias": "CHASE_RISK",
                },
            },
            "current_shares": 160,
            "currentAddLimitPercent": 0,
        }
    )

    decision = dashboard_drawer.build_drawer_primary_decision(row)
    html = dashboard_drawer._drawer_quick_decision_html(row, decision)

    assert decision["acceptance_state_text"] == "承接不足"
    assert decision["momentum_note"] == "RSI 74，价格贴近布林上轨，追高风险升高。"
    assert "当前子区" in html
    assert "当前动作" in html
    assert "主原因" in html
    assert "动能辅助" in html


def test_drawer_shows_star_badge_without_changing_primary_decision() -> None:
    source = inspect.getsource(dashboard_drawer.drawer_html)

    assert "⭐ 星标关注" in source
    assert "isStarred" in source


def test_drawer_actions_include_internal_report_navigation() -> None:
    actions = dashboard_drawer.build_drawer_actions("nvda")
    open_report = actions[0]
    record_signal = actions[1]

    assert open_report["action"] == "open_report"
    assert open_report["label"] == "查看完整研报"
    assert open_report["href"] == "?page=ai-radar&view=report&ticker=NVDA#radar-report"
    assert open_report["target"] == "_self"
    assert open_report["session_updates"]["ai_radar_selected_ticker"] == "NVDA"
    assert open_report["session_updates"]["radar_report_ticker"] == "NVDA"
    assert record_signal["action"] == "record_signal"
    assert record_signal["href"] == "?page=dashboard&recordSignal=NVDA#watchlist-table"


def test_drawer_actions_html_uses_current_app_links_only() -> None:
    html = dashboard_drawer._drawer_actions_html("PLTR")

    assert "标为星标" in html
    assert "toggleStar=PLTR" in html
    assert "查看完整研报" in html
    assert "记录当前信号" in html
    assert "page=ai-radar" in html
    assert "view=report" in html
    assert "ticker=PLTR" in html
    assert "target=\"_self\"" in html
    assert "target=\"_blank\"" not in html
    assert "http://" not in html
    assert "https://" not in html
    assert "dashboard-open-report-action" in html
    assert "__dashboardCloseDrawer" in html


def test_drawer_keeps_full_basis_out_of_main_view() -> None:
    drawer_source = inspect.getsource(dashboard_drawer.drawer_html)
    detail_source = inspect.getsource(dashboard_drawer._drawer_detail_basis_html)

    assert "build_drawer_primary_decision" in drawer_source
    assert "_drawer_quick_decision_html" in drawer_source
    assert "_drawer_detail_basis_html" not in drawer_source
    assert "查看完整依据" not in drawer_source
    assert "_drawer_buy_plan_alert_html" in drawer_source
    assert "历史估值参考，仅供辅助" in detail_source
    assert "该参考不改变买入权限，买区建议以技术承接 buy_zone_context 为准。" in detail_source


def test_drawer_renders_minimal_buy_plan_alert_form() -> None:
    html = dashboard_drawer._drawer_buy_plan_alert_html(
        "ORCL",
        192.64,
        {
            "symbol": "ORCL",
            "planned_buy_price": 185,
            "planned_buy_shares": 50,
            "note": "跌到观察区下沿再买",
            "status": "ACTIVE",
        },
    )

    assert "计划买入提醒" in html
    assert "当前价：<strong>$192.64</strong>" in html
    assert "已设置：跌到 $185.00 提醒买入 50 股" in html
    assert "计划买入价" in html
    assert "计划买入股数" in html
    assert "备注，可选" in html
    assert "保存提醒" in html
    assert "取消提醒" in html
    assert "持仓角色" not in html
    assert "买入类型" not in html
    assert "目标仓位" not in html
    assert "确认价" not in html
    assert "失效价" not in html


def test_drawer_buy_plan_alert_html_shows_triggered_state() -> None:
    html = dashboard_drawer._drawer_buy_plan_alert_html(
        "ORCL",
        184.9,
        {
            "symbol": "ORCL",
            "planned_buy_price": 185,
            "planned_buy_shares": 50,
            "status": "TRIGGERED",
        },
    )

    assert "已到计划价" in html
    assert "ORCL 已到达计划买入价：当前 $184.90，计划 $185.00，提醒买入 50 股。" in html
