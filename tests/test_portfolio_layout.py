from pathlib import Path


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


def test_portfolio_mainline_is_compact_and_button_is_not_isolated() -> None:
    source = Path("ui/portfolio_mainline.py").read_text(encoding="utf-8")

    assert "当前主线" in source
    assert "编辑主线" in source
    assert "portfolio-mainline-card compact" in source
    assert "st.columns([1, 0.14])" not in source
    assert "st.columns([8, 1])" in source
    assert "min-height: 86px" not in source
