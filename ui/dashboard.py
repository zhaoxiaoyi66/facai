from __future__ import annotations

from datetime import datetime
from html import escape
import json
import pandas as pd
import streamlit as st

from data.decision_log import save_decision_snapshot_from_bundle
from data.dashboard_row_builder import (
    build_dashboard_row as _build_dashboard_row,
    derive_dashboard_final_decision as _derive_dashboard_final_decision,
    error_dashboard_row as _error_dashboard_row,
)
from data.dashboard_lanes import (
    DASHBOARD_BLOCKED_ACTIONS,
    actionable_rows as _actionable_rows,
    blocked_or_risky_rows as _blocked_or_risky_rows,
    lane_filter_rows as _lane_filter_rows,
    near_buy_zone_rows as _near_buy_zone_rows,
    row_current_add_text as _row_current_add_text,
    row_decision_lane as _row_decision_lane,
    row_final_action as _row_final_action,
    row_is_actionable as _row_is_actionable,
    row_value as _row_value,
    summary_lane_groups as _summary_lane_groups,
    today_priority_rows as _today_priority_rows,
    wait_or_confirm_rows as _wait_or_confirm_rows,
)
from data.dashboard_freshness import (
    build_dashboard_data_freshness,
)
from data.dashboard_risk_model import (
    build_dashboard_data_health_view,
    build_dashboard_data_health_view_from_summary,
    build_dashboard_risk_radar,
    dashboard_symbols as _dashboard_symbols,
    row_current_add_limit_value as _row_current_add_limit_value,
)
from data.data_health import build_data_health_summary
from data.market_context import build_market_context, build_market_history
from data.market_data_refresh import refresh_symbol_market_data
from data.macro_regime import (
    DOLLAR_INDEX,
    DOLLAR_PROXY,
    FEAR_GREED,
    HYG_CREDIT_PROXY,
    HY_OAS,
    MARKET_BREADTH,
    MARKET_TREND,
    SENTIMENT_PROXY,
    TEN_YEAR_YIELD,
    VIX,
    YIELD_CURVE_10Y2Y,
    load_macro_regime,
    macro_regime_detail_html,
    macro_regime_sentiment_status_text,
)
from data.portfolio_view_model import build_portfolio_view_model
from data.price_alerts import triggered_price_alerts
from data.providers import get_market_data_provider
from data.fundamentals import FundamentalCache
from data.portfolio import PortfolioPositionStore
from data.portfolio_structure_health import (
    build_portfolio_structure_check,
)
from data.refresh_policy import RefreshMode, refresh_symbols_by_mode
from data.trading_discipline_stats import build_trading_discipline_summary
from formatting import format_currency, format_multiple, format_percent
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from scoring.total_score import calculate_total_score
from settings import load_watchlist
from ui.dashboard_drawer import (
    DRAWER_FOCUS_SESSION_KEY,
    DRAWER_SYMBOL_SESSION_KEY,
    DashboardDrawerDeps,
    configure_dashboard_drawer,
    drawer_html as _drawer_html,
    drawer_open_menu_html,
    drawer_open_menu_html as _drawer_open_menu_html,
    _drawer_resolution_html,
    drawer_review_action_bar_html as _drawer_review_action_bar_html,
    drawer_review_summary_html as _drawer_review_summary_html,
    queue_stock_detail_drawer as _queue_stock_detail_drawer,
    render_client_stock_detail_drawers,
    render_client_stock_detail_drawers as _render_client_stock_detail_drawers,
    render_stock_detail_drawer as _render_stock_detail_drawer,
)
from ui.dashboard_lanes_ui import (
    _dashboard_priority_item_html as _dashboard_priority_item_html_base,
    _dashboard_priority_label,
    _dashboard_priority_strip_html as _dashboard_priority_strip_html_base,
    _lane_full_reason as _lane_full_reason_base,
    _lane_item_html as _lane_item_html_base,
    _lane_more_html as _lane_more_html_base,
    _lane_reason as _lane_reason_base,
    _lane_short_reason,
    _lane_stack_html as _lane_stack_html_base,
    _summary_panel_head_html,
)
from ui.dashboard_tables import (
    _badge_cell_html,
    _badge_html,
    _badge_span_html,
    _buy_point_label_tone,
    _compact_data_status_label,
    _compact_watchlist_badge_text,
    _data_status_dot_html,
    _data_status_tone,
    _decision_table_cell_html,
    _decision_table_row_html,
    _display_table_text,
    _entry_label_from_grade,
    _entry_rating_cell_html,
    _entry_rating_chip_text,
    _entry_rating_display_parts,
    _entry_rating_text_label,
    _header_cell_html,
    _looks_like_rating_token,
    _short_badge_text,
)
from ui.metric_labels import action_label, confidence_label, metric_label, model_type_label, resolution_status_label


DASHBOARD_COLUMNS = [
    {"key": "symbol", "label": "代码", "align": "left"},
    {"key": "price", "label": "现价", "align": "right"},
    {"key": "marketCap", "label": "市值", "align": "right"},
    {"key": "qualityRating", "label": "质量", "kind": "badge"},
    {"key": "entryRating", "label": "Radar 买区", "kind": "badge"},
    {"key": "riskRating", "label": "风险", "kind": "badge"},
    {"key": "valuationStatus", "label": "估值状态"},
    {"key": "action", "label": "操作建议"},
    {"key": "maxSuggestedPosition", "label": "当前新增"},
    {"key": "dataStatus", "label": "数据状态"},
    {"key": "actions", "label": "操作"},
]

WATCHLIST_COLUMNS = [
    {"key": "symbol", "label": "代码", "align": "left"},
    {"key": "priceMarket", "label": "价格 / 市值"},
    {"key": "qualityRating", "label": "质量", "kind": "badge"},
    {"key": "entryRating", "label": "Radar 买区", "kind": "badge"},
    {"key": "riskRating", "label": "风险", "kind": "badge"},
    {"key": "actionSummary", "label": "动作"},
    {"key": "dataStatus", "label": "数据"},
    {"key": "actions", "label": "操作", "align": "center"},
]

DETAIL_GROUPS = [
    (
        "Valuation",
        [
            ("trailingPe", "TTM市盈率"),
            ("forwardPe", "预期市盈率"),
            ("priceToSales", "市销率"),
            ("enterpriseToRevenue", "EV/销售额"),
            ("priceToFcf", "P/FCF"),
            ("freeCashFlowYield", "FCF收益率"),
        ],
    ),
    (
        "Quality",
        [
            ("revenueGrowth", "收入增速"),
            ("operatingMargin", "经营利润率"),
            ("returnOnInvestedCapital", "ROIC"),
            ("fcfMargin", "FCF margin"),
        ],
    ),
    (
        "Balance Sheet",
        [
            ("netDebtToEbitda", "鍑€鍊哄姟/EBITDA"),
            ("currentRatio", "流动比率"),
        ],
    ),
    (
        "Technical Setup",
        [
            ("rsi14", "RSI14"),
            ("ema20", "EMA20"),
            ("ema50", "EMA50"),
            ("ema200", "EMA200"),
            ("priceVsEma20", "距EMA20"),
            ("priceVsEma50", "距EMA50"),
            ("dailyReturn", "今日涨跌"),
            ("gain20d", "20日涨幅"),
            ("gain60d", "60日涨幅"),
            ("fiftyTwoWeekHigh", "52周高点"),
            ("fiftyTwoWeekLow", "52周低点"),
        ],
    ),
]

DECISION_COLUMN_WIDTHS = [0.72, 0.82, 0.86, 0.92, 0.92, 0.78, 1.05, 1.35, 0.86, 0.90, 0.58]

BADGE_STYLES = {
    "green": ("var(--dash-success-bg)", "var(--dash-success)", "var(--dash-success-border)"),
    "blue": ("var(--dash-info-bg)", "var(--dash-info)", "var(--dash-info-border)"),
    "yellow": ("var(--dash-warning-bg)", "var(--dash-warning)", "var(--dash-warning-border)"),
    "orange": ("var(--dash-orange-bg)", "var(--dash-orange)", "var(--dash-orange-border)"),
    "red": ("var(--dash-danger-bg)", "var(--dash-danger)", "var(--dash-danger-border)"),
    "deepred": ("var(--dash-danger-bg)", "var(--dash-danger-strong)", "var(--dash-danger-border)"),
    "gray": ("var(--dash-neutral-bg)", "var(--dash-neutral)", "var(--dash-neutral-border)"),
}

DASHBOARD_SCORE_SCHEMA_VERSION = 5
LANE_FILTER_SESSION_KEY = "dashboard_active_lane_filter"
RISK_RADAR_FILTER_SESSION_KEY = "dashboard_active_risk_filter"
LANE_FILTER_LABELS = {
    "actionable": "可行动",
    "nearBuyZone": "接近买区",
    "waitOrReview": "待确认",
    "noChaseHighRisk": "风险隔离",
}
RISK_RADAR_FILTER_LABELS = {
    "overweight": "超仓位",
    "noChase": "禁止追高",
    "review": "需复核",
    "lowConfidence": "低置信",
    "noAdd": "不可新增",
    "concentration": "行业集中",
}
TECHNICAL_ERROR_HINTS = (
    "fmp",
    "curl",
    "traceback",
    "timed out",
    "timeout",
    "handshake",
    "network",
    "connection",
    "httperror",
    "ssl",
)


def render() -> None:
    tickers = load_watchlist()
    dashboard_cache_key = (tuple(tickers), DASHBOARD_SCORE_SCHEMA_VERSION)
    _render_dashboard_styles()
    if "dashboard_density" not in st.session_state:
        st.session_state["dashboard_density"] = "紧凑"
    force_refresh = bool(st.session_state.pop("dashboard_force_fmp_refresh", False))
    force_refresh_symbol = st.session_state.pop("dashboard_force_fmp_refresh_symbol", None)
    _render_dashboard_header(tickers)
    refresh_symbols = set(tickers) if force_refresh else {force_refresh_symbol} if force_refresh_symbol else set()
    if force_refresh:
        _render_terminal_notice("全量更新", "正在逐只绕过缓存拉取，建议只在大面积数据异常时使用。", "orange")
    elif force_refresh_symbol:
        _render_terminal_notice("单只更新", f"正在更新 {force_refresh_symbol}，其他股票继续使用本地缓存。", "blue")

    if force_refresh:
        table = _load_dashboard_with_progress(tuple(tickers), refresh_symbols)
        _store_session_dashboard_table(dashboard_cache_key, table)
    elif force_refresh_symbol:
        table = _refresh_single_dashboard_row(tuple(tickers), str(force_refresh_symbol), dashboard_cache_key)
    else:
        loading_slot = st.empty()
        table = _session_dashboard_table(dashboard_cache_key)
        if table is None:
            loading_slot.markdown(
                _loading_shell_html("读取本地缓存", "正在准备决策表、评分和技术指标。"),
                unsafe_allow_html=True,
            )
            table = _load_dashboard(tuple(tickers), DASHBOARD_SCORE_SCHEMA_VERSION)
            _store_session_dashboard_table(dashboard_cache_key, table)
        loading_slot.empty()

    if table.empty:
        st.warning("还没有加载到仪表盘数据。请检查观察名单或数据连接。")
        return
    st.session_state["dashboard_last_table_loaded_at"] = datetime.now().isoformat()
    _handle_risk_radar_filter_query()
    _handle_lane_filter_query()
    _handle_record_signal_query(table)
    _render_record_signal_notice()

    data_health_context = _build_data_health_context(table)
    portfolio_view = build_portfolio_view_model()
    risk_items = build_dashboard_risk_radar(table, portfolio_view)
    macro_regime = load_macro_regime()
    portfolio_structure_check = build_portfolio_structure_check(portfolio_view, macro_regime=macro_regime)
    _render_dashboard_status_bar(
        table,
        data_health_context,
        risk_items,
        macro_regime,
        portfolio_structure_check=portfolio_structure_check,
        tickers=tickers,
    )
    _render_price_alert_strip(tickers)
    _render_decision_table(table)
    _render_dashboard_system_status(data_health_context, risk_items, table, macro_regime)
    _render_client_stock_detail_drawers(table)

    st.caption("缺失财务数据显示为 N/A；评分不会用模型补造财务数字。")


