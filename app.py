from __future__ import annotations

from html import escape
import json

import streamlit as st
import streamlit.components.v1 as components

from data.fmp_queue import get_fmp_request_queue
from ui import ai_stock_radar, buy_zone, dashboard, manual_review, portfolio, stock_detail, trade_journal, watchlist, weekend_spread
from ui.theme import render_global_styles


PAGE_DASHBOARD = "决策总览"
PAGE_STOCK_DETAIL = "个股研究"
PAGE_BUY_ZONE = "买区计划"
PAGE_PORTFOLIO = "组合持仓"
PAGE_TRADE_JOURNAL = "交易日志"
PAGE_WATCHLIST = "观察池"
PAGE_MANUAL_REVIEW = "数据复核"

PAGE_WEEKEND_SPREAD = "周末价差"

PAGE_QUERY_VALUES = {
    "dashboard": PAGE_DASHBOARD,
    "detail": PAGE_STOCK_DETAIL,
    "buy-zone": PAGE_BUY_ZONE,
    "portfolio": PAGE_PORTFOLIO,
    "trade-journal": PAGE_TRADE_JOURNAL,
    "watchlist": PAGE_WATCHLIST,
    "manual-review": PAGE_MANUAL_REVIEW,
    "weekend-spread": PAGE_WEEKEND_SPREAD,
}
PAGE_TO_QUERY_VALUE = {value: key for key, value in PAGE_QUERY_VALUES.items()}
PAGE_AI_RADAR = "AI Stock Radar"
PAGE_QUERY_VALUES["ai-radar"] = PAGE_AI_RADAR
PAGE_TO_QUERY_VALUE[PAGE_AI_RADAR] = "ai-radar"
LEGACY_PAGE_ALIASES = {
    "总览仪表盘": PAGE_DASHBOARD,
    "决策仪表盘": PAGE_DASHBOARD,
    "单股详情": PAGE_STOCK_DETAIL,
    "买区计算器": PAGE_BUY_ZONE,
    "组合持仓": PAGE_PORTFOLIO,
    "交易日志": PAGE_TRADE_JOURNAL,
    "观察名单": PAGE_WATCHLIST,
    "数据复核中心": PAGE_MANUAL_REVIEW,
}


st.set_page_config(
    page_title="ZHX Research",
    layout="wide",
    initial_sidebar_state="collapsed",
)


PAGES = {
    PAGE_DASHBOARD: dashboard.render,
    PAGE_STOCK_DETAIL: stock_detail.render,
    PAGE_BUY_ZONE: buy_zone.render,
    PAGE_PORTFOLIO: portfolio.render,
    PAGE_TRADE_JOURNAL: trade_journal.render,
    PAGE_WATCHLIST: watchlist.render,
    PAGE_MANUAL_REVIEW: manual_review.render,
    PAGE_AI_RADAR: ai_stock_radar.render,
    PAGE_WEEKEND_SPREAD: weekend_spread.render,
}


def main() -> None:
    render_global_styles()
    st.session_state["market_data_provider"] = "fmp"
    page_name = _resolve_current_page()
    _render_fixed_sidebar(page_name)
    PAGES[page_name]()


def _resolve_current_page() -> str:
    if st.query_params.get("closeDrawer"):
        st.session_state.pop("dashboard_drawer_symbol", None)
        if "drawer" in st.query_params:
            st.query_params.pop("drawer")
        if "closeDrawer" in st.query_params:
            st.query_params.pop("closeDrawer")

    query_page_key = str(st.query_params.get("page", "")).strip()
    query_page = PAGE_QUERY_VALUES.get(query_page_key)
    pending_page = st.session_state.pop("pending_app_page", None)
    pending_page = LEGACY_PAGE_ALIASES.get(pending_page, pending_page)
    session_page = st.session_state.get("app_page")
    session_page = LEGACY_PAGE_ALIASES.get(session_page, session_page)

    if pending_page in PAGES:
        page_name = pending_page
    elif query_page in PAGES:
        page_name = query_page
    elif session_page in PAGES:
        page_name = session_page
    else:
        page_name = PAGE_DASHBOARD

    st.session_state["app_page"] = page_name
    query_value = PAGE_TO_QUERY_VALUE.get(page_name)
    if query_value and st.query_params.get("page") != query_value:
        st.query_params["page"] = query_value
    return page_name


