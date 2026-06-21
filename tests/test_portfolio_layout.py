from pathlib import Path
import inspect

import ui.portfolio as portfolio_ui


def test_portfolio_dashboard_prioritizes_positions_table() -> None:
    source = Path("ui/portfolio.py").read_text(encoding="utf-8")
    render_body = source[source.index("def render()") : source.index("def _consume_portfolio_edit_query")]

    assert 'render_page_header("组合持仓", "查看真实持仓、仓位偏离、组合风险和下一步动作。")' in source
    assert "组合提醒" in source
    assert "持仓清单" in source
    assert "_render_portfolio_alerts" in render_body
    assert render_body.index("_render_overview_strip") < render_body.index("_render_portfolio_alerts")
    assert render_body.index("_render_portfolio_alerts") < render_body.index("_render_positions_table")
    assert render_body.index("_render_positions_table") < render_body.index("_render_role_structure_card")
    assert render_body.index("_render_role_structure_card") < render_body.index("_risk_radar_expander_label")
    assert render_body.index("_risk_radar_expander_label") < render_body.index("_reconciliation_expander_label")
    assert 'with st.expander(_risk_radar_expander_label(view["actionGroups"]), expanded=False)' in source
    assert "with st.expander(_reconciliation_expander_label(reconciliation_rows), expanded=False)" in source


def test_portfolio_dashboard_uses_responsive_grids() -> None:
    source = Path("ui/portfolio.py").read_text(encoding="utf-8")
    final_styles = source[
        source.index("def _render_final_portfolio_styles") : source.index("def _input_value")
    ]
    compact_overview = final_styles[
        final_styles.index(".portfolio-overview.compact") : final_styles.index(
            ".portfolio-stat.compact"
        )
    ]

    assert "repeat(auto-fit, minmax(180px, 1fr))" in compact_overview
    assert "repeat(auto-fit, minmax(140px, 1fr))" in final_styles
    assert "max-width: 1280px" in final_styles
    assert ".portfolio-table th:nth-child(6)" in source
    assert ".portfolio-table th:nth-child(7)" in source
    assert "repeat(6, minmax(0, 1fr))" not in compact_overview
    assert "grid-template-columns: repeat(6, minmax(0, 1fr));" not in source


def test_portfolio_overview_subtitles_are_chinese() -> None:
    source = Path("ui/portfolio.py").read_text(encoding="utf-8")
    overview_source = source[
        source.index("def _render_overview_strip") : source.index("def _cash_source_text")
    ]

    assert "持仓市值" in overview_source
    assert "持仓成本" in overview_source
    assert "手动基准" in overview_source
    assert "启用持仓" in overview_source
    for old_label in ("market value", "cost basis", "manual total", '"active"'):
        assert old_label not in overview_source


def test_portfolio_mainline_is_compact_and_button_is_not_isolated() -> None:
    source = Path("ui/portfolio_mainline.py").read_text(encoding="utf-8")

    assert "当前主线" in source
    assert "编辑主线" in source
    assert "portfolio-mainline-card compact" in source
    assert "st.columns([1, 0.14])" not in source
    assert "st.columns([8, 1])" in source
    assert "min-height: 86px" not in source


def test_portfolio_labels_do_not_show_raw_internal_codes() -> None:
    labels = [
        portfolio_ui._cn_label("NEW_INTERNAL_FIELD"),
        portfolio_ui._snapshot_action_text("NEW_ACTION"),
        portfolio_ui._trade_action_text("NEW_TRADE_ACTION"),
        portfolio_ui._system_action_text({"systemAction": "NEW_SYSTEM_ACTION"}),
    ]

    assert labels == ["未归类", "未标记", "—", "未生成"]
    for label in labels:
        assert "NEW_" not in label


def test_portfolio_reason_labels_do_not_show_raw_internal_codes() -> None:
    assert portfolio_ui._buy_plan_alert_status_text({"status": "NEW_ALERT_STATUS", "triggerPrice": 185}) == "未知｜$185.00"
    assert portfolio_ui._reconciliation_reason_text({"reasons": ["NEW_RECON_REASON"]}) == "其他原因"
    assert portfolio_ui._portfolio_buy_plan_reasons({"plan_match_status": "NEW_PLAN_STATUS"}) == ["其他原因"]
    assert portfolio_ui._portfolio_starter_reasons({"starter_match_status": "NEW_STARTER_STATUS"}) == []


def test_portfolio_drawer_discipline_labels_do_not_show_mojibake() -> None:
    items = portfolio_ui._trading_discipline_items({"symbol": "NVDA", "positionTier": "core"})
    text = " ".join(f"{label} {value}" for label, value in items)

    assert "持仓分层" in text
    assert "纪律提醒" in text
    assert "鑲" not in text
    assert "绾" not in text
    assert "\ue044" not in text


def test_portfolio_unknown_lane_label_does_not_show_raw_key() -> None:
    assert portfolio_ui._lane_label("NEW_LANE") == "未归类"


def test_portfolio_news_check_uses_specific_price_reaction_fallback() -> None:
    function_source = inspect.getsource(portfolio_ui._render_portfolio_news_check)

    assert "价格反应数据不足" in function_source
    assert 'news_price_match_label") or "数据不足"' not in function_source