def _render_dashboard_header(tickers: list[str]) -> None:
    now_text = datetime.now().strftime("%H:%M")
    left, right = st.columns([1.15, 2.35], vertical_alignment="bottom")
    with left:
        st.markdown(
            """
            <div class="terminal-title-group">
                <div class="terminal-kicker">ZHX Research</div>
                <div class="terminal-title">决策仪表盘</div>
                <div class="terminal-subtitle">先看能不能买，再展开看细节</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with right:
        st.markdown(
            f"""
            <div class="terminal-meta">
                <span>{len(tickers)}只观察</span>
                <span>最后更新 {escape(now_text)}</span>
                <span>本地缓存 · FMP Starter</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    command_cols = st.columns([0.86, 0.86, 0.68, 3.1], vertical_alignment="center")
    with command_cols[0]:
        if st.button("更新价格", width="stretch", help="只更新 quote：当前价、涨跌幅、成交量、市值；基本面沿用缓存。", key="dashboard_refresh_price_only"):
            _refresh_dashboard_cache_for_mode(tickers, RefreshMode.PRICE_ONLY)
            _clear_dashboard_table_cache()
            st.rerun()
    with command_cols[1]:
        if st.button("更新技术", width="stretch", help="只刷新日线、EMA、ATR、技术回踩区；不刷新基本面。", key="dashboard_refresh_daily_technical"):
            _refresh_dashboard_cache_for_mode(tickers, RefreshMode.DAILY_TECHNICAL)
            _clear_dashboard_table_cache()
            st.rerun()
    with command_cols[2]:
        with st.popover("更多 ▾", use_container_width=True):
            st.markdown("**视图设置**")
            st.selectbox(
                "密度",
                ["紧凑", "舒适"],
                format_func=lambda value: f"密度：{value}",
                label_visibility="collapsed",
                key="dashboard_density",
            )
            st.divider()
            st.markdown("**数据操作**")
            st.caption("低频或高成本操作。批量类任务会消耗 API 次数。")
            if st.button("刷新大盘环境", width="stretch", key="dashboard_refresh_macro_regime_cache", help="只更新 VIX、信用利差、利率、曲线、观察池强弱等宏观缓存，不刷新个股。"):
                _refresh_dashboard_cache_for_mode(tickers, RefreshMode.MACRO_ONLY)
                st.rerun()
            if st.button("财报后刷新基本面", width="stretch", key="dashboard_refresh_fundamentals_if_event", help="只刷新有财报/披露事件的股票；其他股票跳过。"):
                _refresh_dashboard_cache_for_mode(tickers, RefreshMode.FUNDAMENTALS_IF_EVENT)
                _clear_dashboard_table_cache()
                st.rerun()
            if st.button("强制全量刷新", width="stretch", key="dashboard_force_full_refresh", help="逐只刷新 quote、日线和基本面。只在财报后或数据大面积异常时使用。"):
                _refresh_dashboard_cache_for_mode(tickers, RefreshMode.FULL_REFRESH)
                _clear_dashboard_table_cache()
                st.rerun()
            st.button("运行缺失数据补全", width="stretch", key="dashboard_run_missing_fill", disabled=True, help="批量补全入口待接入；当前可在单股详情页运行。")
            if st.button("查看刷新日志", width="stretch", key="dashboard_open_refresh_log"):
                st.session_state["dashboard_show_refresh_log_panel"] = not st.session_state.get("dashboard_show_refresh_log_panel", False)
            if st.button("重置本地缓存", width="stretch", key="dashboard_clear_streamlit_cache", help="仅清空页面级缓存，不删除本地数据库记录。"):
                _clear_dashboard_table_cache()
                st.rerun()
            if st.session_state.get("dashboard_show_refresh_log_panel"):
                stats = st.session_state.get("dashboard_last_table_loaded_at")
                st.caption(f"页面缓存最近更新：{stats or '暂无记录'}")
    st.markdown('<div class="terminal-divider"></div>', unsafe_allow_html=True)


def _render_terminal_notice(title: str, detail: str, tone: str) -> None:
    st.markdown(
        f"""
        <div class="terminal-notice tone-{escape(tone)}">
            <div class="terminal-notice-dot"></div>
            <div>
                <strong>{escape(title)}</strong>
                <span>{escape(detail)}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _loading_shell_html(title: str, detail: str) -> str:
    skeleton_rows = "".join("<div></div>" for _ in range(7))
    return (
        '<div class="terminal-loading-shell">'
        '<div class="terminal-loading-head">'
        '<span class="terminal-loading-pulse"></span>'
        '<div>'
        f'<strong>{escape(title)}</strong>'
        f'<p>{escape(detail)}</p>'
        '</div>'
        '</div>'
        f'<div class="terminal-skeleton-grid">{skeleton_rows}</div>'
        '</div>'
    )


def _refresh_progress_html(title: str, detail: str, current: int, total: int, active_symbol: str) -> str:
    pct = 0 if total <= 0 else min(100, max(0, int(current / total * 100)))
    return (
        '<div class="terminal-refresh-card">'
        '<div class="terminal-refresh-top">'
        '<div>'
        f'<div class="terminal-refresh-kicker">{escape(title)}</div>'
        f'<div class="terminal-refresh-title">{escape(active_symbol)}</div>'
        f'<div class="terminal-refresh-detail">{escape(detail)}</div>'
        '</div>'
        f'<div class="terminal-refresh-count">{current}/{total}</div>'
        '</div>'
        '<div class="terminal-refresh-track">'
        f'<div class="terminal-refresh-bar" style="width:{pct}%"></div>'
        '</div>'
        '</div>'
    )


def _refresh_done_html(total: int) -> str:
    return (
        '<div class="terminal-refresh-card done">'
        '<div class="terminal-refresh-top">'
        '<div>'
        '<div class="terminal-refresh-kicker">更新完成</div>'
        f'<div class="terminal-refresh-title">已处理 {total} 只标的</div>'
        '<div class="terminal-refresh-detail">评分和缓存状态已同步。</div>'
        '</div>'
        '<div class="terminal-refresh-count">100%</div>'
        '</div>'
        '<div class="terminal-refresh-track"><div class="terminal-refresh-bar" style="width:100%"></div></div>'
        '</div>'
    )


def _session_dashboard_table(cache_key: tuple[tuple[str, ...], int]) -> pd.DataFrame | None:
    if st.session_state.get("dashboard_table_cache_key") != cache_key:
        return None
    table = st.session_state.get("dashboard_table_cache")
    if isinstance(table, pd.DataFrame):
        return table.copy()
    return None


def _store_session_dashboard_table(cache_key: tuple[tuple[str, ...], int], table: pd.DataFrame) -> None:
    st.session_state["dashboard_table_cache_key"] = cache_key
    st.session_state["dashboard_table_cache"] = table.copy()


def _clear_dashboard_table_cache() -> None:
    _load_dashboard.clear()
    st.session_state.pop("dashboard_table_cache", None)
    st.session_state.pop("dashboard_table_cache_key", None)


def _refresh_macro_cache_for_dashboard() -> None:
    _refresh_dashboard_cache_for_mode([], RefreshMode.MACRO_ONLY)


def _refresh_dashboard_cache_for_mode(tickers: list[str], mode: RefreshMode) -> None:
    symbols = [] if mode == RefreshMode.MACRO_ONLY else _dashboard_refresh_symbols(tickers)
    try:
        result = refresh_symbols_by_mode(symbols, mode)
        if mode == RefreshMode.MACRO_ONLY:
            st.session_state["dashboard_macro_last_refresh_result"] = result.get("macro_result") or {}
    except Exception as exc:
        result = {
            "mode": mode.value,
            "status": "failed",
            "refreshed_count": 0,
            "skipped_count": 0,
            "failed_count": len(symbols),
            "duration_seconds": 0,
            "ticker_results": [{"ticker": symbol, "status": "failed", "message": str(exc), "duration_seconds": 0} for symbol in symbols],
        }
        if mode == RefreshMode.MACRO_ONLY:
            st.session_state["dashboard_macro_last_refresh_result"] = {"status": "failed", "error": str(exc), "indicators": {}}
    st.session_state["dashboard_refresh_mode_last_result"] = result


def _dashboard_refresh_symbols(tickers: list[str]) -> list[str]:
    symbols = [str(ticker or "").strip().upper() for ticker in tickers if str(ticker or "").strip()]
    try:
        positions = PortfolioPositionStore().list_active_positions()
    except Exception:
        positions = []
    for position in positions:
        symbol = str(position.get("symbol") or "").strip().upper()
        if symbol:
            symbols.append(symbol)
    seen: set[str] = set()
    unique: list[str] = []
    for symbol in symbols:
        if symbol and symbol not in seen:
            seen.add(symbol)
            unique.append(symbol)
    return unique


def _refresh_single_dashboard_row(tickers: tuple[str, ...], symbol: str, cache_key: tuple[tuple[str, ...], int]) -> pd.DataFrame:
    symbol = symbol.upper()
    table = _session_dashboard_table(cache_key)
    if table is None or table.empty:
        table = _load_dashboard(tickers, DASHBOARD_SCORE_SCHEMA_VERSION)
    progress_slot = st.empty()
    progress_slot.markdown(
        _refresh_progress_html(
            title="单只更新",
            detail=f"只刷新 {symbol}，其他股票沿用当前表格缓存。",
            current=1,
            total=1,
            active_symbol=symbol,
        ),
        unsafe_allow_html=True,
    )
    provider = get_market_data_provider(full_fundamentals=True)
    refreshed_row = _load_dashboard_row(provider, symbol, force_refresh=True)
    table = _replace_dashboard_row(table, refreshed_row)
    _store_session_dashboard_table(cache_key, table)
    st.session_state["dashboard_last_table_loaded_at"] = datetime.now().isoformat()
    progress_slot.markdown(_refresh_done_html(1), unsafe_allow_html=True)
    return table


def _replace_dashboard_row(table: pd.DataFrame, row: dict) -> pd.DataFrame:
    updated = table.copy()
    symbol = str(row.get("symbol") or "").upper()
    if "symbol" not in updated or not symbol:
        return pd.DataFrame([row])
    mask = updated["symbol"].astype(str).str.upper() == symbol
    if mask.any():
        for key, value in row.items():
            if key not in updated.columns:
                updated[key] = pd.Series([None] * len(updated), index=updated.index, dtype=object)
            # Some dashboard fields are list/dict payloads for reasons, missing metrics,
            # and metric resolutions. Assigning them through loc can make pandas try to
            # expand the list as an ndarray. Write per cell so the object stays intact.
            for index in updated.index[mask]:
                updated.at[index, key] = value
        return updated
    return pd.concat([updated, pd.DataFrame([row])], ignore_index=True)


@st.cache_data(ttl=60 * 60, show_spinner=False)
def _load_dashboard(tickers: tuple[str, ...], score_schema_version: int) -> pd.DataFrame:
    _ = score_schema_version
    return _build_dashboard_table(tickers, refresh_symbols=set())


def _load_dashboard_with_progress(tickers: tuple[str, ...], refresh_symbols: set[str]) -> pd.DataFrame:
    normalized_refresh_symbols = {symbol.upper() for symbol in refresh_symbols if symbol}
    progress_slot = st.empty()
    rows = []
    summary_provider = get_market_data_provider(full_fundamentals=False)
    full_provider = get_market_data_provider(full_fundamentals=True)
    total = len(tickers)
    for index, ticker in enumerate(tickers, start=1):
        refresh_this_ticker = ticker.upper() in normalized_refresh_symbols
        verb = "正在向 FMP 更新" if refresh_this_ticker else "读取本地缓存"
        progress_slot.markdown(
            _refresh_progress_html(
                title="更新数据源" if refresh_this_ticker else "刷新评分缓存",
                detail=f"{verb} · {ticker}",
                current=index,
                total=total,
                active_symbol=ticker,
            ),
            unsafe_allow_html=True,
        )
        provider = full_provider if refresh_this_ticker else summary_provider
        rows.append(_load_dashboard_row(provider, ticker, refresh_this_ticker))
    progress_slot.markdown(_refresh_done_html(total), unsafe_allow_html=True)
    table = pd.DataFrame(rows)
    st.session_state["dashboard_last_table_loaded_at"] = datetime.now().isoformat()
    return table


def _build_dashboard_table(tickers: tuple[str, ...], refresh_symbols: set[str]) -> pd.DataFrame:
    if not refresh_symbols:
        return _build_cached_dashboard_table(tickers)

    provider = get_market_data_provider(full_fundamentals=False)
    rows = []
    normalized_refresh_symbols = {symbol.upper() for symbol in refresh_symbols if symbol}
    for ticker in tickers:
        rows.append(_load_dashboard_row(provider, ticker, ticker.upper() in normalized_refresh_symbols))
    return pd.DataFrame(rows)


def _build_cached_dashboard_table(tickers: tuple[str, ...]) -> pd.DataFrame:
    fundamental_cache = FundamentalCache()
    return pd.DataFrame(
        [_load_cached_dashboard_row(fundamental_cache, ticker) for ticker in tickers]
    )


def _load_cached_dashboard_row(fundamental_cache: FundamentalCache, ticker: str) -> dict:
    try:
        snapshot = fundamental_cache.get_snapshot(ticker, max_age_hours=24 * 3650)
        history = build_market_history(ticker)
        if snapshot is None and (history is None or history.empty):
            return _error_dashboard_row(ticker, RuntimeError("本地缓存暂无数据；点击“更新价格”获取 quote，或在更多里强制全量刷新。"))

        snapshot = dict(snapshot or {"ticker": ticker, "symbol": ticker})
        snapshot.setdefault("ticker", ticker)
        snapshot.setdefault("symbol", ticker)
        snapshot["cache_note"] = "首页默认只读本地缓存；点击“更新价格”只刷新 quote，基本面沿用缓存。"
        if history is None or history.empty:
            history = _empty_price_history()

        technicals = latest_technical_snapshot(add_technical_indicators(history))
        _apply_market_price_to_snapshot(ticker, snapshot, technicals)
        score = calculate_total_score(snapshot, technicals)
        data_quality = {"pct": score.data_quality_pct, "missing": score.missing_data}
        return _build_dashboard_row(ticker, snapshot, technicals, score, data_quality)
    except Exception as exc:
        return _error_dashboard_row(ticker, exc)


def _empty_price_history() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _load_dashboard_row(provider, ticker: str, force_refresh: bool) -> dict:
    try:
        snapshot = provider.get_quote(ticker, force_refresh=force_refresh)
        if force_refresh:
            provider.get_price_history(ticker, force_refresh=True)
        history = add_technical_indicators(build_market_history(ticker))
        technicals = latest_technical_snapshot(history)
        _apply_market_price_to_snapshot(ticker, snapshot, technicals)
        score = calculate_total_score(snapshot, technicals)
        data_quality = {"pct": score.data_quality_pct, "missing": score.missing_data}
        return _build_dashboard_row(ticker, snapshot, technicals, score, data_quality)
    except Exception as exc:
        return _error_dashboard_row(ticker, exc)


def _apply_market_price_to_snapshot(ticker: str, snapshot: dict, technicals: dict) -> None:
    market = build_market_context(ticker)
    market_price = _first_number(market.get("currentPrice"), technicals.get("price"), snapshot.get("current_price"))
    if market_price is None:
        return
    snapshot["current_price"] = market_price
    snapshot["price"] = market_price
    technicals["price"] = market_price


def _first_number(*values: object) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _render_market_strip(table: pd.DataFrame) -> None:
    rows = [row for _, row in table.iterrows()]
    actionable = sum(1 for row in rows if _row_is_actionable(row))
    wait_count = sum(
        1
        for row in rows
        if not _row_is_actionable(row)
        and _row_decision_lane(row) == "wait"
        and _row_final_action(row) not in DASHBOARD_BLOCKED_ACTIONS
    )
    risk_count = sum(
        1
        for row in rows
        if _row_decision_lane(row) in {"blocked", "review"}
        or _row_final_action(row) in DASHBOARD_BLOCKED_ACTIONS
        or row.get("highRiskFlagCount", 0) > 0
    )
    avg_drawdown = _average_percent_column(table, "drawdownFromHigh")

    metrics = [
        ("观察数", f"{len(table)}", "当前股票池"),
        ("可行动", f"{actionable}", "可分批执行"),
        ("待确认/观察", f"{wait_count}", "等回踩或复核"),
        ("风险/问题", f"{risk_count}", f"平均回撤 {avg_drawdown}"),
    ]
    cards = "".join(_market_stat_html(label, value, detail) for label, value, detail in metrics)
    st.markdown(f'<section class="market-ribbon">{cards}</section>', unsafe_allow_html=True)


def _render_dashboard_status_bar(
    table: pd.DataFrame,
    data_health_context: dict[str, object],
    risk_items: list[dict[str, object]],
    macro_regime,
    *,
    portfolio_structure_check=None,
    tickers: list[str] | None = None,
) -> None:
    freshness = build_dashboard_data_freshness(
        _dashboard_refresh_symbols(tickers or []),
        macro_regime=macro_regime,
    )
    macro_pills_html = "".join(
        _dashboard_macro_pill_html(label, value, tone)
        for label, value, tone in _dashboard_command_status_items(table, macro_regime, freshness, portfolio_structure_check)
    )
    summary_html = "".join(
        _dashboard_command_summary_item_html(label, value, tone)
        for label, value, tone in _dashboard_command_summary_items(macro_regime, freshness)
    )
    detail_html = _dashboard_command_detail_html(
        macro_regime,
        freshness,
        last_refresh_result=st.session_state.get("dashboard_refresh_mode_last_result"),
        last_macro_refresh_result=st.session_state.get("dashboard_macro_last_refresh_result"),
    )
    updated_text = _dashboard_macro_updated_text(freshness)
    st.markdown(
        (
            '<section class="dashboard-command-center">'
            '<details class="dashboard-command-details">'
            '<summary class="dashboard-command-line">'
            f'<span class="dashboard-command-summary">{summary_html}</span>'
            f'<span class="dashboard-command-primary">{macro_pills_html}</span>'
            f'<span class="dashboard-command-updated">{escape(updated_text)}</span>'
            '<b class="dashboard-command-trigger">详情</b>'
            "</summary>"
            f"{detail_html}"
            "</details>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _dashboard_command_status_items(table, macro_regime, freshness, portfolio_structure_check) -> list[tuple[str, str, str]]:
    return [
        ("F&G", _dashboard_fear_greed_pill_text(macro_regime), "sentiment"),
        ("VIX", _dashboard_vix_pill_text(macro_regime), "vix"),
    ]


def _dashboard_command_summary_items(macro_regime, freshness) -> list[tuple[str, str, str]]:
    return [
        ("大盘", str(getattr(macro_regime, "regime", "") or "数据不足"), "neutral"),
        ("数据", _compact_macro_data_status(str(getattr(macro_regime, "data_status", "") or "")), "neutral"),
    ]


def _dashboard_macro_updated_text(freshness) -> str:
    text = str(_freshness_status_text(freshness, "macro") or "").strip()
    if not text:
        return "暂缺"
    if text in {"过期", "暂缺", "缺失", "无数据"}:
        return text
    return f"{text}更新"


def _dashboard_fear_greed_pill_text(macro_regime) -> str:
    snapshot = _macro_indicator(macro_regime, FEAR_GREED)
    value = _dashboard_number(getattr(snapshot, "value", None))
    if value is None:
        return "暂缺"
    rating = _dashboard_fear_greed_rating_label(getattr(snapshot, "rating", None), value)
    return f"{value:.0f} {rating}"


def _dashboard_fear_greed_rating_label(rating: object, value: float) -> str:
    text = str(rating or "").strip().lower().replace("_", " ")
    mapping = {
        "extreme fear": "极度恐惧",
        "fear": "恐惧",
        "neutral": "中性",
        "greed": "贪婪",
        "extreme greed": "极度贪婪",
    }
    if text in mapping:
        return mapping[text]
    if value <= 25:
        return "极度恐惧"
    if value <= 45:
        return "恐惧"
    if value < 55:
        return "中性"
    if value < 75:
        return "贪婪"
    return "极度贪婪"


def _dashboard_vix_pill_text(macro_regime) -> str:
    snapshot = _macro_indicator(macro_regime, VIX)
    value = _dashboard_number(getattr(snapshot, "value", None))
    if value is None or value < 1:
        return "暂缺"
    return f"{value:.1f}"


def _dashboard_vix_status_text(macro_regime) -> str:
    snapshot = _macro_indicator(macro_regime, VIX)
    value = _dashboard_number(getattr(snapshot, "value", None))
    if value is None or value < 1:
        return "VIX 暂缺"
    suffix = "（缓存）" if _dashboard_indicator_uses_cache(snapshot) else ""
    return f"VIX {value:.1f}{suffix}"


def _dashboard_hy_oas_status_text(macro_regime) -> str:
    official = _macro_indicator(macro_regime, HY_OAS)
    official_value = _dashboard_number(getattr(official, "value", None))
    if official_value is not None and not bool(getattr(official, "is_stale", False)):
        suffix = "（缓存）" if _dashboard_indicator_uses_cache(official) else ""
        return f"HY OAS {official_value:.2f}%{suffix}"
    proxy = _macro_indicator(macro_regime, HYG_CREDIT_PROXY)
    proxy_value = _dashboard_number(getattr(proxy, "value", None))
    if proxy_value is None or bool(getattr(proxy, "is_stale", False)):
        return "HY OAS 暂缺"
    if proxy_value >= 75:
        return "HY OAS 官方暂缺｜信用代理承压"
    if proxy_value >= 60:
        return "HY OAS 官方暂缺｜信用代理转弱"
    return "HY OAS 官方暂缺｜信用代理稳定"


def _dashboard_dollar_status_text(macro_regime) -> str:
    official = _macro_indicator(macro_regime, DOLLAR_INDEX)
    official_value = _dashboard_number(getattr(official, "value", None))
    if official_value is not None and not bool(getattr(official, "is_stale", False)):
        suffix = "（缓存）" if _dashboard_indicator_uses_cache(official) else ""
        source = str(getattr(official, "source", "") or "").upper()
        if "DTWEXBGS" in source:
            return f"美元广义指数 {official_value:.2f}{suffix}"
        return f"美元指数 DXY {official_value:.2f}{suffix}"
    proxy = _macro_indicator(macro_regime, DOLLAR_PROXY)
    proxy_value = _dashboard_number(getattr(proxy, "value", None))
    if proxy_value is None or bool(getattr(proxy, "is_stale", False)):
        return "美元指数暂缺"
    rating = str(getattr(proxy, "rating", "") or "")
    if not rating:
        rating = "走强" if proxy_value >= 62 else "走弱" if proxy_value <= 38 else "稳定"
    return f"美元 proxy：UUP {rating}"


def _dashboard_ten_year_status_text(macro_regime) -> str:
    snapshot = _macro_indicator(macro_regime, TEN_YEAR_YIELD)
    value = _dashboard_number(getattr(snapshot, "value", None))
    if value is None or bool(getattr(snapshot, "is_stale", False)):
        return "10Y 暂缺"
    suffix = "（缓存）" if _dashboard_indicator_uses_cache(snapshot) else ""
    return f"10Y {value:.1f}%{suffix}"


def _dashboard_market_breadth_status_text(macro_regime) -> str:
    snapshot = _macro_indicator(macro_regime, MARKET_BREADTH)
    value = _dashboard_number(getattr(snapshot, "value", None))
    if value is None or bool(getattr(snapshot, "is_stale", False)):
        return "观察池强弱：暂缺"
    suffix = "（缓存）" if _dashboard_indicator_uses_cache(snapshot) else ""
    return f"观察池强弱：{value:.1f}%｜{_dashboard_watchlist_strength_label(value)}{suffix}"


def _dashboard_watchlist_strength_label(value: float) -> str:
    if value > 70:
        return "很强"
    if value >= 50:
        return "偏强"
    if value >= 35:
        return "偏弱"
    if value >= 20:
        return "很弱"
    return "极弱"


def _macro_indicator(macro_regime, indicator: str):
    if macro_regime is None or not hasattr(macro_regime, "indicator"):
        return None
    try:
        return macro_regime.indicator(indicator)
    except Exception:
        return None


def _dashboard_number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dashboard_indicator_uses_cache(snapshot) -> bool:
    source = str(getattr(snapshot, "source", "") or "").lower()
    return bool(getattr(snapshot, "error", None)) or "cache" in source or "cached" in source or "缓存" in source


def _dashboard_command_status_item_html(label: str, value: str, tone: str = "neutral") -> str:
    if label:
        return (
            f'<span class="dashboard-command-item {escape(tone)}">'
            f'<b>{escape(label)}</b>{escape(value)}'
            "</span>"
        )
    return f'<span class="dashboard-command-item {escape(tone)}"><strong>{escape(value)}</strong></span>'


def _dashboard_macro_pill_html(label: str, value: str, tone: str = "neutral") -> str:
    return (
        f'<span class="dashboard-macro-pill {escape(tone)}">'
        f"<b>{escape(label)}</b>"
        f"<strong>{escape(value)}</strong>"
        "</span>"
    )


def _dashboard_command_summary_item_html(label: str, value: str, tone: str = "neutral") -> str:
    return (
        f'<span class="dashboard-command-summary-item {escape(tone)}">'
        f"<b>{escape(label)}</b>"
        f"<strong>{escape(value)}</strong>"
        "</span>"
    )


def _dashboard_command_detail_html(
    macro_regime,
    freshness,
    *,
    last_refresh_result: dict | None = None,
    last_macro_refresh_result: dict | None = None,
) -> str:
    return (
        '<div class="dashboard-command-detail-panel">'
        f"{_dashboard_macro_indicator_group_html('核心指标', _dashboard_core_macro_detail_rows(macro_regime))}"
        f"{_dashboard_macro_indicator_group_html('辅助指标', _dashboard_auxiliary_macro_detail_rows(macro_regime))}"
        f"{_dashboard_refresh_log_detail_block_html(last_refresh_result, last_macro_refresh_result)}"
        f"{_dashboard_macro_diagnostics_html(last_macro_refresh_result)}"
        "</div>"
    )


def _dashboard_core_macro_detail_rows(macro_regime) -> list[tuple[str, str, str, str]]:
    return [
        _dashboard_macro_detail_row("F&G", _macro_indicator(macro_regime, FEAR_GREED)),
        _dashboard_macro_detail_row("VIX", _macro_indicator(macro_regime, VIX)),
        _dashboard_macro_detail_row("10Y", _macro_indicator(macro_regime, TEN_YEAR_YIELD)),
        _dashboard_macro_detail_row("10Y-2Y", _macro_indicator(macro_regime, YIELD_CURVE_10Y2Y)),
        _dashboard_macro_detail_row("大盘趋势", _macro_indicator(macro_regime, MARKET_TREND)),
        _dashboard_macro_detail_row("观察池强弱", _macro_indicator(macro_regime, MARKET_BREADTH)),
    ]


def _dashboard_auxiliary_macro_detail_rows(macro_regime) -> list[tuple[str, str, str, str]]:
    return [
        _dashboard_macro_detail_row("HY OAS", _macro_indicator(macro_regime, HY_OAS)),
        _dashboard_macro_detail_row("信用代理", _macro_indicator(macro_regime, HYG_CREDIT_PROXY)),
        _dashboard_macro_detail_row("美元指数", _macro_indicator(macro_regime, DOLLAR_INDEX)),
        _dashboard_macro_detail_row("美元代理", _macro_indicator(macro_regime, DOLLAR_PROXY)),
        _dashboard_macro_detail_row("情绪代理", _macro_indicator(macro_regime, SENTIMENT_PROXY)),
    ]


def _dashboard_macro_indicator_group_html(title: str, rows: list[tuple[str, str, str, str]]) -> str:
    row_html = "".join(
        "<tr>"
        f"<td>{escape(label)}</td>"
        f"<td>{escape(value)}</td>"
        f"<td>{escape(source)}</td>"
        f"<td>{escape(status)}</td>"
        "</tr>"
        for label, value, source, status in rows
    )
    return (
        '<section class="dashboard-command-detail-section">'
        f"<h4>{escape(title)}</h4>"
        '<table><thead><tr><th>指标</th><th>当前值</th><th>来源</th><th>状态</th></tr></thead>'
        f"<tbody>{row_html}</tbody></table>"
        "</section>"
    )


def _dashboard_macro_detail_row(label: str, snapshot) -> tuple[str, str, str, str]:
    return (
        label,
        _dashboard_macro_detail_value(label, snapshot),
        _dashboard_macro_detail_source(snapshot),
        _dashboard_macro_detail_status(snapshot),
    )


def _dashboard_macro_detail_value(label: str, snapshot) -> str:
    if snapshot is None:
        return "暂缺"
    indicator = str(getattr(snapshot, "indicator", "") or "")
    value = _dashboard_number(getattr(snapshot, "value", None))
    if indicator == VIX and (value is None or value < 1):
        return "暂缺"
    if value is None:
        return "暂缺"
    if indicator == FEAR_GREED:
        return f"{value:.0f} {_dashboard_fear_greed_rating_label(getattr(snapshot, 'rating', None), value)}"
    if indicator == VIX:
        return f"{value:.1f}"
    if indicator in {HY_OAS, TEN_YEAR_YIELD, YIELD_CURVE_10Y2Y}:
        return f"{value:.2f}%"
    if indicator == MARKET_BREADTH:
        return f"{value:.1f}%｜{_dashboard_watchlist_strength_label(value)}"
    if indicator in {HYG_CREDIT_PROXY, DOLLAR_PROXY, SENTIMENT_PROXY}:
        rating = str(getattr(snapshot, "rating", "") or "").strip()
        if rating:
            return f"{rating} {value:.0f}"
        if indicator == HYG_CREDIT_PROXY:
            return f"{_dashboard_credit_proxy_label(value)} {value:.0f}"
        if indicator == SENTIMENT_PROXY:
            return f"{_dashboard_sentiment_proxy_label(value)} {value:.0f}"
    return f"{value:.2f}" if abs(value) < 10 else f"{value:.1f}"


def _dashboard_macro_detail_source(snapshot) -> str:
    if snapshot is None:
        return "—"
    source = str(getattr(snapshot, "source", "") or "").strip()
    if not source:
        return "本地缓存"
    if len(source) > 34:
        return f"{source[:31]}..."
    return source


def _dashboard_macro_detail_status(snapshot) -> str:
    if snapshot is None:
        return "暂缺"
    value = _dashboard_number(getattr(snapshot, "value", None))
    if str(getattr(snapshot, "indicator", "") or "") == VIX and (value is None or value < 1):
        return "暂缺"
    if value is None:
        return "暂缺"
    if bool(getattr(snapshot, "is_stale", False)):
        return "过期"
    source = str(getattr(snapshot, "source", "") or "").lower()
    if "proxy" in source or "代理" in source or str(getattr(snapshot, "indicator", "") or "") in {HYG_CREDIT_PROXY, DOLLAR_PROXY, SENTIMENT_PROXY}:
        return "代理"
    if _dashboard_indicator_uses_cache(snapshot):
        return "缓存"
    return "可用"


def _dashboard_credit_proxy_label(value: float) -> str:
    if value >= 75:
        return "承压"
    if value >= 60:
        return "转弱"
    return "稳定"


def _dashboard_sentiment_proxy_label(value: float) -> str:
    if value <= 35:
        return "偏恐惧"
    if value >= 65:
        return "偏贪婪"
    return "中性"


def _dashboard_freshness_detail_block_html(
    freshness,
    *,
    last_refresh_result: dict | None = None,
    last_macro_refresh_result: dict | None = None,
) -> str:
    pills = "".join(
        f'<span class="dashboard-freshness-pill {escape(str(item.tone))}" title="{escape(str(item.detail))}">'
        f'<b>{escape(str(item.label))}</b>{escape(str(item.status_text))}'
        "</span>"
        for item in freshness.items
    )
    rows = "".join(
        "<li>"
        f"<b>{escape(str(item.label))}</b>"
        f"<span>{escape(str(item.status_text))}｜{escape(str(item.source))}｜{escape(_format_dashboard_time(item.updated_at))}</span>"
        f"<em>{escape(str(item.detail))}</em>"
        "</li>"
        for item in freshness.items
    )
    refresh_note = _dashboard_last_refresh_note(last_refresh_result, last_macro_refresh_result)
    refresh_html = f'<p>{escape(refresh_note)}</p>' if refresh_note else ""
    return (
        '<section class="dashboard-command-detail-card">'
        '<div><strong>数据新鲜度</strong><span>默认折叠，避免压过观察名单</span></div>'
        f'<div class="dashboard-freshness-items compact">{pills}</div>'
        f"<ul>{rows}</ul>"
        f"{refresh_html}"
        "</section>"
    )


def _dashboard_portfolio_structure_detail_block_html(portfolio_structure_check) -> str:
    if portfolio_structure_check is None:
        return (
            '<section class="dashboard-command-detail-card">'
            '<div><strong>仓位结构</strong><span>暂无组合结构快照</span></div>'
            "</section>"
        )
    compact_items = [
        ("现金", _pct_text(getattr(portfolio_structure_check, "cash_pct", None))),
        ("最大单票", _pct_text(getattr(portfolio_structure_check, "largest_position_pct", None))),
        ("前三大", _pct_text(getattr(portfolio_structure_check, "top3_position_pct", None))),
        ("C类", _pct_text((getattr(portfolio_structure_check, "tier_pct", {}) or {}).get("C"))),
    ]
    stats_html = "".join(
        f'<span><b>{escape(label)}</b>{escape(value)}</span>'
        for label, value in compact_items
    )
    reasons = list(getattr(portfolio_structure_check, "reasons", []) or [])
    hints = list(getattr(portfolio_structure_check, "action_hints", []) or [])
    reason_html = "".join(f"<li>{escape(str(item))}</li>" for item in [*reasons[:4], *hints[:3]]) or "<li>暂无结构警报。</li>"
    return (
        '<section class="dashboard-command-detail-card">'
        f'<div><strong>仓位结构：{escape(str(getattr(portfolio_structure_check, "status", "") or "未计算"))}</strong><span>现金、集中度和 C 类仓位</span></div>'
        f'<div class="dashboard-command-mini-stats">{stats_html}</div>'
        f"<ul>{reason_html}</ul>"
        "</section>"
    )


def _dashboard_refresh_log_detail_block_html(
    last_refresh_result: dict | None,
    last_macro_refresh_result: dict | None,
) -> str:
    rows: list[str] = []
    if isinstance(last_refresh_result, dict):
        rows.append(_dashboard_refresh_log_row_html(last_refresh_result))
    if isinstance(last_macro_refresh_result, dict) and last_macro_refresh_result:
        rows.append(_dashboard_macro_refresh_log_row_html(last_macro_refresh_result))
    if not rows:
        rows.append("<li><b>最近刷新</b><span>暂无本次会话刷新记录。</span></li>")
    return (
        '<section class="dashboard-command-detail-section dashboard-command-refresh-section">'
        "<h4>最近刷新</h4>"
        f"<ul>{''.join(rows)}</ul>"
        "</section>"
    )


def _dashboard_macro_diagnostics_html(last_macro_refresh_result: dict | None) -> str:
    if not isinstance(last_macro_refresh_result, dict):
        return ""
    errors: list[str] = []
    raw_error = str(last_macro_refresh_result.get("error") or "").strip()
    if raw_error:
        errors.append(raw_error)
    for item in list(last_macro_refresh_result.get("indicator_results") or []):
        if not isinstance(item, dict):
            continue
        error = str(item.get("error") or "").strip()
        if not error:
            continue
        label = _macro_indicator_label(str(item.get("indicator") or ""))
        errors.append(f"{label}: {error}")
    if not errors:
        return ""
    error_html = "".join(f"<li>{escape(error)}</li>" for error in errors[:8])
    return (
        '<details class="dashboard-command-diagnostics">'
        "<summary>技术诊断</summary>"
        f"<ul>{error_html}</ul>"
        "</details>"
    )


def _dashboard_refresh_log_row_html(result: dict) -> str:
    mode = str(result.get("mode") or "")
    status = str(result.get("status") or "")
    duration = result.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "未知"
    return (
        "<li>"
        f"<b>{escape(_refresh_mode_label(mode))}</b>"
        f"<span>{escape(_refresh_status_label(status))}｜成功 {int(result.get('refreshed_count') or 0)}｜"
        f"跳过 {int(result.get('skipped_count') or 0)}｜失败 {int(result.get('failed_count') or 0)}｜用时 {escape(duration_text)}</span>"
        "</li>"
    )


def _dashboard_macro_refresh_log_row_html(result: dict) -> str:
    status = str(result.get("overall_status") or result.get("status") or "")
    duration = result.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "未知"
    return (
        "<li>"
        "<b>大盘环境</b>"
        f"<span>{escape(_macro_refresh_status_label(status))}｜用时 {escape(duration_text)}</span>"
        "</li>"
    )


def _dashboard_last_refresh_note(last_refresh_result: dict | None, last_macro_refresh_result: dict | None) -> str:
    if not isinstance(last_refresh_result, dict):
        return ""
    mode = str(last_refresh_result.get("mode") or "")
    duration = last_refresh_result.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "未知"
    note = (
        f"最近：{_refresh_mode_label(mode)}｜成功 {int(last_refresh_result.get('refreshed_count') or 0)}｜"
        f"跳过 {int(last_refresh_result.get('skipped_count') or 0)}｜失败 {int(last_refresh_result.get('failed_count') or 0)}｜用时 {duration_text}"
    )
    if mode == "MACRO_ONLY" and isinstance(last_macro_refresh_result, dict):
        macro_status = str(last_macro_refresh_result.get("overall_status") or last_macro_refresh_result.get("status") or "")
        if macro_status:
            note = f"{note}｜宏观 {_macro_refresh_status_label(macro_status)}"
    return note


def _compact_macro_data_status(value: str) -> str:
    text = str(value or "").strip()
    return text.split("｜", 1)[0] if text else "缺失"


def _freshness_status_text(freshness, key: str) -> str:
    item = freshness.item(key)
    return str(getattr(item, "status_text", "") or "缺失")


def _freshness_tone(freshness, key: str) -> str:
    item = freshness.item(key)
    tone = str(getattr(item, "tone", "") or "missing")
    return {
        "fresh": "ok",
        "warn": "warn",
        "stale": "danger",
        "missing": "muted",
    }.get(tone, "neutral")


def _portfolio_status_tone(portfolio_structure_check) -> str:
    status = str(getattr(portfolio_structure_check, "status", "") or "")
    return {
        "健康": "ok",
        "偏激进": "warn",
        "失衡": "warn",
        "危险": "danger",
    }.get(status, "neutral")


def _format_dashboard_time(value: object) -> str:
    if not value:
        return "无更新时间"
    return str(value).replace("T", " ")[:16]


def _pct_text(value: object) -> str:
    try:
        if value is None:
            return "N/A"
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def _render_weekly_discipline_strip() -> None:
    try:
        summary = build_trading_discipline_summary()
    except Exception:
        return
    level = _dashboard_effective_discipline_level(summary)
    emotional_count = (
        int(summary.get("fomoTradeCount") or 0)
        + int(summary.get("anxietyPanicTradeCount") or 0)
        + int(summary.get("revengeTradeCount") or 0)
    )
    suspected_sell_fly_count = int(summary.get("suspectedSellFlyCount") or 0)
    reentry_alert_count = (
        int(summary.get("reentryObligationTriggeredCount") or 0)
        + int(summary.get("reentryObligationOverdueCount") or 0)
        + int(summary.get("reentryObligationMissingPlanCount") or 0)
    )
    items = [
        ("本周操作", summary.get("totalTradesThisWeek", 0)),
        ("A 类卖出", summary.get("aClassSellCountThisWeek", 0)),
        ("情绪化操作", emotional_count),
        ("疑似卖飞", suspected_sell_fly_count),
        ("回补警报", reentry_alert_count),
    ]
    item_html = "".join(
        f'<span>{escape(label)} {escape(str(value))}</span>'
        for label, value in items
    )
    alert_count = (
        int(summary.get("aClassSellCountThisWeek") or 0)
        + int(summary.get("macroSellCountThisWeek") or 0)
        + int(summary.get("noReentryPlanSellCount") or 0)
        + int(summary.get("disciplineBlockerCount") or 0)
        + emotional_count
        + suspected_sell_fly_count
        + reentry_alert_count
    )
    headline = "暂无纪律警报" if level == "normal" and alert_count == 0 else {
        "normal": "纪律正常",
        "caution": "本周操作偏多，注意是否焦虑驱动",
        "danger": "交易纪律风险高，建议暂停非必要操作",
        "stop": "本周停止主动卖出，只允许复核和计划",
    }.get(level, "纪律正常")
    st.markdown(
        (
            f'<section class="dashboard-discipline-strip {escape(_dashboard_discipline_tone(level))}">'
            '<div class="dashboard-discipline-main-row">'
            f'<div class="dashboard-discipline-title"><strong>本周交易纪律：{escape(_dashboard_discipline_level_text(level))}</strong><span>{escape(headline)}</span></div>'
            f'<div class="dashboard-discipline-metrics">{item_html}</div>'
            "</div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_price_alert_strip(tickers: list[str]) -> None:
    try:
        alerts = triggered_price_alerts(symbols=tickers)
    except Exception:
        return
    if not alerts:
        return
    items = "".join(
        '<span>'
        f'<b>{escape(str(alert.get("symbol") or ""))}</b> '
        f'{escape(format_currency(alert.get("triggerPrice")))}'
        f'{" · 数据可能过期" if alert.get("priceDataStale") else ""}'
        '</span>'
        for alert in alerts[:4]
    )
    st.markdown(
        (
            '<section class="dashboard-price-alert-strip">'
            '<div class="dashboard-price-alert-title">'
            '<strong>价格提醒已触发</strong>'
            '<span>到价只提醒复核，不代表自动可以买。</span>'
            '</div>'
            f'<div class="dashboard-price-alert-items">{items}</div>'
            '</section>'
        ),
        unsafe_allow_html=True,
    )


def _dashboard_discipline_tone(level: str) -> str:
    return {
        "normal": "ok",
        "caution": "warning",
        "danger": "error",
        "stop": "error",
    }.get(str(level or ""), "ok")


def _dashboard_discipline_level_text(level: str) -> str:
    return {
        "normal": "正常",
        "caution": "注意",
        "danger": "危险",
        "stop": "停止",
    }.get(str(level or ""), "正常")


def _dashboard_effective_discipline_level(summary: dict[str, object]) -> str:
    over_trading_level = str(summary.get("overTradingLevel") or "normal")
    score_level = str(summary.get("disciplineLevel") or "normal")
    return max([over_trading_level, score_level], key=_dashboard_discipline_level_rank)


def _dashboard_discipline_level_rank(level: str) -> int:
    return {"normal": 0, "caution": 1, "danger": 2, "stop": 3}.get(str(level or ""), 0)


def _build_data_health_context(table: pd.DataFrame) -> dict[str, object]:
    symbols = _dashboard_symbols(table)
    try:
        summary = build_data_health_summary(watchlist=symbols)
        view = build_dashboard_data_health_view_from_summary(summary, symbols)
        raw_issues = list(summary.get("topIssues") or [])
    except Exception:
        summary = {}
        view = build_dashboard_data_health_view(table)
        raw_issues = list(view.get("issues") or [])
    return {
        "summary": summary,
        "view": view,
        "rawIssues": raw_issues,
        "symbols": symbols,
        "lastUpdated": st.session_state.get("dashboard_last_table_loaded_at"),
    }


def _render_data_health_strip(context: dict[str, object]) -> None:
    view = dict(context.get("view") or {})
    summary = dict(context.get("summary") or {})
    raw_issues = list(context.get("rawIssues") or [])
    healthy_count = int(summary.get("healthyCount") or _data_health_item_value(view, "健康项") or 0)
    abnormal_count = _data_health_abnormal_count(summary, raw_issues)
    last_updated = _dashboard_last_updated_text(context.get("lastUpdated"))
    items = [
        ("系统状态", view.get("statusLabel") or "注意"),
        ("健康", healthy_count),
        ("异常", abnormal_count),
        ("最后更新", last_updated),
    ]
    item_html = "".join(
        f'<span>{escape(label)} {escape(str(value))}</span>'
        for label, value in items
    )
    st.markdown(
        (
            f'<section id="data-health-strip" class="data-health-strip {escape(str(view.get("tone") or "warning"))}">'
            '<div class="data-health-main-row">'
            f'<div class="data-health-title"><strong>数据健康：{escape(str(view.get("statusLabel") or "注意"))}</strong><span>异常驱动展示，本地缓存体检</span></div>'
            f'<div class="data-health-metrics">{item_html}</div>'
            "</div>"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_data_health_detail_section(context: dict[str, object]) -> None:
    raw_issues = list(context.get("rawIssues") or [])
    _render_data_health_refresh_result()
    with st.expander("数据健康详情", expanded=_data_health_should_auto_expand(context)):
        _render_data_health_detail_groups(raw_issues)


def _render_dashboard_system_status(
    data_health_context: dict[str, object],
    risk_items: list[dict[str, object]],
    table: pd.DataFrame,
    macro_regime,
) -> None:
    raw_issues = list(data_health_context.get("rawIssues") or [])
    expanded = bool(st.session_state.get(RISK_RADAR_FILTER_SESSION_KEY))
    st.markdown('<div id="dashboard-system-status"></div>', unsafe_allow_html=True)
    with st.expander("系统状态 / 风险雷达", expanded=expanded):
        st.markdown('<div class="drawer-section-title">大盘环境</div>', unsafe_allow_html=True)
        st.markdown(macro_regime_detail_html(macro_regime), unsafe_allow_html=True)
        _render_risk_radar_summary_strip(risk_items, table)
        st.markdown(_dashboard_risk_radar_html(risk_items), unsafe_allow_html=True)
        _render_weekly_discipline_strip()
        _render_data_health_refresh_result()
        st.markdown('<div class="drawer-section-title">数据健康详情</div>', unsafe_allow_html=True)
        _render_data_health_detail_groups(raw_issues)


def _data_health_should_auto_expand(context: dict[str, object]) -> bool:
    view = dict(context.get("view") or {})
    summary = dict(context.get("summary") or {})
    if str(view.get("tone") or "").lower() in {"error", "warning"}:
        return True
    return _data_health_abnormal_count(summary, list(context.get("rawIssues") or [])) > 0


def _data_health_abnormal_count(summary: dict[str, object], raw_issues: list[object]) -> int:
    if summary:
        return sum(
            int(summary.get(key) or 0)
            for key in (
                "missingPriceCount",
                "stalePriceCount",
                "missingHistoryCount",
                "staleHistoryCount",
                "finalDecisionErrorCount",
                "portfolioMissingPriceCount",
                "outcomeMissingCount",
            )
        )
    return len(raw_issues)


def _data_health_item_value(view: dict[str, object], label: str) -> object:
    for item_label, value in list(view.get("items") or []):
        if label in str(item_label):
            return value
    return None


def _dashboard_last_updated_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "N/A"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text[:16]
    return parsed.strftime("%H:%M")


def _render_data_health_refresh_result() -> None:
    result = st.session_state.get("data_health_last_refresh_result")
    if not isinstance(result, dict):
        return
    status = str(result.get("status") or "failed")
    tone = {"success": "ok", "partial": "warning"}.get(status, "error")
    error = str(result.get("error") or "无")
    st.markdown(
        (
            f'<div class="data-health-refresh-result {escape(tone)}">'
            f'<strong>{escape(str(result.get("symbol") or ""))} 刷新{escape(_refresh_status_label(status))}</strong>'
            f'<span>quoteStatus: {escape(str(result.get("quoteStatus") or "N/A"))} · '
            f'historyStatus: {escape(str(result.get("historyStatus") or "N/A"))} · error: {escape(error)}</span>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_macro_refresh_result() -> None:
    result = st.session_state.get("dashboard_macro_last_refresh_result")
    if not isinstance(result, dict):
        return
    status = str(result.get("overall_status") or result.get("status") or "failed")
    tone = {"success": "ok", "partial": "warning"}.get(status, "error")
    duration = result.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "未知"
    headline = f"大盘环境刷新完成：{_macro_refresh_status_label(status)}，用时 {duration_text}"
    indicator_results = list(result.get("indicator_results") or [])
    if not indicator_results:
        indicator_results = [
            {"indicator": key, **dict(value)}
            for key, value in dict(result.get("indicators") or {}).items()
            if isinstance(value, dict)
        ]
    core_rows = "".join(
        _macro_refresh_indicator_row_html(item)
        for item in indicator_results
        if _macro_refresh_indicator_category(item) == "core"
    )
    auxiliary_rows = "".join(
        _macro_refresh_indicator_row_html(item)
        for item in indicator_results
        if _macro_refresh_indicator_category(item) != "core"
    )
    error = str(result.get("error") or "").strip()
    error_html = _macro_refresh_error_summary_html(indicator_results, error)
    st.markdown(
        (
            f'<section class="macro-refresh-result {escape(tone)}">'
            f"<strong>{escape(headline)}</strong>"
            f'<div class="macro-refresh-group"><b>核心指标</b><div class="macro-refresh-grid">{core_rows}</div></div>'
            f'<div class="macro-refresh-group muted"><b>辅助指标</b><div class="macro-refresh-grid">{auxiliary_rows}</div></div>'
            f"{error_html}"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _render_dashboard_refresh_mode_result() -> None:
    result = st.session_state.get("dashboard_refresh_mode_last_result")
    if not isinstance(result, dict):
        return
    mode = str(result.get("mode") or "")
    status = str(result.get("status") or "failed")
    tone = {"success": "ok", "partial": "warning"}.get(status, "error")
    duration = result.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "未知"
    ticker_results = list(result.get("ticker_results") or [])
    details = " · ".join(
        [
            f"成功 {int(result.get('refreshed_count') or 0)}",
            f"跳过 {int(result.get('skipped_count') or 0)}",
            f"失败 {int(result.get('failed_count') or 0)}",
            f"用时 {duration_text}",
        ]
    )
    if mode == "PRICE_ONLY":
        live_count = int(result.get("live_success_count") or 0)
        cache_count = int(result.get("cache_fallback_count") or 0)
        quote_source = str(result.get("quote_source") or "quote")
        details = " · ".join([details, f"实时 {live_count}", f"缓存兜底 {cache_count}", f"来源 {quote_source}"])
        notes = [str(note) for note in (result.get("provider_notes") or []) if str(note).strip()]
        if notes:
            details = " · ".join([details, "；".join(notes[:2])])
    sample_rows = "".join(_dashboard_refresh_ticker_row_html(item) for item in ticker_results[:6])
    st.markdown(
        (
            f'<section class="dashboard-refresh-result {escape(tone)}">'
            f"<strong>{escape(_refresh_mode_label(mode))}：{escape(_refresh_status_label(status))}</strong>"
            f"<span>{escape(details)}</span>"
            f'<div class="dashboard-refresh-result-grid">{sample_rows}</div>'
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _dashboard_refresh_ticker_row_html(item: dict) -> str:
    ticker = str(item.get("ticker") or "")
    status = str(item.get("status") or "")
    message = str(item.get("message") or "")
    duration = item.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "—"
    return (
        "<div>"
        f"<b>{escape(ticker)}</b>"
        f"<span>{escape(_refresh_status_label(status))}｜{escape(duration_text)}｜{escape(message)}</span>"
        "</div>"
    )


def _refresh_mode_label(mode: str) -> str:
    return {
        "PRICE_ONLY": "更新价格",
        "DAILY_TECHNICAL": "更新技术",
        "FUNDAMENTALS_IF_EVENT": "财报后刷新基本面",
        "MACRO_ONLY": "刷新大盘环境",
        "FULL_REFRESH": "强制全量刷新",
    }.get(mode, mode or "刷新")


def _macro_refresh_indicator_row_html(item: dict) -> str:
    indicator = str(item.get("indicator") or "")
    label = str(item.get("label") or _macro_indicator_label(indicator))
    status = str(item.get("status") or "failed")
    value = _macro_refresh_value_text(item)
    source = str(item.get("source") or "未知来源")
    observation_date = str(item.get("observation_date") or item.get("fetched_at") or "无日期")
    duration = item.get("duration_seconds")
    duration_text = f"{float(duration):.1f}s" if isinstance(duration, (int, float)) else "—"
    error = str(item.get("error") or "").strip()
    display_status = _macro_refresh_indicator_display_status(item)
    note = _macro_refresh_indicator_note(item, display_status)
    diagnostic = (
        '<details class="macro-refresh-diagnostics">'
        '<summary>诊断详情</summary>'
        f"<code>{escape(error)}</code>"
        "</details>"
        if error
        else ""
    )
    return (
        "<div>"
        f"<b>{escape(label)}</b>"
        f"<span>{escape(display_status)}｜{escape(value)}｜来源：{escape(source)}｜更新时间：{escape(observation_date)}｜用时 {escape(duration_text)}</span>"
        f"<em>{escape(note)}</em>"
        f"{diagnostic}"
        "</div>"
    )


def _macro_refresh_indicator_category(item: dict) -> str:
    category = str(item.get("category") or "").strip().lower()
    if category:
        return category
    indicator = str(item.get("indicator") or "")
    return "core" if indicator in {"vix", "ten_year_yield", "yield_curve_10y2y", "market_trend", "market_breadth"} else "auxiliary"


def _macro_indicator_label(indicator: str) -> str:
    return {
        "vix": "VIX 波动率指数",
        "hy_oas": "美高收益债信用利差",
        "fear_greed": "CNN恐惧与贪婪指数",
        "ten_year_yield": "10年美债收益率",
        "yield_curve_10y2y": "美债10Y-2Y利差",
        "market_trend": "大盘趋势",
        "market_breadth": "观察池强弱",
        "dollar_index": "美元指数",
        "hyg_credit_proxy": "信用风险代理",
        "sentiment_proxy": "内部情绪代理",
    }.get(str(indicator or ""), str(indicator or "未知指标"))


def _macro_refresh_status_label(status: str) -> str:
    return {"success": "成功", "partial": "部分成功", "failed": "失败"}.get(status, status)


def _macro_refresh_indicator_status_label(status: str) -> str:
    return {
        "success": "成功",
        "failed": "失败",
        "cached_fallback": "使用缓存",
        "stale": "过期",
        "skipped": "跳过",
    }.get(status, status)


def _macro_refresh_indicator_display_status(item: dict) -> str:
    indicator = str(item.get("indicator") or "")
    status = str(item.get("status") or "failed")
    source = str(item.get("source") or "").lower()
    value = item.get("value")
    has_value = value not in (None, "")
    if indicator in {"hyg_credit_proxy", "sentiment_proxy"} and has_value:
        return "使用代理"
    if has_value and (bool(item.get("used_cache")) or status == "cached_fallback" or "cache" in source or "缓存" in source):
        return "过期缓存" if status == "stale" or bool(item.get("is_stale")) else "使用缓存"
    if has_value and status == "success":
        return "实时成功"
    if has_value and status == "stale":
        return "过期缓存"
    if not has_value and _macro_refresh_indicator_category(item) != "core":
        return "暂缺"
    if not has_value:
        return "刷新失败"
    return _macro_refresh_indicator_status_label(status)


def _macro_refresh_indicator_note(item: dict, display_status: str) -> str:
    indicator = str(item.get("indicator") or "")
    if display_status == "使用代理":
        return "官方源缺失时的代理参考，不等同于原始指标。"
    if display_status == "使用缓存":
        return "本次刷新未拿到新值，沿用最近成功缓存。"
    if display_status == "过期缓存":
        return "缓存已过期，仅作参考。"
    if display_status == "暂缺":
        if indicator == "hy_oas":
            return "官方信用利差暂缺；如有信用代理，优先看代理行。"
        if indicator == "fear_greed":
            return "CNN 原版暂缺；如有情绪代理，优先看代理行。"
        if indicator == "dollar_index":
            return "低优先级辅助指标，缺失不影响核心判断。"
        return "辅助指标暂缺，不影响核心判断。"
    if display_status == "刷新失败":
        return "核心指标刷新失败，需要复核数据源。"
    return "数据可用。"


def _macro_refresh_error_summary_html(indicator_results: list[dict], raw_error: str) -> str:
    if not raw_error and not any(str(item.get("error") or "").strip() for item in indicator_results):
        return ""
    missing_auxiliary = [
        _macro_indicator_label(str(item.get("indicator") or ""))
        for item in indicator_results
        if _macro_refresh_indicator_category(item) != "core"
        and _macro_refresh_indicator_display_status(item) in {"暂缺", "过期缓存"}
    ]
    failed_core = [
        _macro_indicator_label(str(item.get("indicator") or ""))
        for item in indicator_results
        if _macro_refresh_indicator_category(item) == "core"
        and _macro_refresh_indicator_display_status(item) == "刷新失败"
    ]
    lines = []
    if failed_core:
        lines.append("核心指标异常：" + " / ".join(failed_core) + "。")
    if missing_auxiliary:
        lines.append("辅助指标缺失：" + " / ".join(missing_auxiliary) + "。核心判断仍可用。")
    if not lines:
        lines.append("部分指标使用缓存或代理，核心判断仍可用。")
    diagnostic_items = [
        f"{_macro_indicator_label(str(item.get('indicator') or ''))}: {str(item.get('error') or '').strip()}"
        for item in indicator_results
        if str(item.get("error") or "").strip()
    ]
    if raw_error:
        diagnostic_items.append(raw_error)
    diagnostics = "<br>".join(escape(item) for item in diagnostic_items)
    diagnostic_html = (
        '<details class="macro-refresh-diagnostics macro-refresh-error-details">'
        '<summary>完整技术诊断</summary>'
        f"<code>{diagnostics}</code>"
        "</details>"
        if diagnostics
        else ""
    )
    return f'<div class="macro-refresh-error">{" ".join(escape(line) for line in lines)}</div>{diagnostic_html}'


def _macro_refresh_value_text(item: dict) -> str:
    value = item.get("value")
    if value is None or value == "":
        return "缺失"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    indicator = str(item.get("indicator") or "")
    if indicator == "fear_greed":
        return f"{numeric:.0f}"
    if indicator in {"hy_oas", "ten_year_yield", "yield_curve_10y2y", "market_breadth"}:
        return f"{numeric:.1f}%"
    return f"{numeric:.1f}"


def _render_data_health_detail_groups(issues: list[object]) -> None:
    groups = [
        ("价格缺失 / 过期", {"missing_price", "stale_quote"}, "刷新该股票"),
        ("历史缺失 / 过期", {"missing_history", "stale_history"}, "刷新该股票"),
        ("finalDecision 异常", {"final_decision_error"}, "查看评分"),
        ("持仓缺价", {"portfolio_missing_price"}, "查看持仓"),
        ("复盘结果缺失", {"outcome_missing"}, "进入交易日志"),
    ]
    normalized = [_normalize_data_health_issue(issue) for issue in issues]
    columns = st.columns(len(groups), gap="small")
    for column, (title, categories, fallback_action) in zip(columns, groups):
        matched = [issue for issue in normalized if issue["category"] in categories]
        with column:
            st.markdown(
                f'<div class="data-health-detail-head"><strong>{escape(title)}</strong><span>{len(matched)}</span></div>',
                unsafe_allow_html=True,
            )
            if not matched:
                st.markdown('<div class="data-health-detail-empty">暂无</div>', unsafe_allow_html=True)
                continue
            for index, issue in enumerate(matched[:5]):
                _render_data_health_issue_row(issue, fallback_action, index)


def _render_data_health_issue_row(issue: dict[str, str], fallback_action: str, index: int) -> None:
    symbol = issue.get("symbol") or "全局"
    category = issue.get("category") or ""
    can_refresh = bool(symbol and symbol != "全局" and category in {"missing_price", "stale_quote", "missing_history", "stale_history"})
    st.markdown(
        (
            '<div class="data-health-detail-row">'
            f'<strong>{escape(symbol)}</strong>'
            f'<span>{escape(issue.get("reason") or "数据问题")}</span>'
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if can_refresh:
        if st.button("刷新该股票", key=f"data-health-refresh-{symbol}-{category}-{index}", width="stretch"):
            st.session_state["data_health_last_refresh_result"] = _refresh_data_health_symbol(symbol)
            st.rerun()
    else:
        st.markdown(f'<em class="data-health-detail-action">{escape(fallback_action)}</em>', unsafe_allow_html=True)


def _refresh_data_health_symbol(symbol: str) -> dict[str, str]:
    normalized_symbol = str(symbol or "").strip().upper()
    try:
        result = refresh_symbol_market_data(normalized_symbol)
    except Exception as exc:
        return {
            "symbol": normalized_symbol,
            "status": "failed",
            "quoteStatus": "not_run",
            "historyStatus": "not_run",
            "error": _clean_refresh_error(exc),
        }
    if not isinstance(result, dict):
        return {
            "symbol": normalized_symbol,
            "status": "failed",
            "quoteStatus": "unknown",
            "historyStatus": "unknown",
            "error": "刷新服务返回异常",
        }
    return {
        "symbol": str(result.get("symbol") or normalized_symbol),
        "status": str(result.get("status") or "failed"),
        "quoteStatus": str(result.get("quoteStatus") or "N/A"),
        "historyStatus": str(result.get("historyStatus") or "N/A"),
        "error": str(result.get("error") or "无"),
    }


def _clean_refresh_error(error: object) -> str:
    message = str(error or "").strip()
    if not message:
        return "刷新失败，请稍后重试"
    return message.splitlines()[0]


def _normalize_data_health_issue(issue: object) -> dict[str, str]:
    if isinstance(issue, dict):
        category = str(issue.get("category") or "")
        symbol = str(issue.get("symbol") or "").upper()
        reason = str(issue.get("message") or _data_health_category_label(category) or "数据问题")
        return {"category": category, "symbol": symbol, "reason": reason}
    text = str(issue or "").strip()
    parts = text.split(maxsplit=1)
    symbol = parts[0] if parts and parts[0].replace(".", "").replace("-", "").isalnum() and parts[0].upper() == parts[0] else ""
    reason = parts[1] if symbol and len(parts) > 1 else text
    category = _data_health_category_from_text(text)
    return {"category": category, "symbol": symbol, "reason": reason or _data_health_category_label(category)}


def _data_health_category_from_text(text: str) -> str:
    if "价格缺失" in text:
        return "missing_price"
    if "价格过期" in text:
        return "stale_quote"
    if "历史缺失" in text:
        return "missing_history"
    if "历史过期" in text:
        return "stale_history"
    if "finalDecision" in text:
        return "final_decision_error"
    if "持仓缺价" in text:
        return "portfolio_missing_price"
    if "复盘" in text or "outcome" in text:
        return "outcome_missing"
    return "unknown"


def _data_health_category_label(category: str) -> str:
    return {
        "missing_price": "价格缺失",
        "stale_quote": "价格过期",
        "missing_history": "历史缺失",
        "stale_history": "历史过期",
        "final_decision_error": "finalDecision 异常",
        "portfolio_missing_price": "持仓缺价",
        "outcome_missing": "复盘结果缺失",
    }.get(category, "数据问题")


def _refresh_status_label(status: str) -> str:
    return {
        "success": "成功",
        "partial": "部分成功",
        "failed": "失败",
        "skipped": "跳过",
    }.get(status, "完成")


def _data_health_detail_groups_html(issues: list[object]) -> str:
    groups = [
        ("价格缺失", ("价格缺失", "价格过期", "缓存缺失"), "查看股票", "stock"),
        ("历史缺失", ("历史缺失",), "查看数据状态", "stock_data"),
        ("finalDecision 异常", ("finalDecision",), "查看评分", "stock_score"),
        ("持仓缺价", ("持仓缺价",), "查看持仓", "portfolio"),
        ("复盘结果缺失", ("复盘", "outcome"), "进入交易日志", "journal"),
    ]
    issue_texts = [str(issue) for issue in issues if str(issue).strip()]
    cards: list[str] = []
    for title, tokens, action, target in groups:
        matched = [text for text in issue_texts if any(token in text for token in tokens)]
        rows = "".join(_data_health_issue_row_html(text, action, target) for text in matched)
        if not rows:
            rows = '<div class="data-health-detail-empty">暂无</div>'
        cards.append(
            (
                '<div class="data-health-detail-group">'
                f'<div class="data-health-detail-head"><strong>{escape(title)}</strong><span>{len(matched)}</span></div>'
                f"{rows}"
                "</div>"
            )
        )
    return f'<div class="data-health-detail-panel">{"".join(cards)}</div>'


def _data_health_issue_row_html(issue: str, action: str, target: str) -> str:
    parts = issue.split(maxsplit=1)
    ticker = parts[0] if parts and parts[0].replace(".", "").replace("-", "").isalnum() and parts[0].upper() == parts[0] else "全局"
    reason = parts[1] if ticker != "全局" and len(parts) > 1 else issue
    action_html = _data_health_issue_action_html(action, target, ticker)
    return (
        '<div class="data-health-detail-row">'
        f'<strong>{escape(ticker)}</strong>'
        f'<span>{escape(reason)}</span>'
        f"{action_html}"
        "</div>"
    )


def _data_health_issue_action_html(action: str, target: str, ticker: str) -> str:
    href = ""
    if target in {"stock", "stock_data", "stock_score"} and ticker != "全局":
        focus = "data" if target == "stock_data" else "score" if target == "stock_score" else "summary"
        href = f"?page=detail&symbol={escape(ticker, quote=True)}&focus={focus}"
    elif target == "portfolio":
        href = "?page=portfolio"
    elif target == "journal":
        href = "?page=trade-journal"
    if not href:
        return f'<em>{escape(action)}</em>'
    return f'<a class="data-health-detail-action" href="{href}" target="_self">{escape(action)}</a>'


def _render_risk_radar_summary_strip(items: list[dict[str, object]], table: pd.DataFrame) -> None:
    summary_items = [
        ("超仓", _risk_item_symbol_count(items, "overweight")),
        ("禁止追高", _risk_item_symbol_count(items, "noChase")),
        ("需要核验", _risk_item_symbol_count(items, "review")),
        ("低置信", _risk_item_symbol_count(items, "lowConfidence")),
        ("不可新增", _risk_item_symbol_count(items, "noAdd")),
        ("blocker", _dashboard_blocker_count(table)),
    ]
    item_html = "".join(
        f'<span>{escape(label)} <strong>{escape(str(value))}</strong></span>'
        for label, value in summary_items
    )
    total = sum(int(value or 0) for _, value in summary_items)
    tone = "warning" if total else "ok"
    st.markdown(
        (
            f'<section class="dashboard-risk-summary-strip {tone}">'
            '<div class="dashboard-risk-summary-title"><strong>风险雷达</strong><span>风险摘要</span></div>'
            f'<div class="dashboard-risk-summary-metrics">{item_html}</div>'
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _dashboard_risk_total_count(items: list[dict[str, object]], table: pd.DataFrame) -> int:
    return sum(
        int(value or 0)
        for value in (
            _risk_item_symbol_count(items, "overweight"),
            _risk_item_symbol_count(items, "noChase"),
            _risk_item_symbol_count(items, "review"),
            _risk_item_symbol_count(items, "lowConfidence"),
            _risk_item_symbol_count(items, "noAdd"),
            _dashboard_blocker_count(table),
        )
    )


def _risk_item_symbol_count(items: list[dict[str, object]], key: str) -> int:
    for item in items:
        if str(item.get("key") or "") == key:
            return len([symbol for symbol in item.get("symbols", []) if symbol])
    return 0


def _dashboard_blocker_count(table: pd.DataFrame) -> int:
    return sum(
        1
        for _, row in table.iterrows()
        if _row_decision_lane(row) == "blocked" or _row_final_action(row) in DASHBOARD_BLOCKED_ACTIONS
    )


def _render_dashboard_risk_radar(items: list[dict[str, object]]) -> None:
    st.markdown('<div id="dashboard-risk-radar-detail"></div>', unsafe_allow_html=True)
    with st.expander("风险雷达详情", expanded=bool(st.session_state.get(RISK_RADAR_FILTER_SESSION_KEY))):
        st.markdown(_dashboard_risk_radar_html(items), unsafe_allow_html=True)


def _dashboard_risk_radar_html(items: list[dict[str, object]]) -> str:
    cards = "".join(_dashboard_risk_radar_item_html(item) for item in items)
    return (
        '<section class="dashboard-risk-radar">'
        '<div class="dashboard-risk-radar-head"><strong>风险雷达</strong><span>点击风险项筛选观察名单</span></div>'
        f'<div class="dashboard-risk-radar-list">{cards}</div>'
        "</section>"
    )


def _dashboard_risk_radar_item_html(item: dict[str, object]) -> str:
    symbols = [str(symbol) for symbol in item.get("symbols", []) if symbol]
    key = str(item.get("key") or "")
    active = " active" if st.session_state.get(RISK_RADAR_FILTER_SESSION_KEY) == key else ""
    return (
        f'<a class="dashboard-risk-radar-item {escape(str(item.get("tone") or "slate"))}{active}" '
        f'href="?page=dashboard&riskFilter={escape(key, quote=True)}#watchlist-table" target="_self">'
        '<div>'
        f'<span>{escape(str(item.get("label") or ""))}</span>'
        f'<strong>{len(symbols)}</strong>'
        "</div>"
        f'<em>{escape(str(item.get("reason") or ""))}</em>'
        "</a>"
    )

def _render_summary_sections(table: pd.DataFrame) -> None:
    st.markdown(_dashboard_filter_strip_html(table), unsafe_allow_html=True)


def _dashboard_filter_strip_html(table: pd.DataFrame) -> str:
    return (
        '<section class="dashboard-filter-strip">'
        '<div class="dashboard-filter-title"><strong>决策筛选</strong><span>点击聚焦主表</span></div>'
        f'<div class="dashboard-filter-chips">{_dashboard_filter_chips_html(table)}</div>'
        "</section>"
    )


def _dashboard_filter_chips_html(table: pd.DataFrame) -> str:
    summary_groups = _summary_lane_groups(table)
    chips = [_dashboard_filter_chip_html("all", "全部", len(table))]
    chips.extend(
        _dashboard_filter_chip_html(lane_key, title, len(rows), color)
        for lane_key, title, _subtitle, rows, color in summary_groups
    )
    return "".join(chips)


def _dashboard_filter_chip_html(lane_key: str, label: str, count: int, color: str = "gray") -> str:
    active_lane = str(st.session_state.get(LANE_FILTER_SESSION_KEY) or "")
    active_risk = str(st.session_state.get(RISK_RADAR_FILTER_SESSION_KEY) or "")
    is_active = (lane_key == "all" and not active_lane and not active_risk) or (lane_key != "all" and active_lane == lane_key)
    active = " active" if is_active else ""
    href = "?page=dashboard&laneFilter=all#watchlist-table" if lane_key == "all" else f"?page=dashboard&laneFilter={escape(lane_key, quote=True)}#watchlist-table"
    return (
        f'<a class="dashboard-filter-chip tone-{escape(color)}{active}" href="{href}" target="_self" '
        f'title="筛选：{escape(label)}">'
        f'<span>{escape(label)}</span><strong>{escape(str(count))}</strong>'
        "</a>"
    )


def _render_decision_table(table: pd.DataFrame) -> None:
    density = st.session_state.get("dashboard_density", "紧凑")
    table_class = "decision-table compact" if density == "紧凑" else "decision-table comfortable"
    st.markdown(
        '<section class="watchlist-head">'
        "<div><strong>观察名单</strong><span>决策摘要</span></div>"
        f'<div class="dashboard-filter-chips watchlist-filter-chips">{_dashboard_filter_chips_html(table)}</div>'
        "</section>",
        unsafe_allow_html=True,
    )
    st.markdown('<div id="watchlist-table"></div>', unsafe_allow_html=True)
    table = _filtered_table_for_active_lane(table)
    _render_active_lane_filter_status(table)
    header_html = "".join(_header_cell_html(definition["label"], definition.get("align")) for definition in WATCHLIST_COLUMNS)
    rows_html = "".join(
        _decision_table_row_html(row, WATCHLIST_COLUMNS, _dashboard_view_action_html, _badge_color_for_cell)
        for _, row in table.iterrows()
    )
    if not rows_html:
        rows_html = '<div class="decision-empty">当前筛选下没有股票。</div>'
    st.markdown(
        f'<div class="{table_class}"><div class="decision-grid decision-grid-head">{header_html}</div>{rows_html}</div>',
        unsafe_allow_html=True,
    )


def _render_row_action_menu(row: pd.Series) -> None:
    symbol = str(row.get("symbol") or "").upper()
    with st.popover("⋯", use_container_width=True):
        st.markdown(_drawer_open_menu_html(symbol, "详情"), unsafe_allow_html=True)
        st.markdown(_drawer_open_menu_html(symbol, "买区 / 仓位", focus="position"), unsafe_allow_html=True)
        st.markdown(_drawer_open_menu_html(symbol, "交易计划", focus="position"), unsafe_allow_html=True)
        if st.button("刷新", key=f"dashboard-refresh-{symbol}", width="stretch"):
            st.session_state["dashboard_force_fmp_refresh_symbol"] = symbol
            st.rerun()


def _render_detail_metrics(row: pd.Series) -> None:
    detail_columns = st.columns(4)
    for column, (group_name, metrics) in zip(detail_columns, DETAIL_GROUPS):
        with column:
            st.markdown(f"**{group_name}**")
            for key, label in metrics:
                value = row.get(key, "N/A")
                if key == "fcfMargin" and value == "N/A":
                    continue
                st.markdown(_detail_metric_html(label, value), unsafe_allow_html=True)
                if key == "fcfMargin" and row.get("fcfMarginNote"):
                    st.caption(str(row.get("fcfMarginNote")))
    data_note = row.get("dataNote")
    if data_note:
        st.caption(str(data_note))


def _render_score_explanation(row: pd.Series) -> None:
    summary = row.get("humanReadableSummary")
    if not isinstance(summary, dict):
        summary = {}
    cards = st.columns(4)
    with cards[0]:
        st.markdown(
            _score_card_html(
                "数据可信度",
                _confidence_text(row),
                [
                    "数据来源：" + _data_source_summary(row),
                    "关键缺失：" + _translated_join(row.get("missingIndustryMetrics")),
                    "人工复核：" + _manual_review_text(row),
                    "仓位上限：" + str(row.get("maxSuggestedPosition") or "N/A"),
                ],
            ),
            unsafe_allow_html=True,
        )
    with cards[1]:
        st.markdown(
            _score_card_html(
                "公司质量解释",
                str(row.get("qualityRating") or "N/A"),
                [
                    "主要加分：" + _translated_join(row.get("keyPositiveDrivers"), limit=4),
                    "主要扣分：" + _translated_join(_quality_negative_items(row), limit=4),
                    "缺失影响：" + _translated_join(_quality_missing_items(row), limit=4),
                    str(summary.get("quality") or ""),
                ],
            ),
            unsafe_allow_html=True,
        )
    with cards[2]:
        entry_label, entry_grade, _entry_raw = _entry_rating_display_parts(row)
        entry_display = _entry_rating_chip_text(entry_label, entry_grade)
        st.markdown(
            _score_card_html(
                "估值/计划参考解释",
                entry_display or str(row.get("entryRating") or "N/A"),
                [
                    "该区域来自 legacy 估值参考，不等同于主表 Radar 纪律买区。",
                    "估值状态：" + str(row.get("valuationStatus") or "N/A"),
                    "回撤幅度：" + str(row.get("drawdownFromHigh") or "N/A"),
                    "技术状态：" + _technical_state_text(row),
                    "当前建议：" + str(row.get("action") or "N/A"),
                    str(summary.get("valuation") or ""),
                    str(summary.get("technical") or ""),
                    str(summary.get("entry") or ""),
                ],
            ),
            unsafe_allow_html=True,
        )
    with cards[3]:
        st.markdown(
            _score_card_html(
                "风险解释",
                str(row.get("riskRating") or "N/A"),
                [
                    "风险来源：" + _translated_join(_risk_items(row), limit=4),
                    "数据风险：" + _data_risk_text(row),
                    "估值风险：" + _valuation_risk_text(row),
                    "业务风险：" + _business_risk_text(row),
                    str(summary.get("risk") or ""),
                ],
            ),
            unsafe_allow_html=True,
        )
    _render_metric_resolution_groups(row)


def _handle_record_signal_query(table: pd.DataFrame) -> None:
    symbol = str(st.query_params.get("recordSignal", "")).strip().upper()
    if not symbol:
        return
    matches = table[table["symbol"].astype(str).str.upper() == symbol]
    if not matches.empty:
        row = matches.iloc[0]
        save_decision_snapshot_from_bundle(
            symbol,
            _signal_price_from_dashboard_row(row),
            _decision_bundle_from_row(row),
            "dashboard",
        )
        st.session_state["dashboard_record_signal_notice"] = "已记录系统信号。"
    else:
        st.session_state["dashboard_record_signal_notice"] = "未找到要记录的系统信号。"
    if "recordSignal" in st.query_params:
        st.query_params.pop("recordSignal")
    st.rerun()


def _handle_risk_radar_filter_query() -> None:
    key = str(st.query_params.get("riskFilter", "")).strip()
    if key in RISK_RADAR_FILTER_LABELS:
        st.session_state[RISK_RADAR_FILTER_SESSION_KEY] = key
        st.session_state.pop(LANE_FILTER_SESSION_KEY, None)
    if "riskFilter" in st.query_params:
        st.query_params.pop("riskFilter")
        st.rerun()


def _handle_lane_filter_query() -> None:
    key = str(st.query_params.get("laneFilter", "")).strip()
    if key == "all":
        st.session_state.pop(LANE_FILTER_SESSION_KEY, None)
        st.session_state.pop(RISK_RADAR_FILTER_SESSION_KEY, None)
    elif key in LANE_FILTER_LABELS:
        st.session_state[LANE_FILTER_SESSION_KEY] = key
        st.session_state.pop(RISK_RADAR_FILTER_SESSION_KEY, None)
    if "laneFilter" in st.query_params:
        st.query_params.pop("laneFilter")
        st.rerun()


def _render_record_signal_notice() -> None:
    message = st.session_state.pop("dashboard_record_signal_notice", "")
    if message == "已记录系统信号。":
        st.success(message)
    elif message:
        st.warning(message)


def _signal_price_from_dashboard_row(row: pd.Series) -> float | None:
    technicals = row.get("rawTechnicals")
    snapshot = row.get("rawSnapshot")
    technicals = technicals if isinstance(technicals, dict) else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    return _first_present(technicals.get("price"), snapshot.get("current_price"), snapshot.get("price"))


def _decision_bundle_from_row(row: pd.Series) -> dict:
    return {
        "finalAction": row.get("finalAction"),
        "decisionLane": row.get("decisionLane"),
        "currentAddLimitPercent": row.get("currentAddLimitPercent"),
        "maxPortfolioWeightPercent": row.get("maxPortfolioWeightPercent"),
        "dataConfidence": row.get("dataConfidence"),
        "displayCategory": row.get("displayCategory"),
        "blockReasons": row.get("decisionBlockReasons") or [],
        "reviewReasons": row.get("decisionReviewReasons") or [],
    }


def _action_recommendation(score, data_quality: dict, anti_fomo: str, high_risk_flags: int, left_side_opportunity: str) -> str:
    if hasattr(score, "action") and score.action:
        return score.action
    if data_quality["pct"] < 40:
        return "数据不足，需复核"
    if anti_fomo:
        return "禁止追高"
    if high_risk_flags > 0:
        return "剔除" if score.total_score < 50 else "财报后复核"

    if score.scoring_model == "power_company":
        if score.value_zone in {"回撤后有吸引力", "合理偏便宜"}:
            return "可小仓分批"
        if score.value_zone == "偏贵":
            return "等回踩"
        return "只观察"

    if score.value_zone == "买入区" or left_side_opportunity:
        return "可小仓分批"
    if score.value_zone in {"合理估值区", "高估区"}:
        return "等回踩"
    return "只观察"


def _entry_rating(score, data_quality: dict, anti_fomo: str) -> str:
    if hasattr(score, "entry_rating") and score.entry_rating:
        return score.entry_rating
    if data_quality["pct"] < 40:
        return "数据不足"
    if anti_fomo:
        return "D - 禁止追高"
    if score.scoring_model == "power_company":
        if score.value_zone == "回撤后有吸引力":
            return "B+ - 回撤买点"
        if score.value_zone == "合理偏便宜":
            return "B - 可等回踩"
        if score.value_zone == "偏贵":
            return "D - 偏贵"
        return "C - 观察"
    if score.value_zone == "买入区":
        return "A - 回撤买点"
    if score.value_zone == "合理估值区":
        return "B - 等回踩"
    if score.value_zone == "高估区":
        return "D - 偏贵"
    return "C - 观察"


def _risk_rating(risk_flags, high_flags: int, medium_flags: int, data_quality: dict) -> str:
    if isinstance(risk_flags, str):
        return risk_flags
    if data_quality["pct"] < 40:
        return "数据不足"
    if high_flags > 0:
        return "高"
    if any(flag.label == "杠杆中高" for flag in risk_flags):
        return "中高"
    if medium_flags >= 3:
        return "中高"
    if medium_flags > 0:
        return "中"
    return "低"


def _valuation_status(value_zone: str, data_quality: dict) -> str:
    if value_zone in {"回撤后有吸引力", "回撤买点", "击球区附近", "合理偏便宜", "极贵", "偏贵", "只观察", "禁止追高", "数据不足"}:
        return value_zone
    if data_quality["pct"] < 40:
        return "数据不足"
    if value_zone == "回撤后有吸引力":
        return "回撤后有吸引力"
    if value_zone == "合理偏便宜":
        return "合理偏便宜"
    if value_zone == "买入区":
        return "回撤买点"
    if value_zone == "合理估值区":
        return "击球区附近"
    if value_zone == "高估区":
        return "偏贵"
    if value_zone == "偏贵":
        return "偏贵"
    return "只观察"


def _rating_from_score(score: float, label: str) -> str:
    if score >= 85:
        return f"A+ - 高{label}"
    if score >= 75:
        return f"A - 高{label}"
    if score >= 65:
        return "B+ - 稳健"
    if score >= 55:
        return "B - 稳健"
    if score >= 40:
        return "C - 可观察"
    return "D - 偏弱"


def _format_billions(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value / 1_000_000_000:,.1f}B"


def _format_plain_number(value: float | None, digits: int = 1) -> str:
    if _is_missing(value):
        return "N/A"
    return f"{float(value):,.{digits}f}"


def _average_percent_column(table: pd.DataFrame, key: str) -> str:
    values: list[float] = []
    for value in table.get(key, []):
        try:
            values.append(float(str(value).replace("%", "").replace(",", "")))
        except (TypeError, ValueError):
            continue
    if not values:
        return "N/A"
    return f"{sum(values) / len(values):.1f}%"


def _action_with_position(row: pd.Series) -> str:
    action = _row_final_action(row) or "N/A"
    value = _row_value(row, "currentAddLimitPercent")
    if value is None:
        value = _row_value(row, "maxSuggestedPositionPercent")
    try:
        max_position = float(value)
    except (TypeError, ValueError):
        return action
    if max_position <= 0:
        return f"{action} · 不新增"
    return f"{action} · ≤{max_position:g}%"


def _data_status_label(score) -> str:
    if getattr(score, "data_insufficient", False):
        return "数据不足"
    confidence = getattr(score, "data_confidence", None)
    if confidence == "high":
        return "完整"
    if confidence == "medium":
        return "中等"
    if confidence == "low":
        return "低置信度"
    return "待复核"


FACTOR_LABELS = {
    "revenue growth": "收入增速",
    "gross margin": "毛利率",
    "subscription revenue growth": "订阅收入增速",
    "non-GAAP operating margin": "Non-GAAP经营利润率",
    "calculated FCF Margin": "FCF利润率",
    "FCF Margin reported/calculated": "财报口径FCF利润率",
    "below EMA200": "股价低于EMA200",
    "drawdown > 40%": "距高点回撤超过40%",
    "above / below EMA200": "均线趋势状态",
    "Confidence Only": "仅影响置信度",
    "market-derived": "基于市场数据估算",
    "net retention rate": "净留存率",
    "PEG": "PEG",
    "forward revenue multiple": "远期收入倍数",
    "net debt / EBITDA": "净债务 / EBITDA",
    "RPO / cRPO growth": "RPO / cRPO增速",
    "SBC / revenue": "SBC / 收入",
    "P/S": "市销率",
    "EV/Sales": "EV/销售额",
    "P/FCF": "P/FCF",
    "EV/FCF": "EV/FCF",
    "FCF Yield": "FCF收益率",
    "ROIC": "ROIC",
    "GAAP operating margin": "GAAP经营利润率",
    "large customer growth": "大客户增长",
    "cash and equivalents": "现金及等价物",
    "current ratio": "流动比率",
    "debt maturity pressure": "债务到期压力",
    "interest coverage": "利息覆盖倍数",
    "valuation risk": "估值风险",
    "growth deceleration risk": "增长放缓风险",
    "dilution risk": "稀释风险",
    "GAAP profitability weakness": "GAAP盈利能力偏弱",
    "acquisition integration risk": "并购整合风险",
    "AI disruption / seat compression risk": "AI替代 / 席位压缩风险",
    "customer concentration": "客户集中度",
    "negative FCF": "自由现金流为负",
    "high leverage": "杠杆偏高",
    "52-week drawdown": "距高点回撤",
    "distance from 52-week low": "距52周低点",
    "volume trend": "成交量趋势",
    "trend confirmation": "趋势确认",
    "technical cooling": "技术冷却",
    "FCF Margin is market-derived and excluded from quality score": "FCF利润率为市场数据推导值，暂不参与公司质量评分。",
    "Operating Margin": "经营利润率",
    "Revenue Growth": "收入增速",
    "Balance Sheet": "资产负债表",
    "Revenue diversification": "收入来源多元化",
    "Product moat / ecosystem": "产品护城河 / 生态",
    "Generation asset quality": "发电资产质量",
}


def _score_card_html(title: str, headline: str, lines: list[str]) -> str:
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if line)
    return (
        '<div class="score-card">'
        f'<div class="score-card-title">{escape(str(title))}</div>'
        f'<div class="score-card-headline">{escape(str(headline))}</div>'
        f"<ul>{items}</ul>"
        "</div>"
    )


def _render_metric_resolution_groups(row: pd.Series) -> None:
    groups = _metric_resolution_groups(row.get("metricResolutionStatus"))
    if not groups:
        return
    st.markdown("##### 数据补全状态")
    cards = []
    for title, items in groups.items():
        if not items:
            continue
        body = "".join(_drawer_actionable_resolution_row(item) for item in items[:6])
        cards.append(
            '<div class="resolution-card">'
            f'<div class="score-card-title">{escape(title)}</div>'
            f"<ul>{body}</ul>"
            "</div>"
        )
    st.markdown('<div class="resolution-grid">' + "".join(cards) + "</div>", unsafe_allow_html=True)


def _metric_resolution_groups(raw_rows) -> dict[str, list[dict[str, object]]]:
    if not isinstance(raw_rows, (list, tuple)):
        return {}
    rows = [item for item in raw_rows if isinstance(item, dict) and str(item.get("resolutionStatus") or "") != "not_applicable"]
    rows = [item for item in rows if _safe_metric_label(item)]
    grouped: dict[str, list[dict[str, object]]] = {
        "关键待补齐": [],
        "可自动补齐": [],
        "已计算摘要": [],
        "低优先级 / 仅解释项": [],
    }
    used_ids: set[int] = set()
    for item in rows:
        if _is_key_resolution_gap(item):
            grouped["关键待补齐"].append(item)
            used_ids.add(id(item))
    for item in _important_calculated_items(rows):
        if id(item) in used_ids:
            continue
        grouped["已计算摘要"].append(item)
        used_ids.add(id(item))
    for item in rows:
        if id(item) in used_ids:
            continue
        if _is_auto_fill_resolution(item):
            grouped["可自动补齐"].append(item)
            used_ids.add(id(item))
    for item in rows:
        if id(item) not in used_ids and _resolution_priority(item) in {"medium", "low"}:
            grouped["低优先级 / 仅解释项"].append(item)
    return {title: items for title, items in grouped.items() if items}


def _is_key_resolution_gap(item: dict[str, object]) -> bool:
    status = str(item.get("resolutionStatus") or "")
    metric_type = str(item.get("metricType") or "")
    if metric_type == "CALCULATED_METRIC":
        return False
    return (
        _resolution_priority(item) == "high"
        and bool(_affects_decision_score(item))
        and status in {
            "requires_ir_scrape",
            "requires_sec_filing",
            "requires_analyst_estimates",
            "company_not_disclosed",
            "manual_override_required",
            "missing",
        }
    )


def _is_auto_fill_resolution(item: dict[str, object]) -> bool:
    status = str(item.get("resolutionStatus") or "")
    return status in {"missing_inputs", "requires_ir_scrape", "requires_sec_filing"}


def _important_calculated_items(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    order = [
        "fcfMargin",
        "netDebtToEbitda",
        "rsi14",
        "ema200",
        "drawdownFrom52WeekHigh",
        "sbcToRevenue",
        "interestCoverage",
        "volumeTrend",
    ]
    lookup: dict[str, dict[str, object]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        key = str(item.get("metricKey") or "")
        display = str(item.get("displayName") or "").lower()
        if not key:
            if "fcf margin" in display:
                key = "fcfMargin"
            elif "net debt" in display:
                key = "netDebtToEbitda"
            elif display == "rsi14":
                key = "rsi14"
            elif display == "ema200":
                key = "ema200"
            elif "drawdown" in display:
                key = "drawdownFrom52WeekHigh"
        status = str(item.get("resolutionStatus") or "")
        if key == "fcfMargin" and status in {"calculated", "available", "derived_score"}:
            lookup[key] = item
        elif status in {"available", "calculated"} and key in order:
            lookup[key] = item
    return [lookup[key] for key in order if key in lookup]


def _resolution_priority(item: dict[str, object]) -> str:
    priority = str(item.get("priority") or "").lower()
    if priority in {"high", "medium", "low"}:
        return priority
    status = str(item.get("resolutionStatus") or "")
    affects = _affects_decision_score(item)
    if status in {"requires_ir_scrape", "requires_sec_filing", "company_not_disclosed", "manual_override_required"} and affects:
        return "high"
    if status in {"missing_inputs", "derived_score", "semi_auto_low_confidence"}:
        return "medium"
    return "low"


def _affects_decision_score(item: dict[str, object]) -> list[str]:
    value = item.get("affects")
    if isinstance(value, (list, tuple, set)):
        parts = [str(part) for part in value if part]
    else:
        parts = [part.strip() for part in str(value or "").split(",") if part.strip()]
    return [part for part in parts if part in {"Quality", "Entry", "Risk"}]


def _safe_metric_label(item: dict[str, object]) -> str | None:
    label = metric_label(item.get("displayName") or item.get("metricKey") or "N/A")
    return None if label.startswith("未映射字段：") else label


def _drawer_actionable_resolution_row(item: dict[str, object]) -> str:
    label = _safe_metric_label(item)
    if not label:
        return ""
    affects = _affects_label_for_resolution(item)
    status = resolution_status_label(item.get("resolutionStatus"))
    action = _recommended_action_text(item)
    return (
        "<li>"
        f"<strong>{escape(label)}</strong>"
        f"<span>{escape(affects)}｜{escape(status)}</span>"
        f"<em>{escape(action)}</em>"
        "</li>"
    )


def _drawer_calculated_resolution_row(item: dict[str, object]) -> str:
    label = _safe_metric_label(item)
    if not label:
        return ""
    return (
        "<li>"
        f"<strong>{escape(label)}</strong>"
        f"<span>{escape(_resolution_value_text(item))}</span>"
        f"<em>{escape(_clean_resolution_explanation(str(item.get('explanation') or '')))}</em>"
        "</li>"
    )


def _drawer_low_priority_resolution_row(item: dict[str, object]) -> str:
    label = _safe_metric_label(item)
    if not label:
        return ""
    return (
        "<li>"
        f"<strong>{escape(label)}</strong>"
        f"<span>{escape(resolution_status_label(item.get('resolutionStatus')))}｜{escape(confidence_label(item.get('confidence')))}</span>"
        f"<em>{escape(_clean_resolution_explanation(str(item.get('explanation') or _recommended_action_text(item))))}</em>"
        "</li>"
    )


def _affects_label_for_resolution(item: dict[str, object]) -> str:
    mapping = {"Quality": "影响质量", "Entry": "影响买点", "Risk": "影响风险", "Technical": "影响技术面"}
    labels = [mapping.get(part, "") for part in _affects_decision_score(item)]
    if labels:
        return " / ".join(label for label in labels if label)
    affects = item.get("affects")
    if isinstance(affects, (list, tuple, set)) and "Technical" in affects:
        return "影响技术面"
    if isinstance(affects, str) and "Technical" in affects:
        return "影响技术面"
    return "影响解释"


def _recommended_action_text(item: dict[str, object]) -> str:
    text = str(item.get("recommendedAction") or "")
    key = str(item.get("metricKey") or "")
    status = str(item.get("resolutionStatus") or "")
    if key == "subscriptionRevenueGrowth":
        return "抓取IR财报新闻稿 / 8-K Exhibit 99.1"
    if key == "nonGaapOperatingMargin":
        return "抓取IR财报新闻稿"
    if key == "rpoGrowth":
        return "抓取IR / 8-K"
    if key == "netRetentionRate":
        return "公司可能未披露，建议人工复核"
    if key == "peg":
        return "需要分析师EPS增长预期"
    if key == "forwardRevenueMultiple":
        return "需要NTM收入预期"
    if key == "debtMaturityPressure" or "debt maturity" in str(item.get("displayName") or "").lower():
        return "检查10-K / 10-Q债务到期表"
    if status == "missing_inputs" and key in {"ema20", "ema50", "ema200", "rsi14", "drawdownFrom52WeekHigh", "return20d", "return60d", "volumeTrend"}:
        return "重新计算技术指标"
    return action_label(text or "复核")


def _clean_resolution_explanation(text: str) -> str:
    if not text:
        return ""
    replacements = {
        "FCF Margin is market-derived and excluded from quality score": "FCF利润率为市场数据推导值，暂不参与公司质量评分。",
        "market-derived": "市场数据推导",
        "quality score": "公司质量评分",
        " and excluded from ": "，暂不参与",
        " is ": "为",
    }
    cleaned = text
    for raw, label in replacements.items():
        cleaned = cleaned.replace(raw, label)
    return cleaned


def _resolution_value_text(item: dict[str, object]) -> str:
    value = item.get("value")
    status = str(item.get("resolutionStatus") or "missing")
    confidence = str(item.get("confidence") or "low")
    if value is None or _is_missing(value):
        value_text = _resolution_status_label(status)
    else:
        try:
            number = float(value)
            unit = str(item.get("unit") or "").lower()
            metric_key = str(item.get("metricKey") or "").lower()
            if unit in {"percent", "%"}:
                value_text = f"{number * 100:.1f}%" if abs(number) <= 1 else f"{number:.1f}%"
            elif abs(number) <= 1 and any(token in metric_key for token in ("margin", "growth", "yield", "ratio", "return", "drawdown", "volume")):
                value_text = f"{number * 100:.1f}%"
            else:
                value_text = f"{number:.2f}".rstrip("0").rstrip(".")
        except (TypeError, ValueError):
            value_text = str(value)
    return f"{value_text}｜状态 {resolution_status_label(status)}｜置信度 {confidence_label(confidence)}"


def _resolution_status_label(status: str) -> str:
    return resolution_status_label(status)


def _confidence_text(row: pd.Series) -> str:
    data = confidence_label(row.get("dataConfidence"))
    proxy = _proxy_confidence_display(row)
    return f"数据 {data} / 代理 {proxy}"


def _proxy_confidence_display(row: pd.Series) -> str:
    proxies = row.get("proxyMetricsUsed")
    if not isinstance(proxies, (list, tuple, set)) or not [item for item in proxies if item]:
        return "不适用"
    return confidence_label(row.get("proxyConfidence") or "不适用")


def _data_source_summary(row: pd.Series) -> str:
    sources = ["基础财务", "技术指标"]
    proxies = row.get("proxyMetricsUsed")
    if isinstance(proxies, (list, tuple, set)) and [item for item in proxies if item]:
        sources.append("代理指标")
    if row.get("missingMetricImpact"):
        sources.append("披露/自动补全状态")
    return "、".join(sources)


def _manual_review_text(row: pd.Series) -> str:
    review_summary = row.get("reviewQueueSummary")
    if isinstance(review_summary, dict) and review_summary.get("pending_review"):
        return f"需要，{int(review_summary.get('pending_review') or 0)} 条数据待确认"
    if isinstance(review_summary, dict) and review_summary.get("needs_data"):
        return f"需要，{int(review_summary.get('needs_data') or 0)} 条关键项待补齐"
    review_summary = row.get("disclosureReviewSummary")
    if isinstance(review_summary, dict) and review_summary.get("pending_review"):
        return f"需要，{int(review_summary.get('pending_review') or 0)} 条自动抽取数据待确认"
    critical = row.get("criticalPendingReviewMetrics")
    if isinstance(critical, (list, tuple, set)) and [item for item in critical if item]:
        return "需要，有关键数据待复核，评分置信度受限"
    if row.get("dataConfidence") == "low":
        return "需要，低置信度下先复核再建仓"
    if row.get("dataConfidence") == "medium" and row.get("modelType") == "SAAS_SOFTWARE":
        return "关键 SaaS 指标待复核，暂不提高仓位"
    missing = row.get("missingIndustryMetrics")
    if isinstance(missing, (list, tuple, set)) and [item for item in missing if item]:
        return "需要，行业关键字段未完全补齐"
    return "暂不需要"


def _quality_negative_items(row: pd.Series) -> list[str]:
    items = _list_value(row.get("keyNegativeDrivers"))
    return [item for item in items if item != "drawdown > 40%" and "鍥炴挙瓒呰繃40" not in item]


def _quality_missing_items(row: pd.Series) -> list[str]:
    impacts = row.get("missingMetricImpact")
    if not isinstance(impacts, (list, tuple)):
        return []
    resolution_rows = row.get("metricResolutionStatus")
    return [
        str(item.get("metric"))
        for item in impacts
        if isinstance(item, dict)
        and item.get("affects") in {"Quality", "Confidence Only"}
        and item.get("metric")
        and not _row_metric_resolved(str(item.get("metric")), resolution_rows)
    ]


def _row_metric_resolved(metric: str, resolution_rows) -> bool:
    if not isinstance(resolution_rows, (list, tuple)):
        return False
    lowered = metric.lower()
    resolved_statuses = {"available", "calculated", "derived", "not_applicable"}
    aliases = {
        "fcfMargin": ("fcf margin", "calculated fcf margin", "fcf margin reported/calculated"),
        "sbcToRevenue": ("sbc / revenue", "stock-based compensation", "sbc"),
        "netDebtToEbitda": ("net debt / ebitda", "net debt"),
        "interestCoverage": ("interest coverage",),
        "ema20": ("ema20",),
        "ema50": ("ema50",),
        "ema200": ("ema200", "below ema200", "above / below ema200"),
        "rsi14": ("rsi14", "rsi"),
        "drawdownFrom52WeekHigh": ("drawdown", "52-week drawdown"),
        "return20d": ("20d return", "20-day", "20日"),
        "return60d": ("60d return", "60-day", "60日"),
    }
    for item in resolution_rows:
        if not isinstance(item, dict):
            continue
        if str(item.get("resolutionStatus") or "") not in resolved_statuses:
            continue
        metric_key = str(item.get("metricKey") or "")
        display = str(item.get("displayName") or "").lower()
        if metric_key.lower() == lowered or display == lowered:
            return True
        if any(token in lowered for token in aliases.get(metric_key, ())):
            return True
    return False


def _risk_items(row: pd.Series) -> list[str]:
    active = _list_value(row.get("activeRiskDrivers"))
    passive = [
        item
        for item in _quality_negative_items(row)
        if metric_label(item) not in {"自由现金流为负", "稀释风险", "并购整合风险", "AI替代风险", "席位压缩风险", "AI替代 / 席位压缩风险"}
    ]
    return _dedupe_text([*active, *passive])


def _dedupe_text(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _technical_state_text(row: pd.Series) -> str:
    risks = " ".join(_quality_negative_items(row))
    if "低于EMA200" in risks or "EMA200" in risks:
        return "股价仍低于EMA200，趋势尚未完全修复"
    if _is_missing(row.get("ema200")):
        return "技术指标计算任务未完成"
    return "均线与动量指标已计算"


def _data_risk_text(row: pd.Series) -> str:
    if row.get("dataConfidence") == "low":
        return "数据置信度低，需复核后执行"
    if _quality_missing_items(row):
        return "部分关键经营指标缺失，降低评分置信度"
    return "未发现严重数据风险"


def _valuation_risk_text(row: pd.Series) -> str:
    status = str(row.get("valuationStatus") or "")
    if any(token in status for token in ("贵", "追高", "偏热")):
        return status
    return "估值未触发严重追高信号"


def _business_risk_text(row: pd.Series) -> str:
    items = _translated_list(_quality_negative_items(row), limit=3)
    return "。".join(items) if items else "暂无明确业务风险"


def _position_limit_text(value: float | None) -> str:
    if value is None or _is_missing(value):
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number <= 0:
        return "不建议新增"
    return f"≤{number:g}%"


def _portfolio_weight_text(value: float | None) -> str:
    if value is None or _is_missing(value):
        return "N/A"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if number <= 0:
        return "不建议配置"
    if number >= 15:
        return "15%-20%"
    if number >= 10:
        return "10%-15%"
    if number >= 5:
        return "5%-10%"
    return f"≤{number:g}%"


def _join_list(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        items = [str(item) for item in value if item]
        return "、".join(items) if items else "无"
    if value is None or value == "":
        return "无"
    return str(value)


def _translated_join(value: object, limit: int | None = None) -> str:
    items = _translated_list(_list_value(value), limit=limit)
    return "、".join(items) if items else "无"


def _translated_list(items: list[str], limit: int | None = None) -> list[str]:
    translated = [_translate_factor(str(item)) for item in items if item and str(item) != "drawdown > 40%"]
    translated = [item for item in translated if item]
    return translated[:limit] if limit else translated


def _list_value(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item]
    if value is None or value == "":
        return []
    return [str(value)]


def _translate_factor(value: str) -> str:
    text = value.strip()
    if text in FACTOR_LABELS:
        return FACTOR_LABELS[text]
    lower_map = {english.lower(): chinese for english, chinese in FACTOR_LABELS.items()}
    if text.lower() in lower_map:
        return lower_map[text.lower()]
    text = _clean_resolution_explanation(text)
    labeled = metric_label(text)
    if not labeled.startswith("未映射字段："):
        return labeled
    for english, chinese in FACTOR_LABELS.items():
        text = text.replace(english, chinese)
        text = text.replace(english.title(), chinese)
    return metric_label(text)


def _join_missing_impact(value: object) -> str:
    if not isinstance(value, (list, tuple)):
        return "无"
    items = []
    for row in value:
        if not isinstance(row, dict):
            continue
        metric = row.get("metric")
        affects = row.get("affects")
        level = row.get("impactLevel")
        if metric:
            items.append(f"{metric_label(metric)} ({metric_label(affects)}, {level})")
    return "、".join(items) if items else "无"


def _data_quality(snapshot: dict, scoring_model: str = "GENERIC") -> dict:
    if scoring_model in {"POWER_GENERATION", "power_company"}:
        fields = [
            ("调整后EBITDA", _first_present(snapshot.get("adjustedEbitda"), snapshot.get("manualAdjustedEbitda"), snapshot.get("ebitda"))),
            (
                "调整后FCF",
                _first_present(
                    snapshot.get("adjustedFcfBeforeGrowth"),
                    snapshot.get("manualAdjustedFcfBeforeGrowth"),
                    snapshot.get("free_cash_flow"),
                ),
            ),
            ("EV/调整后EBITDA", _first_present(snapshot.get("enterpriseValueToAdjustedEbitda"), snapshot.get("enterprise_to_ebitda"))),
            ("净债务/调整后EBITDA", snapshot.get("net_debt_to_ebitda")),
            ("市值", snapshot.get("market_cap")),
            ("流动比率", snapshot.get("current_ratio")),
        ]
        missing = [label for label, value in fields if _is_missing(value)]
        available = len(fields) - len(missing)
        return {"pct": available / len(fields) * 100, "missing": missing}

    fields = [
        ("TTM市盈率", "trailing_pe"),
        ("市销率", "price_to_sales"),
        ("EV/销售额", "enterprise_to_revenue"),
        ("P/FCF", "price_to_fcf"),
        ("FCF收益率", "free_cash_flow_yield"),
        ("收入增速", "revenue_growth"),
        ("经营利润率", "operating_margin"),
        ("ROIC", "return_on_invested_capital"),
        ("净债务/EBITDA", "net_debt_to_ebitda"),
        ("流动比率", "current_ratio"),
    ]
    missing = [label for label, key in fields if snapshot.get(key) is None]
    available = len(fields) - len(missing)
    pct = available / len(fields) * 100
    return {"pct": pct, "missing": missing}


def _data_note(snapshot: dict, data_quality: dict, score=None) -> str:
    notes = snapshot.get("data_quality_notes") or []
    messages = list(notes[:2])
    if snapshot.get("cache_note"):
        messages.append(str(snapshot["cache_note"]))
    if score is not None:
        messages.append(f"模型：{model_type_label(score.scoring_model)}")
        if getattr(score, "fcf_margin_source_type", "") == "derivedFromMarket":
            messages.append("FCF margin 为推导值，不参与质量评分")
        if getattr(score, "missing_industry_metrics", None):
            missing_labels = "、".join(metric_label(item) for item in score.missing_industry_metrics[:4])
            messages.append(f"代理置信度：{confidence_label(score.proxy_confidence)}；缺行业 KPI：" + missing_labels)
    missing = data_quality.get("missing") or []
    if missing:
        messages.append("缺失：" + "、".join(missing[:4]))
    return "；".join(messages)


def _looks_like_technical_error(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(hint in text for hint in TECHNICAL_ERROR_HINTS)


def _safe_table_value(key: str, value: object) -> object:
    if not _looks_like_technical_error(value):
        return value
    if key == "dataStatus":
        return "数据异常"
    if key == "action":
        return "数据不足，需复核"
    if key in {"valuationStatus", "qualityRating", "entryRating", "riskRating"}:
        return "数据不足"
    return "数据异常"


def _top_research_rows(table: pd.DataFrame) -> list[pd.Series]:
    candidates = [
        row
        for _, row in table.iterrows()
        if row.get("action") not in {"禁止追高", "财报后复核", "数据不足，需复核", "剔除"} and row.get("dataQualityPct", 0) >= 40
    ]
    return sorted(candidates, key=lambda row: row.get("totalScore", 0), reverse=True)[:4]


def _filtered_table_for_active_lane(table: pd.DataFrame) -> pd.DataFrame:
    risk_key = str(st.session_state.get(RISK_RADAR_FILTER_SESSION_KEY) or "")
    if risk_key in RISK_RADAR_FILTER_LABELS and "symbol" in table.columns:
        portfolio_view = build_portfolio_view_model()
        risk_item = next(
            (item for item in build_dashboard_risk_radar(table, portfolio_view) if item.get("key") == risk_key),
            None,
        )
        symbols = {str(symbol).upper() for symbol in (risk_item or {}).get("symbols", [])}
        if not symbols:
            return table.iloc[0:0].copy()
        return table[table["symbol"].astype(str).str.upper().isin(symbols)].copy()

    lane_key = str(st.session_state.get(LANE_FILTER_SESSION_KEY) or "")
    if lane_key not in LANE_FILTER_LABELS or "symbol" not in table.columns:
        return table
    symbols = {str(row.get("symbol") or "").upper() for row in _lane_filter_rows(table, lane_key)}
    if not symbols:
        return table.iloc[0:0].copy()
    return table[table["symbol"].astype(str).str.upper().isin(symbols)].copy()


def _render_active_lane_filter_status(filtered_table: pd.DataFrame) -> None:
    risk_key = str(st.session_state.get(RISK_RADAR_FILTER_SESSION_KEY) or "")
    risk_label = RISK_RADAR_FILTER_LABELS.get(risk_key)
    if risk_label:
        left, _spacer, clear = st.columns([0.18, 0.72, 0.10], gap="small", vertical_alignment="center")
        with left:
            st.markdown(
                f'<div class="table-filter-chip">当前筛选：<strong>{escape(risk_label)}</strong> · {len(filtered_table)}只</div>',
                unsafe_allow_html=True,
            )
        with clear:
            if st.button("清除", key="dashboard_clear_risk_filter", width="stretch", help="清除当前筛选"):
                st.session_state.pop(RISK_RADAR_FILTER_SESSION_KEY, None)
                st.rerun()
        return

    lane_key = str(st.session_state.get(LANE_FILTER_SESSION_KEY) or "")
    label = LANE_FILTER_LABELS.get(lane_key)
    if not label:
        return
    left, _spacer, clear = st.columns([0.18, 0.72, 0.10], gap="small", vertical_alignment="center")
    with left:
        st.markdown(
            f'<div class="table-filter-chip">当前筛选：<strong>{escape(label)}</strong> · {len(filtered_table)}只</div>',
            unsafe_allow_html=True,
        )
    with clear:
        if st.button("清除", key="dashboard_clear_lane_filter", width="stretch", help="清除当前筛选"):
            st.session_state.pop(LANE_FILTER_SESSION_KEY, None)
            st.rerun()


def _rows_matching_actions(table: pd.DataFrame, actions: set[str]) -> list[pd.Series]:
    return [
        row
        for _, row in table.iterrows()
        if row.get("action") in actions
    ][:4]


def _overheat_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if _numeric(row.get("overheatScore")) >= 40 or row.get("action") == "禁止追高"
    ]
    return sorted(rows, key=lambda row: _numeric(row.get("overheatScore")), reverse=True)[:8]


def _high_risk_or_data_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if row.get("action") in {"财报后复核", "数据不足，需复核", "剔除"}
        or row.get("highRiskFlagCount", 0) > 0
        or row.get("dataQualityPct", 100) < 60
    ]
    return rows[:4]


def _badge_color_for_cell(key: str, value: object, row: pd.Series | None = None) -> str:
    text = str(value)
    if key == "qualityRating":
        if text.startswith(("A",)):
            return "green"
        if text.startswith(("B",)):
            return "blue"
        if text.startswith("C"):
            return "yellow"
        if text.startswith("D"):
            return "red"
        return "gray"
    if key == "entryRating":
        return _buy_point_tone(text, row)
    if key == "riskRating":
        if text == "低":
            return "green"
        if text in {"中", "中等"}:
            return "yellow"
        if text == "中高":
            return "orange"
        if text in {"偏高", "高风险", "高"}:
            return "red"
        return "gray"
    if key == "dataStatus":
        if text == "完整":
            return "green"
        if text == "中等":
            return "blue"
        if text in {"待复核", "低置信度"}:
            return "yellow" if text == "待复核" else "orange"
        if text in {"数据不足", "数据异常", "使用本地缓存"}:
            return "gray"
        return "gray"
    if key == "maxSuggestedPosition":
        return "gray"
    if key == "action" and row is not None and row.get("dataConfidence") == "low" and "小仓" in text:
        return "yellow"
    if key == "valuationStatus":
        return _buy_point_tone(text, row)
    return _badge_color_for_action(text)


def _buy_point_tone(value: object, row: pd.Series | None = None) -> str:
    primary_texts = [str(value or "")]
    action_text = ""
    if row is not None:
        primary_texts.extend(
            [
                str(row.get("valuationStatus") or ""),
                str(row.get("entryRating") or ""),
            ]
        )
        action_text = str(row.get("action") or "")
    combined = " ".join(part for part in primary_texts if part)
    severe_text = f"{combined} {action_text}"
    if "极贵" in severe_text or "禁止追高" in severe_text or "高风险" in severe_text:
        return "deepred"
    if "偏贵" in combined or combined.startswith("D"):
        return "orange"
    if "击球区" in combined or "回撤买点" in combined or "合理偏便宜" in combined or combined.startswith("A"):
        return "green"
    if "等回踩" in combined or "接近" in combined or combined.startswith("B"):
        return "blue"
    if "未到估值买点" in combined or "只观察" in combined or "观察" in combined or "待复核" in combined or "需复核" in combined or combined.startswith("C"):
        return "yellow"
    if "数据" in combined:
        return "gray"
    return "gray"


def _badge_color_for_action(value: object) -> str:
    text = str(value)
    if "极贵" in text or "禁止追高" in text:
        return "deepred"
    if text in {"回撤买点", "回撤后有吸引力", "可小仓分批", "可正常分批"}:
        return "green"
    if text in {"观察", "只观察", "等回踩"}:
        return "blue"
    if text in {"击球区附近", "待复核后小仓", "可小仓观察，待关键数据复核后再加仓"}:
        return "yellow"
    if text in {"偏贵", "合理偏便宜"}:
        return "orange"
    if text in {"禁止追高", "高风险", "高", "剔除"}:
        return "red"
    if text in {"数据不足", "数据不足，需复核"}:
        return "gray"
    if text in {"可小仓观察", "正常评估"}:
        return "blue"
    return "gray"


def _numeric(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _market_stat_html(label: object, value: object, detail: object) -> str:
    return (
        '<div class="market-stat">'
        f'<div class="market-stat-label">{escape(str(label))}</div>'
        f'<div class="market-stat-value">{escape(str(value))}</div>'
        f'<div class="market-stat-detail">{escape(str(detail))}</div>'
        "</div>"
    )


def _dashboard_priority_strip_html(table: pd.DataFrame) -> str:
    rows = _today_priority_rows(table, limit=max(5, len(table)))
    top_rows = rows[:5]
    hidden_rows = rows[5:]
    if top_rows:
        body = "".join(_dashboard_priority_item_html(lane_key, row, color) for lane_key, row, color in top_rows)
    else:
        body = '<div class="dashboard-priority-empty">暂无明确可执行机会，优先等待回踩或复核数据。</div>'
    more_html = ""
    if hidden_rows:
        more_body = "".join(_dashboard_priority_item_html(lane_key, row, color) for lane_key, row, color in hidden_rows)
        more_html = (
            '<details class="dashboard-priority-more">'
            f'<summary>查看全部 {len(rows)} 条</summary>'
            f'<div class="dashboard-priority-more-list">{more_body}</div>'
            "</details>"
        )
    return (
        '<section class="dashboard-priority-strip">'
        '<div class="dashboard-priority-head"><strong>今日行动</strong><span>Top 5</span></div>'
        f'<div class="dashboard-priority-list">{body}</div>'
        f"{more_html}"
        "</section>"
    )


def _dashboard_priority_item_html(lane_key: str, row: pd.Series, color: str) -> str:
    return _dashboard_priority_item_html_base(lane_key, row, color, _lane_full_reason)


def _render_lane_more_button(lane_key: str) -> None:
    if st.button(_lane_more_label(), key=f"dashboard_lane_more_{lane_key}", width="stretch", help="筛选主表显示该分组"):
        st.session_state[LANE_FILTER_SESSION_KEY] = str(lane_key)


def _lane_more_label() -> str:
    return "查看全部"


def _lane_more_html(lane_key: str, hidden_count: int) -> str:
    return _lane_more_html_base(lane_key, hidden_count, LANE_FILTER_LABELS)


def _render_dashboard_styles() -> None:
    st.markdown(
        """
        <style>
        .market-stat {
            background: linear-gradient(180deg, rgba(255,255,255,0.96), rgba(248,250,252,0.92));
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 0.75rem;
            padding: 0.95rem 1rem;
            box-shadow: 0 16px 34px rgba(15, 23, 42, 0.06);
        }
        .market-stat-label {
            color: #667085;
            font-size: 0.76rem;
            font-weight: 680;
        }
        .market-stat-value {
            margin-top: 0.2rem;
            color: #172033;
            font-size: 1.55rem;
            line-height: 1.05;
            font-weight: 780;
        }
        .market-stat-detail {
            margin-top: 0.35rem;
            color: #98a2b3;
            font-size: 0.78rem;
        }
        .summary-panel-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 0.75rem;
            min-height: 4.4rem;
            background: rgba(255, 255, 255, 0.88);
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 0.75rem 0.75rem 0.35rem 0.35rem;
            padding: 0.85rem 0.9rem;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.05);
        }
        .summary-panel-title {
            color: #172033;
            font-size: 0.93rem;
            font-weight: 760;
        }
        .summary-panel-subtitle {
            margin-top: 0.22rem;
            color: #667085;
            font-size: 0.78rem;
            line-height: 1.35;
        }
        .summary-count {
            min-width: 24px;
            height: 24px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 760;
        }
        .summary-empty {
            min-height: 2.1rem;
            padding: 0.55rem 0.75rem;
            color: #98a2b3;
            font-size: 0.84rem;
            border: 1px dashed rgba(15, 23, 42, 0.12);
            border-top: 0;
            border-radius: 0 0 0.6rem 0.6rem;
            background: rgba(255, 255, 255, 0.55);
        }
        .decision-cell {
            min-height: 2.25rem;
            display: flex;
            align-items: center;
            padding: 0.35rem 0;
            font-size: 0.9rem;
            line-height: 1.25;
        }
        .decision-header {
            min-height: 1.8rem;
            display: flex;
            align-items: center;
            padding-bottom: 0.25rem;
            color: rgba(49, 51, 63, 0.72);
            font-size: 0.82rem;
            font-weight: 700;
            border-bottom: 1px solid rgba(49, 51, 63, 0.14);
        }
        .decision-badge {
            display: inline-flex;
            align-items: center;
            min-height: 1.75rem;
            max-width: 100%;
            padding: 0.18rem 0.55rem;
            border-radius: 999px;
            font-size: 0.82rem;
            font-weight: 650;
            line-height: 1.2;
            white-space: normal;
        }
        .summary-badge {
            display: inline-flex;
            width: 100%;
            justify-content: space-between;
            gap: 0.45rem;
            margin: 0;
            padding: 0.3rem 0.55rem;
            border-radius: 0;
            font-size: 0.85rem;
            font-weight: 650;
            line-height: 1.25;
        }
        .summary-badge:last-child {
            border-radius: 0 0 0.6rem 0.6rem;
        }
        .overheat-card {
            margin: 0;
            padding: 0.45rem 0.55rem;
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-top: 0;
            background: rgba(255, 255, 255, 0.70);
            font-size: 0.78rem;
            line-height: 1.35;
        }
        .overheat-card:last-child {
            border-radius: 0 0 0.6rem 0.6rem;
        }
        .overheat-top {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.5rem;
            color: #172033;
            font-size: 0.88rem;
        }
        .overheat-top span {
            min-width: 1.9rem;
            height: 1.45rem;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            font-size: 0.75rem;
            font-weight: 760;
        }
        .overheat-state {
            margin-top: 0.18rem;
            color: #475467;
        }
        .overheat-reason {
            margin-top: 0.12rem;
            color: #667085;
        }
        .overheat-action {
            margin-top: 0.18rem;
            color: #172033;
            font-weight: 650;
        }
        .score-card {
            min-height: 12rem;
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 0.7rem;
            padding: 0.85rem 0.9rem;
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.05);
        }
        .score-card-title {
            color: #667085;
            font-size: 0.76rem;
            font-weight: 760;
        }
        .score-card-headline {
            margin-top: 0.25rem;
            color: #172033;
            font-size: 0.98rem;
            font-weight: 780;
            line-height: 1.3;
        }
        .score-card ul {
            margin: 0.65rem 0 0 1rem;
            padding: 0;
            color: #475467;
            font-size: 0.8rem;
            line-height: 1.45;
        }
        .resolution-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 0.75rem;
            margin-top: 0.55rem;
        }
        .resolution-card {
            background: rgba(255, 255, 255, 0.78);
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 0.7rem;
            padding: 0.75rem 0.85rem;
        }
        .resolution-card ul {
            margin: 0.55rem 0 0 1rem;
            padding: 0;
            color: #475467;
            font-size: 0.78rem;
            line-height: 1.45;
        }
        .resolution-card span {
            color: #667085;
        }
        .resolution-card em {
            color: #1f4f8f;
            font-style: normal;
        }
        .detail-metric {
            display: flex;
            justify-content: space-between;
            gap: 0.55rem;
            padding: 0.26rem 0;
            border-bottom: 1px solid rgba(49, 51, 63, 0.08);
            font-size: 0.84rem;
            line-height: 1.25;
        }
        .detail-metric-label {
            color: rgba(49, 51, 63, 0.66);
        }
        .detail-metric-value {
            color: rgba(49, 51, 63, 0.9);
            font-weight: 650;
            text-align: right;
        }
        :root {
            --dash-bg: #F5F7FA;
            --dash-surface: #FFFFFF;
            --dash-surface-muted: #F8FAFC;
            --dash-border: rgba(15, 23, 42, 0.08);
            --dash-border-soft: rgba(15, 23, 42, 0.055);
            --dash-text: #0F172A;
            --dash-secondary: #52657F;
            --dash-muted: #94A3B8;
            --dash-accent: #2563EB;
            --dash-success: #15803D;
            --dash-success-bg: #F0FDF4;
            --dash-success-border: #BBF7D0;
            --dash-info: #1D4ED8;
            --dash-info-bg: #EFF6FF;
            --dash-info-border: #BFDBFE;
            --dash-warning: #A16207;
            --dash-warning-bg: #FEFCE8;
            --dash-warning-border: #FEF08A;
            --dash-orange: #C2410C;
            --dash-orange-bg: #FFF7ED;
            --dash-orange-border: #FED7AA;
            --dash-danger: #B91C1C;
            --dash-danger-strong: #7F1D1D;
            --dash-danger-bg: #FEF2F2;
            --dash-danger-border: #FECACA;
            --dash-neutral: #475569;
            --dash-neutral-bg: #F8FAFC;
            --dash-neutral-border: #E2E8F0;
            --dash-radius: 8px;
            --dash-radius-sm: 7px;
            --dash-radius-md: 10px;
            --dash-shell-width: 1680px;
            --dash-sidebar-width: 224px;
            --dash-table-row-height: 48px;
            --dash-badge-height: 20px;
            --dash-shadow: 0 10px 24px rgba(15, 23, 42, 0.035);
        }
        .terminal-header,
        .terminal-title-group {
            max-width: var(--dash-shell-width);
            margin: 0 0 0.42rem;
            padding: 0.46rem 0 0.42rem;
        }
        .terminal-header {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 1.25rem;
            border-bottom: 1px solid var(--dash-border);
        }
        .terminal-title-group {
            margin-bottom: 0.15rem;
            padding-bottom: 0;
        }
        .terminal-divider {
            height: 1px;
            margin: 0.28rem 0 0.46rem;
            background: var(--dash-border);
        }
        .terminal-kicker {
            color: #1D4ED8;
            font-size: 0.72rem;
            font-weight: 760;
            letter-spacing: 0;
            text-transform: uppercase;
        }
        .terminal-title {
            margin-top: 0.1rem;
            color: var(--dash-text);
            font-size: 1.46rem;
            line-height: 1.1;
            font-weight: 780;
        }
        .terminal-subtitle {
            margin-top: 0.22rem;
            color: var(--dash-secondary);
            font-size: 0.88rem;
        }
        .terminal-meta {
            display: flex;
            flex-wrap: wrap;
            justify-content: flex-end;
            gap: 0.4rem;
            color: var(--dash-secondary);
            font-size: 0.75rem;
            margin-bottom: 0.32rem;
        }
        .terminal-meta span {
            display: inline-flex;
            height: 28px;
            align-items: center;
            padding: 0 0.62rem;
            border: 1px solid var(--dash-border);
            border-radius: 999px;
            background: rgba(255,255,255,0.72);
            color: var(--dash-secondary);
            box-shadow: none;
            pointer-events: none;
            font-size: 12px;
            line-height: 1;
            font-weight: 650;
            font-variant-numeric: tabular-nums;
        }
        .dashboard-command-center {
            max-width:var(--dash-shell-width);
            margin:0 0 0.36rem;
            padding:0.28rem 0.52rem;
            border:1px solid var(--dash-border);
            border-radius:var(--dash-radius-md);
            background:rgba(255,255,255,0.94);
            color:var(--dash-secondary);
            box-shadow:none;
            position:relative;
            z-index:8;
        }
        .dashboard-command-line {
            display:flex;
            align-items:center;
            justify-content:flex-start;
            gap:0.46rem;
            min-height:40px;
            width:100%;
            white-space:nowrap;
            list-style:none;
            cursor:pointer;
        }
        .dashboard-command-main {
            display:flex;
            flex-direction:column;
            align-items:stretch;
            justify-content:flex-start;
            gap:0.34rem;
            min-height:0;
        }
        .dashboard-command-primary {
            display:flex;
            align-items:center;
            flex-wrap:nowrap;
            gap:0.42rem;
            flex:0 0 auto;
        }
        .dashboard-macro-pill {
            display:inline-flex;
            align-items:center;
            gap:0.24rem;
            min-height:24px;
            padding:0.08rem 0.42rem;
            border-radius:999px;
            border:1px solid rgba(148, 163, 184, 0.20);
            background:#FFFFFF;
            color:var(--dash-text);
            box-shadow:none;
            white-space:nowrap;
        }
        .dashboard-macro-pill.sentiment {
            background:#FFFBF4;
            border-color:rgba(251, 146, 60, 0.24);
        }
        .dashboard-macro-pill.vix {
            background:#F8FAFC;
            border-color:rgba(96, 165, 250, 0.20);
        }
        .dashboard-macro-pill b {
            color:var(--dash-muted);
            font-size:12px;
            font-weight:720;
        }
        .dashboard-macro-pill strong {
            color:var(--dash-text);
            font-size:14.5px;
            line-height:1.15;
            font-weight:820;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-command-summary {
            display:inline-flex;
            align-items:center;
            flex-wrap:nowrap;
            gap:0;
            min-width:0;
            flex:0 0 auto;
            color:var(--dash-secondary);
        }
        .dashboard-command-summary-item {
            display:inline-flex;
            align-items:center;
            gap:0.24rem;
            color:var(--dash-secondary);
            font-size:12.5px;
            line-height:1;
            font-weight:650;
            white-space:nowrap;
            padding:0 0.42rem;
            border-right:1px solid var(--dash-border-soft);
        }
        .dashboard-command-summary-item:first-child {
            padding-left:0;
        }
        .dashboard-command-summary-item:last-child {
            border-right:0;
        }
        .dashboard-command-summary-item b {
            color:var(--dash-muted);
            font-weight:650;
        }
        .dashboard-command-summary-item strong {
            color:var(--dash-secondary);
            font-size:13.5px;
            font-weight:780;
        }
        .dashboard-command-summary-item.ok strong { color:var(--dash-success); }
        .dashboard-command-summary-item.warn strong { color:var(--dash-warning); }
        .dashboard-command-summary-item.danger strong { color:var(--dash-danger); }
        .dashboard-command-summary-item.muted strong { color:var(--dash-muted); }
        .dashboard-command-summary-item.neutral strong { color:var(--dash-secondary); }
        .dashboard-command-primary + .dashboard-command-summary { margin-top:0; }
        .dashboard-command-line > * + *::before {
            content:"·";
            color:#CBD5E1;
            margin-right:0.46rem;
            font-weight:700;
        }
        .dashboard-command-line > .dashboard-command-summary::before {
            content:"";
            margin-right:0;
        }
        .dashboard-command-items {
            display:flex;
            align-items:center;
            flex-wrap:wrap;
            gap:0;
            min-width:0;
        }
        .dashboard-command-item {
            display:inline-flex;
            align-items:center;
            gap:0.2rem;
            min-width:0;
            color:var(--dash-secondary);
            font-size:11px;
            line-height:1;
            font-weight:650;
            white-space:nowrap;
            padding:0 0.46rem;
            border-right:1px solid var(--dash-border-soft);
        }
        .dashboard-command-item:first-child {
            padding-left:0;
        }
        .dashboard-command-item:last-child {
            border-right:0;
        }
        .dashboard-command-item strong {
            color:var(--dash-text);
            font-size:11.6px;
            font-weight:820;
        }
        .dashboard-command-item b {
            color:var(--dash-muted);
            font-weight:650;
        }
        .dashboard-command-item.ok { color:var(--dash-success); }
        .dashboard-command-item.warn { color:var(--dash-warning); }
        .dashboard-command-item.danger { color:var(--dash-danger); }
        .dashboard-command-item.muted { color:var(--dash-muted); }
        .dashboard-command-link {
            flex:0 0 auto;
            color:var(--dash-neutral) !important;
            font-size:11px;
            font-weight:720;
            text-decoration:none !important;
            white-space:nowrap;
        }
        .dashboard-command-link:hover,
        .dashboard-command-link:visited {
            color:var(--dash-text) !important;
            text-decoration:none !important;
        }
        .dashboard-command-details {
            margin-top:0;
            width:100%;
            color:var(--dash-secondary);
            position:relative;
        }
        .dashboard-command-details summary {
            cursor:pointer;
            display:flex;
            align-items:center;
            color:var(--dash-secondary);
            font-size:12px;
            font-weight:740;
            white-space:nowrap;
            list-style:none;
        }
        .dashboard-command-details summary::-webkit-details-marker {
            display:none;
        }
        .dashboard-command-details summary::marker {
            content:"";
        }
        .dashboard-command-updated {
            color:var(--dash-muted);
            margin-left:auto;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-command-trigger {
            display:inline-flex;
            align-items:center;
            min-height:24px;
            padding:0 0.48rem;
            border:1px solid transparent;
            border-radius:999px;
            background:transparent;
            color:var(--dash-neutral);
            font-size:12px;
            font-weight:760;
        }
        .dashboard-command-trigger:hover {
            border-color:var(--dash-border);
            background:#F8FAFC;
            color:var(--dash-text);
        }
        .dashboard-command-detail-panel {
            position:absolute;
            right:0;
            top:calc(100% + 0.46rem);
            width:min(720px, calc(100vw - 48px));
            display:grid;
            gap:0.5rem;
            padding:0.62rem;
            border:1px solid var(--dash-border);
            border-radius:12px;
            background:#FFFFFF;
            box-shadow:0 18px 42px rgba(15, 23, 42, 0.12);
            z-index:40;
        }
        .dashboard-command-detail-section {
            display:grid;
            gap:0.26rem;
        }
        .dashboard-command-detail-section h4 {
            margin:0;
            color:var(--dash-text);
            font-size:12px;
            font-weight:820;
        }
        .dashboard-command-detail-section table {
            width:100%;
            border-collapse:collapse;
            table-layout:fixed;
            font-size:11.2px;
        }
        .dashboard-command-detail-section th,
        .dashboard-command-detail-section td {
            padding:0.26rem 0.34rem;
            border-bottom:1px solid var(--dash-border-soft);
            color:var(--dash-secondary);
            text-align:left;
            vertical-align:top;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .dashboard-command-detail-section th {
            color:var(--dash-muted);
            font-size:10.5px;
            font-weight:720;
        }
        .dashboard-command-detail-section td:nth-child(1),
        .dashboard-command-detail-section td:nth-child(2) {
            color:var(--dash-text);
            font-weight:720;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-command-refresh-section ul,
        .dashboard-command-diagnostics ul {
            margin:0;
            padding:0;
            list-style:none;
            display:grid;
            gap:0.18rem;
        }
        .dashboard-command-refresh-section li,
        .dashboard-command-diagnostics li {
            display:grid;
            gap:0.04rem;
            min-width:0;
            color:var(--dash-secondary);
            font-size:11px;
        }
        .dashboard-command-refresh-section li b {
            color:var(--dash-text);
            font-size:11px;
        }
        .dashboard-command-refresh-section li span {
            color:var(--dash-secondary);
        }
        .dashboard-command-diagnostics {
            border-top:1px solid var(--dash-border-soft);
            padding-top:0.28rem;
        }
        .dashboard-command-diagnostics summary {
            width:auto;
            display:inline-flex;
            color:var(--dash-muted);
            font-size:11px;
            font-weight:720;
            min-height:0;
            cursor:pointer;
        }
        .dashboard-command-mini-stats {
            display:flex !important;
            align-items:center !important;
            justify-content:flex-start !important;
            flex-wrap:wrap;
            gap:0.28rem;
            margin-top:0.36rem;
        }
        .dashboard-command-mini-stats span {
            display:inline-flex;
            gap:0.16rem;
            padding:0.08rem 0.32rem;
            border:1px solid var(--dash-border);
            border-radius:999px;
            background:#FFFFFF;
            color:var(--dash-neutral);
            font-weight:680;
        }
        .dashboard-command-mini-stats b {
            color:var(--dash-muted);
            font-weight:650;
        }
        @media (max-width: 1180px) {
            .dashboard-command-line {
                flex-wrap:wrap;
                align-items:flex-start;
            }
            .dashboard-command-updated {
                margin-left:0;
            }
        }
        @media (max-width: 760px) {
            .dashboard-command-primary {
                flex-wrap:wrap;
            }
            .dashboard-command-details {
                width:100%;
            }
            .dashboard-command-detail-panel {
                position:static;
                width:100%;
                margin-top:0.42rem;
                box-shadow:0 10px 24px rgba(15, 23, 42, 0.08);
            }
        }
        .macro-regime-status {
            max-width:var(--dash-shell-width);
            margin:-0.1rem 0 0.42rem;
            padding:0.44rem 0.66rem;
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:9px;
            background:#FFFFFF;
            color:#334155;
            box-shadow:none;
            font-size:12px;
            line-height:1.35;
        }
        .macro-regime-status strong {
            font-weight:720;
        }
        .macro-regime-status.warning,
        .macro-regime-status.stress {
            border-color:rgba(217, 119, 6, 0.24);
            background:#FFFBEB;
            color:#7C4A1D;
        }
        .macro-regime-status.panic {
            border-color:rgba(185, 28, 28, 0.22);
            background:#FEF2F2;
            color:#7F1D1D;
        }
        .dashboard-freshness-strip {
            max-width:var(--dash-shell-width);
            margin:-0.1rem 0 0.44rem;
            padding:0.38rem 0.62rem;
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:9px;
            background:#FFFFFF;
            color:#334155;
            box-shadow:none;
            font-size:11px;
            line-height:1.35;
        }
        .dashboard-freshness-main {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.6rem;
        }
        .dashboard-freshness-main > strong {
            flex:0 0 auto;
            color:#64748B;
            font-size:11px;
            font-weight:720;
        }
        .dashboard-freshness-items {
            display:flex;
            flex-wrap:wrap;
            justify-content:flex-end;
            gap:0.34rem;
            min-width:0;
        }
        .dashboard-freshness-pill {
            display:inline-flex;
            align-items:center;
            gap:0.18rem;
            min-height:20px;
            padding:0 0.45rem;
            border:1px solid rgba(148, 163, 184, 0.22);
            border-radius:999px;
            background:#F8FAFC;
            color:#475467;
            font-weight:650;
            white-space:nowrap;
        }
        .dashboard-freshness-pill b {
            color:#94A3B8;
            font-weight:650;
        }
        .dashboard-freshness-pill.fresh {
            border-color:rgba(22, 163, 74, 0.20);
            background:#F0FDF4;
            color:#166534;
        }
        .dashboard-freshness-pill.warn {
            border-color:rgba(217, 119, 6, 0.22);
            background:#FFFBEB;
            color:#92400E;
        }
        .dashboard-freshness-pill.stale {
            border-color:rgba(185, 28, 28, 0.22);
            background:#FEF2F2;
            color:#991B1B;
        }
        .dashboard-freshness-pill.missing {
            border-color:rgba(148, 163, 184, 0.22);
            background:#F8FAFC;
            color:#64748B;
        }
        .dashboard-freshness-detail {
            margin-top:0.28rem;
            color:#64748B;
        }
        .dashboard-freshness-detail summary {
            cursor:pointer;
            color:#64748B;
            font-size:11px;
            font-weight:650;
        }
        .dashboard-freshness-detail-grid {
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:0.34rem 0.55rem;
            margin-top:0.35rem;
        }
        .dashboard-freshness-detail-grid div {
            display:flex;
            flex-direction:column;
            gap:0.06rem;
            padding:0.32rem 0.4rem;
            border:1px solid rgba(226,232,240,0.95);
            border-radius:8px;
            background:rgba(248,250,252,0.62);
        }
        .dashboard-freshness-detail-grid b {
            color:#334155;
            font-size:11px;
        }
        .dashboard-freshness-detail-grid span,
        .dashboard-freshness-detail-grid em,
        .dashboard-freshness-refresh {
            color:#64748B;
            font-style:normal;
            font-size:11px;
        }
        .dashboard-freshness-refresh {
            margin-top:0.32rem;
        }
        .portfolio-structure-strip {
            max-width:var(--dash-shell-width);
            margin:-0.1rem 0 0.44rem;
            padding:0.42rem 0.66rem;
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:9px;
            background:#FFFFFF;
            color:#334155;
            box-shadow:none;
            font-size:11px;
            line-height:1.35;
        }
        .portfolio-structure-strip.aggressive {
            border-color:rgba(217,119,6,0.20);
            background:#FFFBEB;
        }
        .portfolio-structure-strip.imbalanced {
            border-color:rgba(234,88,12,0.20);
            background:#FFF7ED;
        }
        .portfolio-structure-strip.danger {
            border-color:rgba(185,28,28,0.22);
            background:#FEF2F2;
        }
        .portfolio-structure-main {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.6rem;
        }
        .portfolio-structure-main > strong {
            flex:0 0 auto;
            color:#334155;
            font-size:11px;
            font-weight:760;
        }
        .portfolio-structure-items {
            display:flex;
            flex-wrap:wrap;
            justify-content:flex-end;
            gap:0.34rem;
            min-width:0;
        }
        .portfolio-structure-items span {
            display:inline-flex;
            align-items:center;
            gap:0.18rem;
            padding:0.12rem 0.36rem;
            border:1px solid rgba(226,232,240,0.95);
            border-radius:999px;
            background:rgba(255,255,255,0.72);
            color:#334155;
            font-size:11px;
            line-height:1.1;
            font-weight:650;
        }
        .portfolio-structure-items b {
            color:#64748B;
            font-weight:650;
        }
        .portfolio-structure-hint {
            margin-top:0.24rem;
            color:#64748B;
            overflow:hidden;
            white-space:nowrap;
            text-overflow:ellipsis;
        }
        .macro-regime-detail {
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:10px;
            padding:0.75rem;
            background:#FFFFFF;
            color:#334155;
            margin-bottom:0.75rem;
        }
        .macro-regime-detail > div:first-child {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.75rem;
            margin-bottom:0.55rem;
        }
        .macro-regime-detail span {
            color:#64748B;
            font-size:12px;
        }
        .macro-regime-sentiment-lines {
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:0.5rem;
            margin:0 0 0.6rem;
        }
        .macro-regime-sentiment-lines span {
            padding:0.38rem 0.5rem;
            border:1px solid rgba(226,232,240,0.92);
            border-radius:8px;
            background:#F8FAFC;
            color:#475569;
        }
        .macro-regime-detail table {
            width:100%;
            border-collapse:collapse;
            font-size:12px;
            margin-bottom:0.6rem;
        }
        .macro-regime-detail th,
        .macro-regime-detail td {
            border-bottom:1px solid rgba(226,232,240,0.92);
            padding:0.38rem 0.4rem;
            text-align:left;
        }
        .macro-regime-detail-grid {
            display:grid;
            grid-template-columns:1fr 1fr;
            gap:0.7rem;
            font-size:12px;
        }
        .macro-regime-detail-grid ul {
            margin:0.35rem 0 0;
            padding-left:1.05rem;
        }
        .macro-refresh-result {
            max-width:var(--dash-shell-width);
            margin:-0.08rem 0 0.5rem;
            padding:0.58rem 0.72rem;
            border:1px solid rgba(148, 163, 184, 0.2);
            border-radius:10px;
            background:#FFFFFF;
            color:#334155;
            font-size:12px;
            box-shadow:0 10px 22px rgba(15,23,42,0.035);
        }
        .macro-refresh-result.warning {
            border-color:rgba(217,119,6,0.25);
            background:#FFFBEB;
        }
        .macro-refresh-result.error {
            border-color:rgba(185,28,28,0.24);
            background:#FEF2F2;
        }
        .macro-refresh-grid {
            display:grid;
            grid-template-columns:repeat(2, minmax(0, 1fr));
            gap:0.42rem 0.7rem;
            margin-top:0.45rem;
        }
        .macro-refresh-group {
            margin-top:0.5rem;
        }
        .macro-refresh-group > b {
            color:#0F172A;
            font-size:12px;
        }
        .macro-refresh-group.muted {
            opacity:0.82;
        }
        .macro-refresh-grid div {
            display:flex;
            flex-direction:column;
            gap:0.12rem;
            padding:0.38rem 0.46rem;
            border:1px solid rgba(226,232,240,0.95);
            border-radius:8px;
            background:rgba(255,255,255,0.75);
        }
        .macro-refresh-grid span,
        .macro-refresh-grid em,
        .macro-refresh-error {
            color:#64748B;
            font-style:normal;
        }
        .macro-refresh-error {
            margin-top:0.45rem;
        }
        .macro-refresh-diagnostics {
            margin-top:0.18rem;
            color:#64748B;
            font-size:11px;
        }
        .macro-refresh-diagnostics summary {
            cursor:pointer;
            color:#475569;
            font-weight:650;
        }
        .macro-refresh-diagnostics code {
            display:block;
            margin-top:0.18rem;
            padding:0.32rem 0.4rem;
            border-radius:6px;
            background:rgba(15,23,42,0.04);
            color:#475569;
            white-space:normal;
            overflow-wrap:anywhere;
        }
        .dashboard-refresh-result {
            max-width:var(--dash-shell-width);
            margin:-0.08rem 0 0.5rem;
            padding:0.54rem 0.72rem;
            border:1px solid rgba(148, 163, 184, 0.2);
            border-radius:10px;
            background:#FFFFFF;
            color:#334155;
            font-size:12px;
            box-shadow:none;
        }
        .dashboard-refresh-result.warning {
            border-color:rgba(217,119,6,0.25);
            background:#FFFBEB;
        }
        .dashboard-refresh-result.error {
            border-color:rgba(185,28,28,0.24);
            background:#FEF2F2;
        }
        .dashboard-refresh-result > span {
            display:block;
            color:#64748B;
            margin-top:0.16rem;
        }
        .dashboard-refresh-result-grid {
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:0.32rem 0.5rem;
            margin-top:0.42rem;
        }
        .dashboard-refresh-result-grid div {
            display:flex;
            flex-direction:column;
            gap:0.08rem;
            padding:0.32rem 0.42rem;
            border:1px solid rgba(226,232,240,0.95);
            border-radius:8px;
            background:rgba(255,255,255,0.75);
        }
        .dashboard-refresh-result-grid span {
            color:#64748B;
        }
        .st-key-dashboard_refresh_price_only button,
        .st-key-dashboard_refresh_daily_technical button {
            min-height: 36px !important;
            height: 36px !important;
            border-radius: 10px !important;
            padding: 0 0.9rem !important;
            font-size: 0.84rem !important;
            font-weight: 720 !important;
            box-shadow: none !important;
        }
        .st-key-dashboard_refresh_price_only button {
            background: rgba(255,255,255,0.92) !important;
            color: var(--dash-text) !important;
            border: 1px solid var(--dash-border) !important;
        }
        .st-key-dashboard_refresh_daily_technical button {
            background: #1F2937 !important;
            color: #FFFFFF !important;
            border: 1px solid #1F2937 !important;
        }
        .st-key-dashboard_refresh_daily_technical button:hover {
            background: #111827 !important;
            border-color: #111827 !important;
            color: #FFFFFF !important;
        }
        .st-key-dashboard_density [data-baseweb="select"] > div {
            min-height: 36px;
            height: 36px;
            border-radius: 10px;
            border-color: var(--dash-border);
            background: rgba(255,255,255,0.70);
            box-shadow: none;
        }
        .st-key-dashboard_density [data-baseweb="select"] span,
        .st-key-dashboard_density [data-baseweb="select"] div {
            font-size: 12px;
            color: var(--dash-secondary);
            font-weight: 650;
        }
        .terminal-notice,
        .terminal-refresh-card,
        .terminal-loading-shell {
            max-width: var(--dash-shell-width);
            margin: 0.55rem 0 0.72rem;
            border: 1px solid var(--dash-border);
            border-radius: 0.7rem;
            background: rgba(255,255,255,0.84);
            box-shadow: none;
        }
        .terminal-notice {
            display: flex;
            align-items: center;
            gap: 0.65rem;
            padding: 0.72rem 0.86rem;
            color: var(--dash-secondary);
            font-size: 0.82rem;
        }
        .terminal-notice strong {
            display: block;
            color: var(--dash-text);
            font-size: 0.86rem;
            line-height: 1.2;
        }
        .terminal-notice span {
            display: block;
            margin-top: 0.12rem;
        }
        .terminal-notice-dot {
            width: 0.52rem;
            height: 0.52rem;
            border-radius: 999px;
            background: #1D4ED8;
            box-shadow: 0 0 0 4px #EFF6FF;
            flex: 0 0 auto;
        }
        .terminal-notice.tone-orange .terminal-notice-dot {
            background: #C2410C;
            box-shadow: 0 0 0 4px #FFF7ED;
        }
        .terminal-notice.tone-blue .terminal-notice-dot {
            background: #1D4ED8;
            box-shadow: 0 0 0 4px #EFF6FF;
        }
        .terminal-loading-shell {
            padding: 1rem;
        }
        .terminal-loading-head {
            display: flex;
            align-items: center;
            gap: 0.72rem;
        }
        .terminal-loading-head strong {
            color: var(--dash-text);
            font-size: 0.92rem;
        }
        .terminal-loading-head p {
            margin: 0.15rem 0 0;
            color: var(--dash-secondary);
            font-size: 0.78rem;
        }
        .terminal-loading-pulse {
            width: 0.72rem;
            height: 0.72rem;
            border-radius: 999px;
            background: #1D4ED8;
            box-shadow: 0 0 0 0 rgba(29, 78, 216, 0.28);
            animation: terminalPulse 1.4s ease-out infinite;
        }
        .terminal-skeleton-grid {
            display: grid;
            grid-template-columns: repeat(7, minmax(0, 1fr));
            gap: 0.55rem;
            margin-top: 0.95rem;
        }
        .terminal-skeleton-grid div {
            height: 2.6rem;
            border-radius: 0.45rem;
            background: linear-gradient(90deg, #F3F4F6 0%, #FFFFFF 45%, #F3F4F6 100%);
            background-size: 220% 100%;
            animation: terminalShimmer 1.6s linear infinite;
            border: 1px solid #E5E7EB;
        }
        .terminal-refresh-card {
            padding: 0.85rem 0.95rem;
        }
        .terminal-refresh-card.done {
            border-color: #BBF7D0;
            background: #F7FEFA;
        }
        .terminal-refresh-top {
            display: flex;
            justify-content: space-between;
            gap: 1rem;
            align-items: flex-start;
        }
        .terminal-refresh-kicker {
            color: var(--dash-secondary);
            font-size: 0.72rem;
            font-weight: 760;
        }
        .terminal-refresh-title {
            color: var(--dash-text);
            font-size: 1.05rem;
            font-weight: 800;
            letter-spacing: 0;
        }
        .terminal-refresh-detail,
        .terminal-refresh-count {
            color: var(--dash-secondary);
            font-size: 0.78rem;
            font-variant-numeric: tabular-nums;
        }
        .terminal-refresh-count {
            font-weight: 800;
        }
        .terminal-refresh-track {
            height: 0.42rem;
            margin-top: 0.72rem;
            border-radius: 999px;
            background: var(--dash-surface-muted);
            overflow: hidden;
        }
        .terminal-refresh-bar {
            height: 100%;
            border-radius: 999px;
            background: linear-gradient(90deg, #1D4ED8, #16A34A);
            transition: width 180ms ease;
        }
        @keyframes terminalPulse {
            0% { box-shadow: 0 0 0 0 rgba(29, 78, 216, 0.28); }
            100% { box-shadow: 0 0 0 12px rgba(29, 78, 216, 0); }
        }
        @keyframes terminalShimmer {
            0% { background-position: 220% 0; }
            100% { background-position: -220% 0; }
        }
        .market-stat {
            min-height: 66px;
            border-radius: 0.55rem;
            border-color: var(--dash-border);
            background: var(--dash-surface);
            box-shadow: none;
            padding: 0.58rem 0.72rem;
        }
        .market-stat-value {
            font-size: 1.14rem;
            font-variant-numeric: tabular-nums;
        }
        .market-stat-detail {
            margin-top: 0.18rem;
        }
        .decision-lanes-marker,
        .decision-lanes-end {
            display: none;
        }
        .zhx-section-title {
            margin: 0.55rem 0 0.28rem;
        }
        .zhx-section-title span:empty {
            display: none;
        }
        div[data-testid="stVerticalBlock"] > div:has(.decision-lanes-marker) + div [data-testid="stHorizontalBlock"] {
            gap: 0.55rem !important;
            margin: 0 0 0.28rem;
            padding: 0.55rem;
            border: 1px solid rgba(15, 23, 42, 0.05);
            border-radius: 0.65rem;
            background: rgba(255,255,255,0.50);
        }
        .summary-panel-head {
            min-height: 2.6rem;
            border-radius: 0.55rem 0.55rem 0 0;
            background: rgba(255,255,255,0.70);
            box-shadow: none;
            border-color: rgba(15, 23, 42, 0.05);
            padding: 0.44rem 0.58rem;
        }
        .summary-panel-title {
            font-size: 13px;
            font-weight: 650;
        }
        .summary-panel-subtitle {
            margin-top: 0.08rem;
            font-size: 11.5px;
            color: var(--dash-muted);
        }
        .summary-count {
            min-width: 22px;
            height: 22px;
            font-size: 11px;
            font-weight: 650;
        }
        .summary-empty,
        .lane-more {
            display: block;
            min-height: 1.65rem;
            padding: 0.26rem 0.56rem;
            color: var(--dash-muted);
            background: transparent;
            border: 0;
            border-top: 1px solid rgba(15, 23, 42, 0.04);
            font-size: 11.5px;
            font-weight: 500;
            text-decoration: none !important;
            cursor: pointer;
        }
        .lane-more:hover {
            color: var(--dash-text);
            background: rgba(248,250,252,0.60);
        }
        .summary-empty {
            min-height: 28px;
            display: flex;
            align-items: center;
        }
        .summary-empty.is-blank {
            cursor: default;
            pointer-events: none;
        }
        .lane-row-stack {
            height: 112px;
            min-height: 112px;
            overflow: hidden;
            border-bottom: 1px solid rgba(15, 23, 42, 0.035);
            box-sizing: border-box;
        }
        .lane-row-stack .summary-empty {
            height: 112px;
            min-height: 112px;
        }
        .st-key-dashboard_lane_more_actionable button,
        .st-key-dashboard_lane_more_nearBuyZone button,
        .st-key-dashboard_lane_more_waitOrReview button,
        .st-key-dashboard_lane_more_noChaseHighRisk button {
            min-height: 20px;
            height: 20px;
            padding: 0 0.5rem;
            color: #718198;
            background: transparent;
            border: 0;
            border-top: 1px solid rgba(15, 23, 42, 0.04);
            border-radius: 0 0 0.45rem 0.45rem;
            box-shadow: none;
            font-size: 10px;
            font-weight: 520;
            line-height: 20px;
            justify-content: flex-end;
            text-align: right;
            opacity: 0.72;
            font-variant-numeric: tabular-nums;
        }
        .st-key-dashboard_lane_more_actionable button p,
        .st-key-dashboard_lane_more_nearBuyZone button p,
        .st-key-dashboard_lane_more_waitOrReview button p,
        .st-key-dashboard_lane_more_noChaseHighRisk button p {
            width: auto;
            margin: 0;
            color: inherit;
            font-size: 10px;
            font-weight: 520;
            line-height: 20px;
            text-align: right;
            letter-spacing: 0;
        }
        .st-key-dashboard_lane_more_actionable button:hover,
        .st-key-dashboard_lane_more_nearBuyZone button:hover,
        .st-key-dashboard_lane_more_waitOrReview button:hover,
        .st-key-dashboard_lane_more_noChaseHighRisk button:hover {
            color: var(--dash-text);
            background: rgba(248,250,252,0.56);
            border-color: rgba(15, 23, 42, 0.04);
            opacity: 1;
        }
        .lane-item {
            --legacy-row-height: 32px;
            display: grid;
            grid-template-columns: 52px auto auto minmax(0, 1fr);
            align-items: center;
            gap: 6px;
            height: 28px;
            min-height: 28px;
            padding: 0 0.54rem;
            border: 0;
            border-top: 1px solid rgba(15, 23, 42, 0.04);
            background: transparent;
            font-size: 12px;
            color: inherit;
            text-decoration: none !important;
            overflow: hidden;
            cursor: pointer;
        }
        .lane-item:hover,
        .lane-item:focus,
        .lane-item:visited {
            color: inherit;
            text-decoration: none !important;
        }
        .lane-item:hover {
            background: rgba(248,250,252,0.72);
        }
        .lane-symbol {
            color: var(--dash-text);
            font-size: 12px;
            font-weight: 650;
            font-variant-numeric: tabular-nums;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            text-decoration: none !important;
        }
        .lane-item > .decision-badge {
            justify-self: start;
            max-width: 100%;
            flex-shrink: 0;
            white-space: nowrap;
        }
        .lane-reason {
            color: var(--dash-secondary);
            font-size: 12px;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            line-height: 1.35;
            padding-left: 0;
        }
        .table-filter-chip {
            display: inline-flex;
            align-items: center;
            min-height: 28px;
            height: 28px;
            margin-top: 0.34rem;
            margin-bottom: 0.34rem;
            padding: 0 10px;
            border: 1px solid rgba(191,219,254,0.86);
            border-radius: 999px;
            background: rgba(239,246,255,0.72);
            color: var(--dash-secondary);
            font-size: 12px;
            font-weight: 650;
            line-height: 26px;
        }
        .st-key-dashboard_clear_lane_filter,
        .st-key-dashboard_clear_risk_filter {
            margin-top: 0.34rem;
            margin-bottom: 0.34rem;
        }
        .st-key-dashboard_clear_lane_filter button,
        .st-key-dashboard_clear_risk_filter button {
            min-width: 54px !important;
            min-height: 28px !important;
            height: 28px !important;
            border-radius: 999px !important;
            padding: 0 8px !important;
            font-size: 11px !important;
            font-weight: 620 !important;
            line-height: 26px !important;
            white-space: nowrap !important;
            word-break: keep-all !important;
            box-shadow: none !important;
            background: rgba(254, 242, 242, 0.62) !important;
            border: 1px solid rgba(239, 68, 68, 0.20) !important;
            color: #A33A3A !important;
            opacity: 0.88;
        }
        .st-key-dashboard_clear_lane_filter button p,
        .st-key-dashboard_clear_risk_filter button p {
            font-size: 11px !important;
            font-weight: 620 !important;
            line-height: 26px !important;
            margin: 0;
            white-space: nowrap !important;
            word-break: keep-all !important;
        }
        .st-key-dashboard_clear_lane_filter button:hover,
        .st-key-dashboard_clear_risk_filter button:hover {
            opacity: 1;
            color: #8A1F1F !important;
            background: rgba(254, 226, 226, 0.78) !important;
            border-color: rgba(220, 38, 38, 0.30) !important;
        }
        .decision-table {
            margin-top: 0.15rem;
        }
        .decision-cell-link {
            display: block;
            color: inherit;
            text-decoration: none !important;
            border-radius: 6px;
        }
        .decision-cell-link:hover .decision-cell {
            background: #F8FAFC;
        }
        .decision-table [data-testid="stVerticalBlockBorderWrapper"] {
            border: 0;
            box-shadow: none;
            background: transparent;
        }
        .decision-header {
            min-height: 2rem;
            padding: 0.35rem 0.3rem;
            color: var(--dash-secondary);
            font-size: 0.74rem;
            font-weight: 650;
            background: rgba(247,248,250,0.94);
            border-bottom: 1px solid rgba(15, 23, 42, 0.05);
            position: sticky;
            top: 0;
            z-index: 2;
        }
        .decision-cell {
            min-height: 3.05rem;
            padding: 0.28rem 0.3rem;
            color: var(--dash-text);
            font-size: 12.5px;
            border-bottom: 1px solid rgba(15, 23, 42, 0.05);
            font-variant-numeric: tabular-nums;
        }
        .decision-table.compact .decision-cell {
            min-height: 2.65rem;
            font-size: 12.5px;
        }
        .align-right {
            justify-content: flex-end;
            text-align: right;
        }
        .decision-badge {
            min-height: 24px;
            padding: 0 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            line-height: 24px;
            white-space: nowrap;
        }
        div[data-testid="stPopover"] > button {
            min-height: 30px;
            height: 30px;
            padding: 0 8px;
            border-color: transparent;
            background: transparent;
            color: var(--dash-secondary);
            box-shadow: none;
        }
        div[data-testid="stPopover"] > button:hover {
            border-color: rgba(15, 23, 42, 0.08);
            background: #F8FAFC;
            color: var(--dash-text);
        }
        [class*="st-key-dashboard-detail-"] button,
        [class*="st-key-dashboard-position-"] button,
        [class*="st-key-dashboard-plan-"] button,
        [class*="st-key-dashboard-refresh-"] button {
            min-height: 30px !important;
            height: 30px !important;
            border-radius: 7px !important;
            border-color: transparent !important;
            background: transparent !important;
            box-shadow: none !important;
            color: var(--dash-text) !important;
            font-size: 12px !important;
            font-weight: 560 !important;
            justify-content: flex-start !important;
        }
        [class*="st-key-dashboard-detail-"] button:hover,
        [class*="st-key-dashboard-position-"] button:hover,
        [class*="st-key-dashboard-plan-"] button:hover,
        [class*="st-key-dashboard-refresh-"] button:hover {
            background: #F8FAFC !important;
            border-color: rgba(15, 23, 42, 0.06) !important;
        }
        .score-card,
        .resolution-card {
            box-shadow: none;
            border-color: var(--dash-border);
            border-radius: 0.55rem;
            background: var(--dash-surface);
        }
        .drawer-backdrop {
            position: fixed;
            inset: 0;
            background: transparent;
            z-index: 2147482998;
            pointer-events: auto;
        }
        .drawer-close-link {
            position: fixed;
            top: 14px;
            right: 16px;
            z-index: 2147483001;
            width: 38px;
            display: flex;
            align-items: center;
            justify-content: center;
            width: 38px;
            height: 38px;
            padding: 0;
            border-radius: 999px;
            border: 1px solid var(--dash-border);
            background: rgba(255,255,255,0.96);
            color: var(--dash-secondary);
            box-shadow: 0 12px 30px rgba(15,23,42,0.14);
            text-decoration: none;
            font-size: 1.25rem;
            line-height: 1;
            font-weight: 650;
        }
        .drawer-close-link:hover {
            border-color: #CBD5E1;
            background: var(--dash-surface);
            color: var(--dash-text);
        }
        .drawer-menu-link {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 100%;
            min-height: 34px;
            margin-bottom: 0.35rem;
            border-radius: 0.62rem;
            border: 1px solid var(--dash-border);
            background: var(--dash-surface);
            color: var(--dash-text);
            text-decoration: none;
            font-size: 0.86rem;
            font-weight: 700;
            font-family: inherit;
            cursor: pointer;
            appearance: none;
        }
        .drawer-menu-link:hover {
            background: var(--dash-surface-muted);
            border-color: #CBD5E1;
        }
        .stock-drawer {
            position: fixed;
            top: 0;
            right: 0;
            width: min(620px, 42vw);
            height: 100vh;
            overflow-y: auto;
            padding: 1.15rem;
            background: var(--dash-surface);
            border-left: 1px solid var(--dash-border);
            box-shadow: -14px 0 34px rgba(15,23,42,0.10);
            z-index: 2147482999;
            transform: translateX(0);
            opacity: 1;
            contain: paint;
            animation: dashboard-drawer-in 120ms ease-out;
        }
        @keyframes dashboard-drawer-in {
            from {
                transform: translateX(18px);
                opacity: 0.98;
            }
            to {
                transform: translateX(0);
                opacity: 1;
            }
        }
        .drawer-topline {
            color: var(--dash-muted);
            font-size: 0.72rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .drawer-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 1rem;
            margin-top: 0.5rem;
        }
        .drawer-symbol {
            color: var(--dash-text);
            font-size: 1.45rem;
            font-weight: 800;
        }
        .drawer-company,
        .drawer-price,
        .drawer-muted {
            color: var(--dash-secondary);
            font-size: 0.82rem;
        }
        .drawer-price {
            color: var(--dash-text);
            font-weight: 760;
            font-variant-numeric: tabular-nums;
        }
        .drawer-meta-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.45rem;
            margin: 0.85rem 0;
            color: var(--dash-secondary);
            font-size: 0.78rem;
        }
        .drawer-meta-grid span {
            padding: 0.45rem 0.55rem;
            border: 1px solid var(--dash-border);
            border-radius: 0.45rem;
            background: var(--dash-surface-muted);
        }
        .drawer-badges {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            margin-bottom: 0.85rem;
        }
        .drawer-signal-actions {
            display:flex;
            justify-content:flex-end;
            margin:-0.35rem 0 0.72rem;
        }
        .drawer-signal-actions a {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            height:26px;
            padding:0 0.56rem;
            border:1px solid rgba(15, 23, 42, 0.10);
            border-radius:4px;
            background:#FFFFFF;
            color:#52657F;
            font-size:12px;
            font-weight:700;
            text-decoration:none !important;
        }
        .drawer-signal-actions a:hover {
            background:#FFFFFF;
            border-color:rgba(15, 23, 42, 0.12);
            color:#0F172A;
        }
        .drawer-decision-card {
            margin: 0.64rem 0;
            padding: 0.74rem 0.78rem;
            border-radius: var(--dash-radius);
            border: 1px solid var(--dash-border);
            border-left: 3px solid var(--dash-accent);
            background: #FFFFFF;
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.035);
        }
        .drawer-decision-headline {
            margin-top: 0.2rem;
            color: var(--dash-text);
            font-size: 0.98rem;
            font-weight: 780;
            line-height: 1.45;
        }
        .drawer-decision-card p,
        .drawer-waiting li {
            color: var(--dash-secondary);
            font-size: 0.78rem;
            line-height: 1.5;
        }
        .drawer-decision-grid,
        .drawer-position-card {
            display: grid;
            gap: 0.46rem;
        }
        .drawer-decision-grid {
            grid-template-columns: repeat(3, minmax(0, 1fr));
        }
        .drawer-position-card {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .drawer-decision-grid span,
        .drawer-position-card > div {
            display: grid;
            gap: 0.18rem;
            padding: 0.5rem 0.54rem;
            border-radius: var(--dash-radius);
            border: 1px solid var(--dash-border);
            background: var(--dash-surface-muted);
        }
        .drawer-decision-grid b,
        .drawer-position-card span {
            color: var(--dash-secondary);
            font-size: 0.72rem;
            font-weight: 740;
        }
        .drawer-decision-grid strong,
        .drawer-position-card strong {
            color: var(--dash-text);
            font-size: 1rem;
            font-weight: 820;
            font-variant-numeric: tabular-nums;
        }
        .drawer-position-card {
            margin: 0.62rem 0;
        }
        .drawer-position-card > div {
            border-color: var(--dash-border);
            background: var(--dash-surface);
        }
        .drawer-position-card em {
            color: var(--dash-muted);
            font-size: 0.72rem;
            font-style: normal;
            line-height: 1.45;
        }
        .drawer-next-action-card {
            margin-top: 0.65rem;
        }
        .drawer-detail-basis {
            margin-top: 0.64rem;
        }
        .drawer-detail-basis > summary {
            cursor: pointer;
            color: var(--dash-secondary);
            font-size: 0.78rem;
            font-weight: 780;
        }
        .drawer-waiting {
            margin-top: 0.62rem;
        }
        .drawer-waiting ul {
            margin: 0.25rem 0 0 1rem;
            padding: 0;
        }
        .drawer-section {
            display: grid;
            gap: 0.56rem;
        }
        .drawer-card,
        .drawer-resolution,
        .drawer-review-summary,
        .drawer-industry-card,
        .drawer-raw {
            padding: 0.64rem 0.68rem;
            border: 1px solid var(--dash-border);
            border-radius: var(--dash-radius);
            background: var(--dash-surface);
            box-shadow: none;
        }
        .drawer-card-title,
        .drawer-section-title {
            color: var(--dash-secondary);
            font-size: 0.74rem;
            font-weight: 750;
        }
        .drawer-section-title {
            margin: 0.82rem 0 0.36rem;
        }
        .drawer-card-headline {
            margin-top: 0.2rem;
            color: var(--dash-text);
            font-weight: 760;
        }
        .drawer-card ul,
        .drawer-resolution ul,
        .drawer-review-summary ul,
        .drawer-industry-card ul,
        .drawer-metric-group ul {
            margin: 0.45rem 0 0 1rem;
            padding: 0;
            color: var(--dash-secondary);
            font-size: 0.78rem;
            line-height: 1.45;
        }
        .drawer-entry-zone-table {
            width: 100%;
            margin-top: 0.48rem;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 0.76rem;
            color: var(--dash-secondary);
            border: 1px solid var(--dash-border);
            border-radius: var(--dash-radius);
            overflow: hidden;
        }
        .drawer-entry-zone-table th,
        .drawer-entry-zone-table td {
            border-bottom: 1px solid var(--dash-border-soft);
            padding: 0.34rem 0.42rem;
            text-align: left;
            vertical-align: top;
        }
        .drawer-entry-zone-table th {
            color: var(--dash-muted);
            background: var(--dash-surface-muted);
            font-size: 0.68rem;
            font-weight: 760;
            text-transform: uppercase;
        }
        .drawer-entry-zone-table tr:last-child td {
            border-bottom: 0;
        }
        .drawer-entry-zone-table td:first-child {
            color: var(--dash-text);
            font-weight: 760;
            white-space: nowrap;
        }
        .drawer-resolution {
            margin-bottom: 0.5rem;
        }
        .drawer-resolution li,
        .drawer-review-summary li,
        .drawer-industry-card li,
        .drawer-metric-group li {
            display: flex;
            justify-content: space-between;
            gap: 0.8rem;
            border-bottom: 1px solid rgba(229,231,235,0.7);
            padding: 0.18rem 0;
        }
        .drawer-industry-card li {
            display: grid;
            grid-template-columns: 110px 92px minmax(0, 1fr);
            align-items: start;
            gap: 0.45rem;
            padding: 0.38rem 0;
        }
        .drawer-industry-card li span {
            color: var(--dash-text);
            font-weight: 740;
        }
        .drawer-industry-card li strong {
            color: var(--dash-text);
            font-weight: 760;
            font-variant-numeric: tabular-nums;
        }
        .drawer-industry-card li em {
            color: var(--dash-secondary);
            font-style: normal;
            min-width: 0;
        }
        .drawer-resolution li {
            flex-direction: column;
            gap: 0.15rem;
            padding: 0.42rem 0;
        }
        .drawer-resolution li strong {
            color: var(--dash-text);
            font-size: 0.8rem;
        }
        .drawer-resolution li span {
            color: var(--dash-secondary);
            font-size: 0.75rem;
        }
        .drawer-resolution li em {
            color: var(--dash-muted);
            font-size: 0.73rem;
            font-style: normal;
        }
        .drawer-resolution.priority-high {
            border-color: #FDE68A;
            background: #FFFEF5;
        }
        .drawer-low-priority summary {
            cursor: pointer;
            color: var(--dash-secondary);
            font-size: 0.78rem;
            font-weight: 760;
        }
        .drawer-review-summary p {
            margin: 0.6rem 0 0;
            color: #854D0E;
            font-size: 0.78rem;
            font-weight: 760;
        }
        .drawer-review-actions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 0.6rem;
        }
        .drawer-review-actions a,
        .drawer-review-actions button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 26px;
            height: 26px;
            padding: 0 0.56rem;
            border-radius: 4px;
            border: 1px solid var(--dash-border);
            background: var(--dash-surface);
            color: var(--dash-text);
            text-decoration: none;
            font-size: 12px;
            font-weight: 700;
            cursor: pointer;
            font-family: inherit;
        }
        .drawer-review-actions a.primary,
        .drawer-review-actions button.primary {
            background: var(--dash-text);
            border-color: var(--dash-text);
            color: white;
        }
        .drawer-action-note {
            flex-basis: 100%;
            border: 1px solid #BFDBFE;
            background: #EFF6FF;
            color: #1D4ED8;
            border-radius: 10px;
            padding: 0.55rem 0.65rem;
            font-size: 0.76rem;
            line-height: 1.45;
        }
        .drawer-section-pulse {
            animation: drawerSectionPulse 0.9s ease;
        }
        @keyframes drawerSectionPulse {
            0% { box-shadow: 0 0 0 0 rgba(37, 99, 235, 0.28); }
            100% { box-shadow: 0 0 0 12px rgba(37, 99, 235, 0); }
        }
        div[data-testid="stAppViewContainer"] {
            background:var(--dash-bg);
        }
        div.block-container {
            max-width: min(var(--dash-shell-width), calc(100vw - var(--dash-sidebar-width) - 32px));
            width: min(var(--dash-shell-width), calc(100vw - var(--dash-sidebar-width) - 32px));
            margin-left: calc(var(--dash-sidebar-width) + 12px) !important;
            margin-right: 20px !important;
            padding-left: 0.85rem;
            padding-right: 0.85rem;
        }
        @media (max-width: 980px) {
            div.block-container {
                width: 100%;
                max-width: 100%;
                margin-left: 0 !important;
                margin-right: 0 !important;
            }
        }
        .terminal-header,
        .terminal-title-group,
        .terminal-notice,
        .terminal-refresh-card,
        .terminal-loading-shell,
        .market-ribbon,
        .dashboard-discipline-strip,
        .data-health-strip,
        .dashboard-risk-summary-strip,
        .dashboard-risk-radar,
        .decision-terminal-head,
        .dashboard-priority-strip,
        .watchlist-head,
        .decision-table,
        .table-filter-chip {
            max-width: var(--dash-shell-width);
            margin-left: 0;
            margin-right: 0;
            box-sizing: border-box;
        }
        .terminal-header,
        .terminal-title-group {
            max-width: var(--dash-shell-width);
        }
        .terminal-header {
            margin-bottom: 0.28rem;
            border-bottom-color: rgba(15,23,42,0.08);
        }
        .terminal-title {
            font-size: 1.48rem;
            font-weight: 780;
        }
        .terminal-subtitle {
            color:#64748B;
            font-size:12.5px;
        }
        .terminal-kicker {
            color:#2563EB;
        }
        .terminal-meta span {
            height:25px;
            border-color:var(--dash-border);
            background:#FFFFFF;
            color:var(--dash-secondary);
            box-shadow:none;
        }
        .market-ribbon {
            display:grid;
            grid-template-columns:repeat(4, minmax(0, 1fr));
            gap:0;
            margin-top:0.48rem;
            margin-bottom:0.42rem;
            border:1px solid rgba(148, 163, 184, 0.20);
            border-radius:8px;
            background:#FFFFFF;
            overflow:hidden;
            box-shadow:none;
        }
        .market-ribbon .market-stat {
            min-height:54px;
            border:0;
            border-right:1px solid rgba(15, 23, 42, 0.035);
            border-radius:0;
            background:transparent;
            box-shadow:none;
            padding:0.44rem 0.74rem;
            display:grid;
            align-content:center;
        }
        .market-ribbon .market-stat:last-child {
            border-right:0;
        }
        .market-stat-label {
            color:#64748B;
            font-size:12px;
            font-weight:650;
            line-height:1.05;
        }
        .market-stat-value {
            display:block;
            margin-top:0.06rem;
            color:#0F172A;
            font-size:17px;
            line-height:1.05;
            font-weight:720;
            font-variant-numeric:tabular-nums;
        }
        .market-stat-detail {
            margin-top:0.06rem;
            color:#94A3B8;
            font-size:11px;
            line-height:1.2;
        }
        .dashboard-discipline-strip {
            display:block;
            margin:0 0 0.42rem;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-left:2px solid #16A34A;
            border-radius:7px;
            background:#FFFFFF;
            overflow:hidden;
        }
        .dashboard-discipline-main-row {
            display:grid;
            grid-template-columns:158px minmax(0, 1fr);
            gap:0;
            align-items:stretch;
            min-height:34px;
        }
        .dashboard-discipline-strip.ok {
            border-left-color:#16A34A;
            background:#FCFEFC;
        }
        .dashboard-discipline-strip.warning {
            border-left-color:#D97706;
            background:#FFFDF8;
        }
        .dashboard-discipline-strip.error {
            border-left-color:#DC2626;
            background:#FFFCFC;
        }
        .dashboard-discipline-title {
            display:grid;
            align-content:center;
            gap:0.08rem;
            padding:0.3rem 0.54rem;
            border-right:1px solid rgba(15, 23, 42, 0.045);
        }
        .dashboard-discipline-strip.ok .dashboard-discipline-title {
            background:#F6FBF7;
        }
        .dashboard-discipline-strip.warning .dashboard-discipline-title {
            background:#FFFAF0;
        }
        .dashboard-discipline-strip.error .dashboard-discipline-title {
            background:#FEF4F4;
        }
        .dashboard-discipline-title strong {
            color:#0F172A;
            font-size:11.5px;
            line-height:1.15;
            font-weight:760;
        }
        .dashboard-discipline-title span {
            color:#64748B;
            font-size:9.5px;
            line-height:1.2;
            font-weight:560;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .dashboard-discipline-metrics {
            display:flex;
            align-items:center;
            gap:0;
            min-width:0;
            overflow:hidden;
            background:rgba(255, 255, 255, 0.72);
        }
        .dashboard-discipline-metrics span {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:0;
            margin:0.25rem 0 0.25rem 0.4rem;
            height:18px;
            padding:0 0.36rem;
            border:1px solid rgba(148, 163, 184, 0.15);
            border-radius:999px;
            background:#FFFFFF;
            color:#334155;
            font-size:10px;
            line-height:18px;
            font-weight:680;
            white-space:nowrap;
        }
        .dashboard-price-alert-strip {
            display:grid;
            grid-template-columns:170px minmax(0, 1fr);
            align-items:stretch;
            min-height:34px;
            margin:0 0 0.42rem;
            border:1px solid rgba(217, 119, 6, 0.18);
            border-left:2px solid #D97706;
            border-radius:7px;
            background:#FFFCF7;
            overflow:hidden;
        }
        .dashboard-price-alert-title {
            display:grid;
            align-content:center;
            gap:0.08rem;
            padding:0.3rem 0.54rem;
            border-right:1px solid rgba(217, 119, 6, 0.09);
            background:#FFF8EC;
        }
        .dashboard-price-alert-title strong {
            color:#0F172A;
            font-size:11.5px;
            line-height:1.15;
            font-weight:760;
        }
        .dashboard-price-alert-title span {
            color:#64748B;
            font-size:9.5px;
            line-height:1.2;
            font-weight:560;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .dashboard-price-alert-items {
            display:flex;
            align-items:center;
            gap:0.4rem;
            min-width:0;
            overflow:hidden;
            padding:0.25rem 0.45rem;
        }
        .dashboard-price-alert-items span {
            display:inline-flex;
            align-items:center;
            gap:0.25rem;
            height:20px;
            max-width:220px;
            padding:0 0.45rem;
            border:1px solid rgba(217, 119, 6, 0.16);
            border-radius:999px;
            background:#FFFFFF;
            color:#334155;
            font-size:10px;
            font-weight:680;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .data-health-strip {
            display:block;
            margin:0 0 0.42rem;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-left:2px solid #94A3B8;
            border-radius:7px;
            background:#FFFFFF;
            overflow:visible;
        }
        .data-health-main-row {
            display:grid;
            grid-template-columns:128px minmax(0, 1fr);
            gap:0;
            align-items:stretch;
            min-height:34px;
        }
        .data-health-strip.ok {
            border-left-color:#16A34A;
            background:#FCFEFC;
        }
        .data-health-strip.warning {
            border-left-color:#D97706;
            background:#FFFDF8;
        }
        .data-health-strip.error {
            border-left-color:#DC2626;
            background:#FFFCFC;
        }
        .data-health-title {
            display:grid;
            align-content:center;
            gap:0.08rem;
            padding:0.3rem 0.54rem;
            border-right:1px solid rgba(15, 23, 42, 0.045);
        }
        .data-health-strip.ok .data-health-title {
            background:#F6FBF7;
        }
        .data-health-strip.warning .data-health-title {
            background:#FFFAF0;
        }
        .data-health-strip.error .data-health-title {
            background:#FEF4F4;
        }
        .data-health-title strong {
            color:#0F172A;
            font-size:11.5px;
            line-height:1.15;
            font-weight:760;
        }
        .data-health-title span {
            color:#94A3B8;
            font-size:9.5px;
            line-height:1.2;
            font-weight:560;
        }
        .data-health-metrics {
            display:flex;
            align-items:center;
            gap:0;
            min-width:0;
            overflow:hidden;
            background:rgba(255, 255, 255, 0.72);
        }
        .data-health-metrics span {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:0;
            margin:0.25rem 0 0.25rem 0.4rem;
            height:18px;
            padding:0 0.36rem;
            border-right:1px solid rgba(15, 23, 42, 0.04);
            border:1px solid rgba(148, 163, 184, 0.15);
            border-radius:999px;
            background:#FFFFFF;
            color:#334155;
            font-size:10px;
            line-height:18px;
            font-weight:680;
            white-space:nowrap;
        }
        .data-health-metrics span:last-child {
            margin-right:0.4rem;
        }
        .data-health-refresh-result {
            max-width:var(--dash-shell-width);
            margin:0 0 0.42rem;
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.6rem;
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:7px;
            background:#FFFFFF;
            padding:0.38rem 0.56rem;
            color:#475569;
            font-size:11px;
        }
        .data-health-refresh-result strong {
            color:#0F172A;
            font-size:11.5px;
            font-weight:780;
            white-space:nowrap;
        }
        .data-health-refresh-result span {
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .data-health-refresh-result.ok { border-left:2px solid #16A34A; }
        .data-health-refresh-result.warning { border-left:2px solid #D97706; }
        .data-health-refresh-result.error { border-left:2px solid #DC2626; }
        .dashboard-risk-summary-strip {
            display:grid;
            grid-template-columns:128px minmax(0, 1fr);
            align-items:stretch;
            min-height:34px;
            margin:0 0 0.42rem;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-left:2px solid #94A3B8;
            border-radius:7px;
            background:#FFFFFF;
            overflow:hidden;
        }
        .dashboard-risk-summary-strip.ok {
            border-left-color:#16A34A;
            background:#FCFEFC;
        }
        .dashboard-risk-summary-strip.warning {
            border-left-color:#D97706;
            background:#FFFDF8;
        }
        .dashboard-risk-summary-title {
            display:grid;
            align-content:center;
            gap:0.08rem;
            padding:0.3rem 0.54rem;
            border-right:1px solid rgba(15, 23, 42, 0.045);
            background:#F8FAFC;
        }
        .dashboard-risk-summary-strip.ok .dashboard-risk-summary-title {
            background:#F6FBF7;
        }
        .dashboard-risk-summary-strip.warning .dashboard-risk-summary-title {
            background:#FFFAF0;
        }
        .dashboard-risk-summary-title strong {
            color:#0F172A;
            font-size:11.5px;
            line-height:1.15;
            font-weight:760;
        }
        .dashboard-risk-summary-title span {
            color:#94A3B8;
            font-size:9.5px;
            line-height:1.2;
            font-weight:560;
        }
        .dashboard-risk-summary-metrics {
            display:flex;
            align-items:center;
            gap:0;
            min-width:0;
            overflow:hidden;
            background:rgba(255, 255, 255, 0.72);
        }
        .dashboard-risk-summary-metrics span {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            min-width:0;
            margin:0.25rem 0 0.25rem 0.4rem;
            height:18px;
            padding:0 0.36rem;
            border:1px solid rgba(148, 163, 184, 0.15);
            border-radius:999px;
            background:#FFFFFF;
            color:#334155;
            font-size:10px;
            line-height:18px;
            font-weight:680;
            white-space:nowrap;
        }
        .dashboard-risk-summary-metrics strong {
            margin-left:0.14rem;
            color:#0F172A;
            font-weight:780;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-filter-strip {
            max-width:var(--dash-shell-width);
            margin:0 0 0.42rem;
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.75rem;
            padding:0.36rem 0.48rem;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-radius:var(--dash-radius-md);
            background:#FFFFFF;
        }
        .dashboard-filter-title {
            display:grid;
            gap:0.04rem;
            flex:0 0 auto;
        }
        .dashboard-filter-title strong {
            color:#0F172A;
            font-size:12px;
            font-weight:780;
            line-height:1.15;
        }
        .dashboard-filter-title span {
            color:#94A3B8;
            font-size:10px;
            font-weight:620;
            line-height:1.15;
        }
        .dashboard-filter-chips {
            display:flex;
            align-items:center;
            justify-content:flex-end;
            gap:0.35rem;
            min-width:0;
            flex-wrap:wrap;
        }
        .watchlist-filter-chips {
            flex:1 1 auto;
            row-gap:0.24rem;
        }
        .dashboard-filter-chip {
            display:inline-flex;
            align-items:center;
            gap:0.22rem;
            height:23px;
            padding:0 0.52rem;
            border:1px solid var(--dash-border);
            border-radius:999px;
            background:#FFFFFF;
            color:var(--dash-neutral) !important;
            font-size:10.8px;
            font-weight:700;
            text-decoration:none !important;
            white-space:nowrap;
        }
        .dashboard-filter-chip strong {
            color:var(--dash-text);
            font-weight:800;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-filter-chip.active {
            border-color:rgba(37, 99, 235, 0.24);
            background:var(--dash-info-bg);
            color:var(--dash-info) !important;
        }
        .dashboard-filter-chip.tone-green.active { background:var(--dash-success-bg); border-color:var(--dash-success-border); color:var(--dash-success) !important; }
        .dashboard-filter-chip.tone-blue.active { background:var(--dash-info-bg); border-color:var(--dash-info-border); color:var(--dash-info) !important; }
        .dashboard-filter-chip.tone-yellow.active { background:var(--dash-warning-bg); border-color:var(--dash-warning-border); color:var(--dash-warning) !important; }
        .dashboard-filter-chip.tone-red.active { background:var(--dash-danger-bg); border-color:var(--dash-danger-border); color:var(--dash-danger) !important; }
        .dashboard-filter-chip:hover,
        .dashboard-filter-chip:visited {
            color:var(--dash-text) !important;
            text-decoration:none !important;
            background:var(--dash-surface-muted);
        }
        .data-health-popover {
            position:relative;
            min-width:0;
            height:100%;
            border-left:1px solid rgba(15, 23, 42, 0.05);
            color:#64748B;
            font-size:10px;
            line-height:1.2;
            font-weight:650;
            white-space:nowrap;
        }
        .data-health-popover summary {
            display:flex;
            align-items:center;
            justify-content:center;
            height:100%;
            width:100%;
            padding:0 0.54rem;
            cursor:pointer;
            list-style:none;
            outline:none;
            white-space:nowrap;
        }
        .data-health-popover summary::-webkit-details-marker {
            display:none;
        }
        .data-health-popover summary:hover {
            color:#0F172A;
            background:rgba(15, 23, 42, 0.025);
        }
        .data-health-popover summary span {
            color:inherit;
            line-height:18px;
        }
        .data-health-popover[open] summary {
            background:rgba(15, 23, 42, 0.035);
            color:#0F172A;
        }
        .data-health-popover-panel {
            position:absolute;
            right:0;
            top:calc(100% + 7px);
            z-index:30;
            width:min(720px, calc(100vw - 48px));
            border:1px solid rgba(148, 163, 184, 0.22);
            border-radius:7px;
            background:#FFFFFF;
            box-shadow:0 18px 42px rgba(15, 23, 42, 0.13);
            white-space:normal;
        }
        .data-health-popover:not([open]) .data-health-popover-panel {
            display:none;
        }
        .data-health-detail-panel {
            position:static;
            display:grid;
            grid-template-columns:repeat(3, minmax(0, 1fr));
            gap:0.42rem;
            width:auto;
            margin:0;
            padding:0.52rem;
            border:0;
            border-radius:7px;
            background:#FFFFFF;
            box-shadow:none;
            color:#0F172A;
            white-space:normal;
        }
        .data-health-detail-group {
            display:grid;
            gap:0.3rem;
            min-width:0;
            align-content:start;
            padding:0.38rem 0.42rem;
            border:1px solid rgba(148, 163, 184, 0.14);
            border-radius:6px;
            background:#FBFCFE;
        }
        .data-health-detail-head {
            display:flex;
            align-items:center;
            justify-content:space-between;
            gap:0.5rem;
            color:#0F172A;
        }
        .data-health-detail-head strong {
            font-size:10.5px;
            line-height:1.2;
            font-weight:760;
        }
        .data-health-detail-head span {
            color:#64748B;
            font-size:10px;
            font-weight:760;
            font-variant-numeric:tabular-nums;
        }
        .data-health-detail-row {
            display:grid;
            grid-template-columns:44px minmax(0, 1fr);
            gap:0.34rem;
            align-items:center;
            min-width:0;
            color:#475569;
            font-size:10px;
            line-height:1.25;
        }
        .data-health-detail-row strong {
            color:#0F172A;
            font-size:10.5px;
            font-weight:780;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .data-health-detail-row span {
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .data-health-detail-row em,
        .data-health-detail-action {
            grid-column:2;
            color:#52657F;
            font-style:normal;
            font-weight:720;
            text-align:left;
            text-decoration:none !important;
            white-space:nowrap;
        }
        .data-health-detail-action:hover,
        .data-health-detail-action:visited {
            color:#0F172A;
            text-decoration:none !important;
        }
        .data-health-detail-empty {
            color:#94A3B8;
            font-size:10px;
            font-weight:620;
        }
        .dashboard-risk-radar {
            display:grid;
            grid-template-columns:108px minmax(0, 1fr);
            align-items:stretch;
            min-height:52px;
            margin-top:0;
            margin-bottom:0.42rem;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-radius:7px;
            background:#FFFFFF;
            overflow:hidden;
        }
        .dashboard-risk-radar-head {
            display:grid;
            align-content:center;
            gap:0.12rem;
            padding:0.42rem 0.58rem;
            border-right:1px solid rgba(15, 23, 42, 0.045);
            background:#F8FAFC;
        }
        .dashboard-risk-radar-head strong {
            color:#0F172A;
            font-size:12.5px;
            font-weight:760;
            line-height:1.15;
        }
        .dashboard-risk-radar-head span {
            color:#94A3B8;
            font-size:10px;
            font-weight:560;
            line-height:1.25;
        }
        .dashboard-risk-radar-list {
            display:grid;
            grid-template-columns:repeat(6, minmax(0, 1fr));
            gap:0;
            min-width:0;
        }
        .dashboard-risk-radar-item {
            display:grid;
            grid-template-columns:minmax(0, 1fr);
            grid-template-rows:auto auto;
            align-content:center;
            gap:0.16rem;
            min-width:0;
            max-width:100%;
            min-height:52px;
            padding:0.42rem 0.52rem;
            border-left:2px solid rgba(148, 163, 184, 0.35);
            border-right:1px solid rgba(15, 23, 42, 0.035);
            background:#FFFFFF;
            color:inherit;
            text-decoration:none !important;
            overflow:hidden;
        }
        .dashboard-risk-radar-item:hover,
        .dashboard-risk-radar-item:focus,
        .dashboard-risk-radar-item:visited {
            color:inherit;
            text-decoration:none !important;
        }
        .dashboard-risk-radar-item:hover {
            background:#FAFBFD;
        }
        .dashboard-risk-radar-item.active {
            background:#F8FAFC;
            box-shadow: inset 0 0 0 1px rgba(15, 23, 42, 0.06);
        }
        .dashboard-risk-radar-item:last-child {
            border-right:0;
        }
        .dashboard-risk-radar-item.red { border-left-color:#EF4444; }
        .dashboard-risk-radar-item.amber { border-left-color:#D97706; }
        .dashboard-risk-radar-item.blue { border-left-color:#64748B; }
        .dashboard-risk-radar-item.slate { border-left-color:#94A3B8; }
        .dashboard-risk-radar-item div {
            display:flex;
            align-items:center;
            gap:0.26rem;
            min-height:15px;
            min-width:0;
            max-width:100%;
            overflow:hidden;
        }
        .dashboard-risk-radar-item span {
            color:#64748B;
            font-size:11px;
            font-weight:650;
            line-height:1.15;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .dashboard-risk-radar-item strong {
            color:#0F172A;
            font-size:13.5px;
            line-height:1.1;
            font-weight:760;
            font-variant-numeric:tabular-nums;
            flex:0 0 auto;
        }
        .dashboard-risk-radar-item em {
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            line-height:1.25;
        }
        .dashboard-risk-radar-item em {
            color:#94A3B8;
            font-size:10.5px;
            font-style:normal;
            font-weight:560;
        }
        .decision-terminal-head,
        .watchlist-head {
            display:flex;
            justify-content:space-between;
            align-items:flex-end;
            gap:0.8rem;
            margin-top:0.08rem;
            margin-bottom:0.12rem;
        }
        .decision-terminal-head {
            max-width:var(--dash-shell-width);
            margin-left:0;
            margin-right:0;
        }
        .decision-terminal-head strong,
        .watchlist-head strong {
            display:block;
            color:var(--dash-text);
            font-size:15.5px;
            font-weight:800;
            line-height:1.2;
        }
        .decision-terminal-head span,
        .watchlist-head span {
            display:block;
            margin-top:0.12rem;
            color:var(--dash-secondary);
            font-size:11.5px;
            font-weight:520;
        }
        .dashboard-priority-strip {
            display:grid;
            grid-template-columns:82px minmax(0, 1fr);
            align-items:stretch;
            min-height:42px;
            max-width:var(--dash-shell-width);
            margin:0.18rem 0 0;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-bottom:0;
            border-radius:6px 6px 0 0;
            background:linear-gradient(180deg, #F8FAFC 0%, #FFFFFF 100%);
            overflow:hidden;
            box-sizing:border-box;
        }
        .dashboard-priority-head {
            display:flex;
            flex-direction:column;
            justify-content:center;
            align-items:flex-start;
            gap:0.02rem;
            min-width:0;
            padding:0 0.52rem;
            border-right:1px solid rgba(148, 163, 184, 0.14);
            background:rgba(241,245,249,0.72);
        }
        .dashboard-priority-head strong {
            color:#0F172A;
            font-size:12.5px;
            font-weight:760;
            line-height:1.1;
        }
        .dashboard-priority-head span {
            color:#64748B;
            font-size:10px;
            font-weight:520;
            white-space:nowrap;
        }
        .dashboard-priority-list {
            display:grid;
            grid-template-columns:repeat(5, minmax(0, 1fr));
            align-items:center;
            gap:0.4rem;
            min-width:0;
            margin:0;
            padding:0.34rem 0.48rem;
            border:0;
            background:transparent;
            overflow:hidden;
            box-sizing:border-box;
        }
        .dashboard-priority-row {
            display:grid;
            grid-template-columns:8px minmax(34px, auto) minmax(42px, auto) minmax(0, 1fr);
            align-items:center;
            gap:0.28rem;
            min-height:30px;
            min-width:0;
            max-width:100%;
            padding:0 0.5rem;
            border:1px solid rgba(148, 163, 184, 0.10);
            border-radius:5px;
            background:rgba(255,255,255,0.58);
            overflow:hidden;
            box-sizing:border-box;
            box-shadow:none;
            color:inherit;
            text-decoration:none !important;
            cursor:pointer;
        }
        .dashboard-priority-row:visited,
        .dashboard-priority-row:focus,
        .dashboard-priority-row:hover {
            color:inherit;
            text-decoration:none !important;
        }
        .dashboard-priority-row:first-child {
            background:#FFFFFF;
            border-color:rgba(148, 163, 184, 0.12);
        }
        .dashboard-priority-row.tone-green {
            box-shadow:inset 2px 0 0 rgba(22,101,52,0.20);
        }
        .dashboard-priority-row.tone-blue {
            box-shadow:inset 2px 0 0 rgba(54,81,111,0.20);
        }
        .dashboard-priority-row.tone-yellow {
            box-shadow:inset 2px 0 0 rgba(122,92,18,0.20);
        }
        .dashboard-priority-row.tone-red {
            box-shadow:inset 2px 0 0 rgba(138,31,31,0.20);
        }
        .dashboard-priority-row:hover {
            background:#FFFFFF;
            border-color:rgba(148, 163, 184, 0.16);
        }
        .dashboard-priority-row:last-child {
            border-right:1px solid rgba(148, 163, 184, 0.10);
        }
        .dashboard-priority-status,
        .dashboard-dot-status {
            display:inline-flex;
            align-items:center;
            gap:0.28rem;
            color:#475569;
            font-size:10.5px;
            font-weight:600;
            white-space:nowrap;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .dashboard-priority-status i,
        .dashboard-dot-status i {
            display:inline-block;
            width:7px;
            height:7px;
            border-radius:999px;
            background:#94A3B8;
            box-shadow:0 0 0 2px rgba(148, 163, 184, 0.10);
        }
        .dashboard-priority-status.green i,
        .dashboard-dot-status.green i { background:#16803E; }
        .dashboard-priority-status.blue i,
        .dashboard-dot-status.blue i { background:#36516F; }
        .dashboard-priority-status.yellow i,
        .dashboard-priority-status.orange i,
        .dashboard-dot-status.orange i,
        .dashboard-dot-status.yellow i { background:#A46A16; }
        .dashboard-priority-status.red i,
        .dashboard-dot-status.red i { background:#B34A4A; }
        .dashboard-priority-status.deepred i,
        .dashboard-dot-status.deepred i { background:#6F1111; }
        .dashboard-priority-row strong {
            min-width:0;
            color:#0F172A;
            font-size:11.5px;
            font-weight:780;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .dashboard-priority-row span:not(.dashboard-priority-status) {
            min-width:0;
            max-width:78px;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:#334155;
            font-size:11.5px;
            font-weight:700;
        }
        .dashboard-priority-row em {
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:#64748B;
            font-size:10.8px;
            font-style:normal;
            font-weight:520;
        }
        .dashboard-priority-empty {
            min-height:28px;
            display:flex;
            align-items:center;
            color:#64748B;
            font-size:11.5px;
            font-weight:560;
            padding:0 0.5rem;
        }
        .dashboard-priority-more {
            grid-column:2;
            border-top:1px solid rgba(148, 163, 184, 0.10);
            background:rgba(255, 255, 255, 0.58);
        }
        .dashboard-priority-more summary {
            min-height:28px;
            padding:0.28rem 0.58rem;
            color:#52657F;
            font-size:11px;
            font-weight:720;
            cursor:pointer;
            list-style:none;
        }
        .dashboard-priority-more summary::-webkit-details-marker {
            display:none;
        }
        .dashboard-priority-more-list {
            display:grid;
            grid-template-columns:repeat(5, minmax(0, 1fr));
            gap:0.4rem;
            padding:0 0.48rem 0.4rem;
        }
        div[data-testid="stVerticalBlock"] > div:has(.decision-lanes-marker) + div [data-testid="stHorizontalBlock"] {
            max-width:var(--dash-shell-width);
            margin:0 0 0.3rem;
            padding:0.34rem 0.4rem 0.4rem;
            border:1px solid rgba(148, 163, 184, 0.16);
            border-top:0;
            border-radius:0 0 7px 7px;
            background:linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
            gap:0.36rem !important;
            overflow:hidden;
            box-sizing:border-box;
        }
        .summary-panel-head {
            min-height:2rem;
            border-radius:5px 5px 0 0;
            background:rgba(255,255,255,0.78);
            border-color:rgba(148, 163, 184, 0.13);
            padding:0.26rem 0.4rem 0.26rem 0.52rem;
            position:relative;
            overflow:hidden;
        }
        .summary-panel-head::before {
            content:"";
            position:absolute;
            left:0;
            top:0;
            bottom:0;
            width:3px;
            background:#94A3B8;
        }
        .summary-panel-head.tone-green {
            background:#FFFFFF;
            border-color:rgba(148,163,184,0.14);
        }
        .summary-panel-head.tone-green::before { background:#16803E; }
        .summary-panel-head.tone-blue {
            background:#FFFFFF;
            border-color:rgba(148,163,184,0.14);
        }
        .summary-panel-head.tone-blue::before { background:#36516F; }
        .summary-panel-head.tone-yellow {
            background:#FFFFFF;
            border-color:rgba(148,163,184,0.14);
        }
        .summary-panel-head.tone-yellow::before { background:#A46A16; }
        .summary-panel-head.tone-red {
            background:#FFFFFF;
            border-color:rgba(148,163,184,0.14);
        }
        .summary-panel-head.tone-red::before { background:#B34A4A; }
        .summary-panel-title {
            font-size:12px;
            color:#0F172A;
            font-weight:700;
        }
        .summary-panel-subtitle {
            color:#94A3B8;
            font-size:10px;
            font-weight:500;
            line-height:1.12;
            margin-top:1px;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .summary-count {
            min-width:18px;
            height:18px;
            font-size:10.5px;
            font-weight:620;
            box-shadow:inset 0 0 0 1px rgba(255,255,255,0.42);
            background:#FFFFFF !important;
            border-color:rgba(148,163,184,0.22) !important;
        }
        .summary-panel-head.tone-green .summary-count { color:#166534 !important; }
        .summary-panel-head.tone-blue .summary-count { color:#36516F !important; }
        .summary-panel-head.tone-yellow .summary-count { color:#7A5C12 !important; }
        .summary-panel-head.tone-red .summary-count { color:#8A1F1F !important; }
        .lane-row-stack {
            height:112px;
            min-height:112px;
            overflow:hidden;
            border-bottom:1px solid rgba(15, 23, 42, 0.035);
            box-sizing:border-box;
        }
        .lane-row-stack .summary-empty {
            height:112px;
            min-height:112px;
        }
        .lane-item {
            grid-template-columns:46px minmax(0, 1fr) minmax(58px, 82px);
            gap:8px;
            height:28px;
            min-height:28px;
            padding:0 0.55rem;
            border-top-color:rgba(15, 23, 42, 0.032);
            min-width:0;
            max-width:100%;
            overflow:hidden;
            box-sizing:border-box;
        }
        .lane-symbol {
            color:#0F172A;
            font-size:11.5px;
            font-weight:700;
            min-width:0;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .lane-reason {
            color:#53657B;
            font-size:11px;
            font-weight:530;
            justify-self:start;
            min-width:0;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .lane-item > .decision-badge {
            justify-self:end;
            flex-shrink:0;
            min-width:0;
            max-width:82px;
            height:17px;
            min-height:17px;
            padding:0 5px;
            border-radius:5px;
            font-size:10.5px;
            font-weight:580;
            line-height:17px;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .summary-empty,
        .st-key-dashboard_lane_more_actionable button,
        .st-key-dashboard_lane_more_nearBuyZone button,
        .st-key-dashboard_lane_more_waitOrReview button,
        .st-key-dashboard_lane_more_noChaseHighRisk button {
            color:#718198 !important;
            font-size:10px !important;
            min-height:20px !important;
            height:20px !important;
            padding:0 0.5rem !important;
            border:0 !important;
            border-top:1px solid rgba(15, 23, 42, 0.04) !important;
            border-radius:0 0 5px 5px !important;
            background:transparent !important;
            box-shadow:none !important;
            justify-content:flex-end !important;
            text-align:right !important;
            font-weight:520 !important;
            line-height:20px !important;
            opacity:0.72;
            font-variant-numeric:tabular-nums;
        }
        .st-key-dashboard_lane_more_actionable button p,
        .st-key-dashboard_lane_more_nearBuyZone button p,
        .st-key-dashboard_lane_more_waitOrReview button p,
        .st-key-dashboard_lane_more_noChaseHighRisk button p {
            width:auto;
            margin:0;
            color:inherit;
            font-size:10px !important;
            font-weight:520 !important;
            line-height:20px !important;
            letter-spacing:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            text-align:right;
        }
        .st-key-dashboard_lane_more_actionable button:hover {
            color:#334155 !important;
            background:rgba(248,250,252,0.78) !important;
            opacity:1;
        }
        .st-key-dashboard_lane_more_nearBuyZone button:hover {
            color:#334155 !important;
            background:rgba(248,250,252,0.78) !important;
            opacity:1;
        }
        .st-key-dashboard_lane_more_waitOrReview button:hover {
            color:#334155 !important;
            background:rgba(248,250,252,0.78) !important;
            opacity:1;
        }
        .st-key-dashboard_lane_more_noChaseHighRisk button:hover {
            color:#334155 !important;
            background:rgba(248,250,252,0.78) !important;
            opacity:1;
        }
        .table-filter-chip {
            display:inline-flex;
            align-items:center;
            width:max-content;
            max-width:var(--dash-shell-width);
            min-height:28px;
            height:28px;
            margin-top:0.34rem;
            margin-bottom:0.34rem;
            padding:0 9px;
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:999px;
            background:#FFFFFF;
            color:#64748B;
            font-size:11.5px;
            font-weight:620;
            line-height:26px;
        }
        .st-key-dashboard_clear_lane_filter,
        .st-key-dashboard_clear_risk_filter {
            margin-top:0.34rem;
            margin-bottom:0.34rem;
        }
        .st-key-dashboard_clear_lane_filter button,
        .st-key-dashboard_clear_risk_filter button {
            min-width:54px !important;
            min-height:28px !important;
            height:28px !important;
            padding:0 8px !important;
            border-radius:999px !important;
            border:1px solid rgba(239, 68, 68, 0.20) !important;
            background:rgba(254, 242, 242, 0.62) !important;
            color:#A33A3A !important;
            box-shadow:none !important;
            font-size:11px !important;
            font-weight:620 !important;
            line-height:26px !important;
            white-space:nowrap !important;
            word-break:keep-all !important;
            opacity:0.88;
        }
        .st-key-dashboard_clear_lane_filter button p,
        .st-key-dashboard_clear_risk_filter button p {
            margin:0;
            font-size:11px !important;
            font-weight:620 !important;
            line-height:26px !important;
            letter-spacing:0;
            white-space:nowrap !important;
            word-break:keep-all !important;
        }
        .st-key-dashboard_clear_lane_filter button:hover,
        .st-key-dashboard_clear_risk_filter button:hover {
            opacity:1;
            color:#8A1F1F !important;
            background:rgba(254, 226, 226, 0.78) !important;
            border-color:rgba(220, 38, 38, 0.30) !important;
        }
        .decision-table {
            display:block;
            width:100%;
            border:1px solid var(--dash-border);
            border-radius:var(--dash-radius);
            overflow-x:auto;
            overflow-y:hidden;
            background:#FFFFFF;
            margin-top:0;
            margin-bottom:0.72rem;
            box-shadow:none;
            box-sizing:border-box;
        }
        .decision-grid {
            display:grid;
            grid-template-columns:
                minmax(112px, 0.72fr)
                minmax(172px, 0.98fr)
                minmax(88px, 0.45fr)
                minmax(172px, 0.86fr)
                minmax(90px, 0.44fr)
                minmax(148px, 0.72fr)
                minmax(92px, 0.42fr)
                92px;
            align-items:center;
            gap:0.62rem;
            min-height:var(--dash-table-row-height);
            min-width:980px;
            width:100%;
            padding:0 13px;
            box-sizing:border-box;
            font-size:12px;
            overflow:hidden;
        }
        .decision-grid > * {
            min-width:0;
            max-width:100%;
            overflow:hidden;
        }
        .decision-grid > :nth-child(2) {
            padding-left:16px;
            padding-right:14px;
        }
        .decision-grid > :nth-child(3) {
            padding-left:2px;
        }
        .decision-grid-head {
            min-height:30px;
            background:rgba(248, 250, 252, 0.92);
            border-bottom:1px solid var(--dash-border-soft);
        }
        .decision-row {
            border-bottom:1px solid var(--dash-border-soft);
            cursor:pointer;
            transition:background 120ms ease;
        }
        .decision-row:last-child {
            border-bottom:0;
        }
        .decision-row:hover {
            background:#F8FAFC;
        }
        .decision-header {
            min-height:32px;
            padding:0;
            color:var(--dash-muted);
            font-size:10.6px;
            font-weight:680;
            background:transparent;
            border-bottom:0;
            position:static;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            letter-spacing:0;
        }
        .decision-header.align-center {
            display:flex;
            align-items:center;
            justify-content:center;
            text-align:center;
        }
        .decision-cell {
            display:flex;
            align-items:center;
            min-height:var(--dash-table-row-height);
            padding:0;
            border-bottom:0;
            color:var(--dash-text);
            font-size:12px;
            font-variant-numeric:tabular-nums;
            min-width:0;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            box-sizing:border-box;
        }
        .decision-table.compact .decision-cell {
            min-height:var(--dash-table-row-height);
            font-size:12px;
        }
        .decision-cell > * {
            min-width:0;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .decision-cell-stack {
            display:flex;
            flex-direction:column;
            align-items:flex-start;
            justify-content:center;
            gap:0.04rem;
            min-width:0;
            max-width:100%;
            line-height:1.13;
        }
        .decision-cell-stack.align-right {
            align-items:flex-end;
        }
        .price-market-cell {
            align-items:flex-start;
            text-align:left;
            width:100%;
        }
        .price-market-cell strong {
            font-family:ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
            font-variant-numeric:tabular-nums;
            letter-spacing:0;
            font-size:12.6px;
        }
        .price-market-cell span {
            color:var(--dash-muted);
            font-weight:560;
        }
        .decision-cell-stack strong,
        .action-cell strong {
            max-width:100%;
            color:var(--dash-text);
            font-size:12px;
            font-weight:700;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .action-cell {
            width:100%;
            max-width:146px;
            justify-content:flex-start;
        }
        .action-cell .decision-badge {
            max-width:138px;
        }
        .decision-cell-stack span,
        .action-cell span {
            max-width:100%;
            color:var(--dash-secondary);
            font-size:11px;
            font-weight:540;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .stock-cell strong {
            font-size:13.8px;
            font-weight:800;
            letter-spacing:0;
            padding-left:1px;
        }
        .stock-cell span {
            color:var(--dash-secondary);
            max-width:100%;
        }
        .decision-badge {
            display:inline-flex;
            align-items:center;
            height:var(--dash-badge-height);
            min-height:var(--dash-badge-height);
            max-width:100%;
            padding:0 8px;
            border-radius:999px;
            font-size:11px;
            font-weight:700;
            line-height:var(--dash-badge-height);
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
            flex:0 1 auto;
            min-width:0;
            box-sizing:border-box;
        }
        .decision-cell .decision-badge {
            width:auto;
            max-width:100%;
        }
        .entry-rating-cell {
            justify-content:flex-start;
        }
        .entry-rating-token {
            display:flex;
            flex-direction:column;
            align-items:flex-start;
            gap:1px;
            min-height:36px;
            height:auto;
            max-width:100%;
            min-width:0;
            padding:4px 8px;
            border-radius:8px;
            box-sizing:border-box;
            overflow:hidden;
            white-space:normal;
        }
        .entry-rating-token strong {
            max-width:100%;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:inherit;
            font-size:11.4px;
            font-weight:760;
            line-height:14px;
        }
        .entry-rating-token em {
            max-width:100%;
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:var(--dash-secondary);
            font-size:10px;
            font-style:normal;
            font-weight:650;
            line-height:12px;
            opacity:0.82;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-dot-status {
            display:inline-flex;
            align-items:center;
            gap:0.28rem;
            color:var(--dash-neutral);
            font-size:11.5px;
            font-weight:620;
            min-width:0;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
        }
        .dashboard-dot-status i {
            flex:0 0 auto;
        }
        .action-view-cell {
            justify-content:center;
            justify-self:center;
            width:96px;
            max-width:96px;
        }
        .dashboard-row-actions {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            gap:7px;
            width:max-content;
            max-width:96px;
            white-space:nowrap;
        }
        .dashboard-row-actions .dashboard-view-action:first-child::after {
            content:"·";
            position:absolute;
            right:-6px;
            top:50%;
            transform:translateY(-50%);
            color:#CBD5E1;
            font-size:12px;
            line-height:1;
            pointer-events:none;
        }
        .dashboard-view-action {
            position:relative;
            display:inline-flex;
            align-items:center;
            justify-content:center;
            gap:0.12rem;
            min-width:0;
            height:22px;
            padding:0 0.18rem;
            border:1px solid transparent;
            border-radius:4px;
            background:transparent;
            color:var(--dash-text);
            font-size:11.5px;
            font-weight:740;
            text-decoration:none !important;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
            box-sizing:border-box;
        }
        .dashboard-record-action {
            padding-left:0.1rem;
            padding-right:0.1rem;
            color:var(--dash-muted);
            font-weight:620;
        }
        .dashboard-view-action span {
            display:block;
            padding-right:0;
        }
        .dashboard-view-action i {
            position:absolute;
            right:4px;
            color:#94A3B8;
            font-style:normal;
            font-size:14px;
            line-height:1;
        }
        .dashboard-view-action:hover {
            color:var(--dash-text);
            border-color:var(--dash-border);
            background:var(--dash-surface-muted);
        }
        .dashboard-view-action:hover i {
            color:#475569;
        }
        .decision-empty {
            display:flex;
            align-items:center;
            min-height:44px;
            padding:0 12px;
            color:#64748B;
            font-size:12px;
            border-top:1px solid rgba(15, 23, 42, 0.05);
        }
        @media (max-width: 760px) {
            .decision-table {
                border-radius:7px;
            }
            .decision-grid {
                grid-template-columns:112px 172px 88px 172px 90px 148px 92px 92px;
                min-width:948px;
                gap:8px;
                min-height:48px;
                padding:0 8px;
                font-size:11.5px;
            }
            .decision-grid-head {
                min-height:29px;
            }
            .decision-cell,
            .decision-table.compact .decision-cell {
                min-height:48px;
                font-size:11.5px;
            }
            .decision-cell-stack strong,
            .action-cell strong {
                font-size:11.5px;
            }
            .decision-cell-stack span,
            .action-cell span {
                font-size:10.5px;
            }
            .stock-cell strong {
                font-size:12px;
            }
            .decision-badge {
                height:17px;
                min-height:17px;
                padding:0 5px;
                font-size:10.5px;
                line-height:17px;
            }
            .dashboard-dot-status,
            .dashboard-view-action {
                font-size:11px;
            }
        }
        .drawer-raw {
            margin-top: 0.8rem;
        }
        .drawer-raw summary {
            cursor: pointer;
            color: var(--dash-text);
            font-weight: 700;
        }
        @media (max-width: 1100px) {
            .stock-drawer {
                width: min(94vw, 640px);
            }
            .terminal-header {
                align-items: flex-start;
                flex-direction: column;
            }
            .terminal-meta {
                justify-content: flex-start;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _dashboard_view_action_html(symbol: str) -> str:
    normalized_symbol = str(symbol or "").upper()
    safe_symbol = escape(normalized_symbol)
    onclick = (
        "event.preventDefault();event.stopPropagation();"
        f"if(window.__dashboardOpenDrawer){{window.__dashboardOpenDrawer({json.dumps(normalized_symbol, ensure_ascii=False)},null);}}"
        "return false;"
    )
    record_href = f"?page=dashboard&recordSignal={escape(normalized_symbol, quote=True)}#watchlist-table"
    return (
        '<span class="dashboard-row-actions">'
        f'<a class="dashboard-view-action" href="#" data-dashboard-drawer-open="{safe_symbol}" '
        f'onclick="{escape(onclick, quote=True)}" title="打开 {safe_symbol} 右侧详情面板"><span>查看</span></a>'
        f'<a class="dashboard-view-action dashboard-record-action" href="{record_href}" target="_self" '
        f'onclick="event.stopPropagation();" title="记录 {safe_symbol} 当前系统信号"><span>记录</span></a>'
        "</span>"
    )


def _dashboard_cell_link(inner_html: str, symbol: str | None) -> str:
    safe_symbol = escape(str(symbol or "").upper())
    if not safe_symbol:
        return inner_html
    return (
        f'<a class="decision-cell-link" href="#" data-dashboard-drawer-open="{safe_symbol}" '
        f'title="打开 {safe_symbol} 右侧详情面板">{inner_html}</a>'
    )


def _body_cell_html(value: object, align: object = None, symbol: str | None = None) -> str:
    align_class = " align-right" if align == "right" else ""
    return _dashboard_cell_link(f'<div class="decision-cell{align_class}">{escape(str(value))}</div>', symbol)


def _summary_badge_html(symbol: object, action: object, color: str) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return (
        f'<span class="summary-badge" style="background:{background};color:{foreground};border:1px solid {border};">'
        f"<span>{escape(str(symbol))}</span><span>{escape(str(action))}</span>"
        "</span>"
    )


def _lane_item_html(row: pd.Series) -> str:
    return _lane_item_html_base(row, _badge_color_for_cell, _lane_full_reason)


def _lane_stack_html(rows: list[pd.Series]) -> str:
    return _lane_stack_html_base(rows, _lane_item_html)


def _lane_reason(row: pd.Series) -> str:
    return _lane_reason_base(row, _lane_full_reason)


def _lane_full_reason(row: pd.Series) -> str:
    return _lane_full_reason_base(
        row,
        _list_value,
        _numeric,
        _translate_factor,
        lambda items, limit=None: _translated_list(items, limit=limit),
        _quality_negative_items,
    )


def _overheat_card_html(row: pd.Series) -> str:
    score = _numeric(row.get("overheatScore"))
    status = str(row.get("overheatStatus") or "正常评估")
    action = str(row.get("overheatAction") or row.get("action") or "正常评估")
    recommendation = str(row.get("overheatRecommendation") or "等待确认")
    reasons = row.get("overheatReasons") or []
    if isinstance(reasons, str):
        reasons = [item.strip() for item in reasons.split("；") if item.strip()]
    reason_text = " / ".join(str(item).rstrip("。") for item in list(reasons)[:3]) or "未触发明显追高风险"
    color = _overheat_color(score)
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return (
        '<div class="overheat-card">'
        '<div class="overheat-top">'
        f'<strong>{escape(str(row.get("symbol")))}</strong>'
        f'<span style="background:{background};color:{foreground};border:1px solid {border};">{score:.0f}</span>'
        "</div>"
        f'<div class="overheat-state">状态：<b>{escape(status)}</b></div>'
        f'<div class="overheat-reason">原因：{escape(reason_text)}</div>'
        f'<div class="overheat-action">建议：{escape(action)} · {escape(recommendation)}</div>'
        "</div>"
    )


def _overheat_color(score: float) -> str:
    if score >= 80:
        return "red"
    if score >= 60:
        return "orange"
    if score >= 40:
        return "yellow"
    if score >= 20:
        return "blue"
    return "gray"


def _detail_metric_html(label: object, value: object) -> str:
    return (
        '<div class="detail-metric">'
        f'<span class="detail-metric-label">{escape(str(label))}</span>'
        f'<span class="detail-metric-value">{escape(str(value))}</span>'
        "</div>"
    )


def _first_present(*values: object) -> float | None:
    for value in values:
        if not _is_missing(value):
            return float(value)
    return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if _is_missing(numerator) or _is_missing(denominator) or float(denominator) == 0:
        return None
    return float(numerator) / float(denominator)


def _is_missing(value: object) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


configure_dashboard_drawer(
    DashboardDrawerDeps(
        badge_span_html=_badge_span_html,
        badge_color_for_cell=_badge_color_for_cell,
        translated_join=_translated_join,
        quality_negative_items=_quality_negative_items,
        risk_items=_risk_items,
        resolution_value_text=_resolution_value_text,
        clean_resolution_explanation=_clean_resolution_explanation,
        dedupe_text=_dedupe_text,
        metric_resolution_groups=_metric_resolution_groups,
        drawer_actionable_resolution_row=_drawer_actionable_resolution_row,
        drawer_calculated_resolution_row=_drawer_calculated_resolution_row,
        drawer_low_priority_resolution_row=_drawer_low_priority_resolution_row,
        detail_groups=DETAIL_GROUPS,
    )
)