def _render_fixed_sidebar(active_page: str) -> None:
    queue_stats = get_fmp_request_queue().stats()
    nav_items = "\n".join(_nav_link(page, active_page) for page in PAGES)
    sidebar_html = [
        '<aside class="zhx-fixed-sidebar">',
        '<div class="zhx-side-brand">',
        '<div class="zhx-side-title">ZHX Research</div>',
        '<div class="zhx-side-subtitle">Equity Decision Terminal</div>',
        "</div>",
        '<div class="zhx-side-section">DATA</div>',
        '<div class="zhx-side-data-card">',
        "<strong>本地缓存 · FMP Starter</strong>",
        "<span>缓存优先</span>",
        "<span>最近更新：等待更新</span>",
        "</div>",
    ]
    if queue_stats["queued"]:
        sidebar_html.append(
            f'<div class="zhx-side-queue">更新队列：{int(queue_stats["queued"])} 个请求</div>'
        )
    sidebar_html.extend(
        [
            '<div class="zhx-side-section workspace">WORKSPACE</div>',
            '<nav class="zhx-side-nav">',
            nav_items,
            "</nav>",
            '<div class="zhx-side-footer">',
            '<span class="zhx-side-mark">炸虾</span>',
            '<span class="zhx-side-dot">·</span>',
            "<span>ZHX Research</span>",
            "</div>",
            "</aside>",
        ]
    )
    sidebar_markup = "\n".join(sidebar_html)
    components.html(
        f"""
        <script>
        (() => {{
          const doc = window.parent.document;
          let root = doc.getElementById("zhx-fixed-sidebar-root");
          if (!root) {{
            root = doc.createElement("div");
            root.id = "zhx-fixed-sidebar-root";
            doc.body.appendChild(root);
          }}
          root.innerHTML = {json.dumps(sidebar_markup, ensure_ascii=False)};
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _nav_link(page_name: str, active_page: str) -> str:
    query_value = PAGE_TO_QUERY_VALUE.get(page_name, "dashboard")
    label = _nav_label(page_name)
    icon = _nav_icon(page_name)
    active_class = " active" if page_name == active_page else ""
    return (
        f'<a class="zhx-side-nav-item{active_class}" href="?page={escape(query_value)}" target="_self">'
        f'<span class="zhx-side-nav-icon">{escape(icon)}</span>'
        f"<span>{escape(label)}</span>"
        "</a>"
    )


def _nav_label(page_name: str) -> str:
    labels = {
        PAGE_DASHBOARD: "决策总览",
        PAGE_STOCK_DETAIL: "个股研究",
        PAGE_BUY_ZONE: "买区计划",
        PAGE_PORTFOLIO: "组合持仓",
        PAGE_TRADE_JOURNAL: "交易日志",
        PAGE_WATCHLIST: "观察池",
        PAGE_MANUAL_REVIEW: "数据复核",
    }
    return labels.get(page_name, page_name)


def _nav_icon(page_name: str) -> str:
    icons = {
        PAGE_DASHBOARD: "◇",
        PAGE_STOCK_DETAIL: "⌕",
        PAGE_BUY_ZONE: "◎",
        PAGE_PORTFOLIO: "▣",
        PAGE_TRADE_JOURNAL: "▤",
        PAGE_WATCHLIST: "≡",
        PAGE_MANUAL_REVIEW: "◆",
    }
    return icons.get(page_name, "•")


if __name__ == "__main__":
    main()
