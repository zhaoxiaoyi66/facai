from __future__ import annotations

from datetime import datetime
from html import escape
import json
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from buy_zone_engine import generate_buy_zone
from data.providers import get_market_data_provider
from data.fundamentals import FundamentalCache
from data.prices import PriceCache
from data.review_queue_builder import ReviewQueueStore
from data.stock_plan import StockPlanStore
from formatting import format_currency, format_multiple, format_percent
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from scoring.metric_sources import fcf_margin_metric, fcf_margin_source_note
from scoring.final_decision_adapter import build_final_decision_bundle
from scoring.total_score import calculate_total_score
from settings import load_watchlist
from ui.metric_labels import action_label, confidence_label, metric_label, model_type_label, resolution_status_label


DASHBOARD_COLUMNS = [
    {"key": "symbol", "label": "代码", "align": "left"},
    {"key": "price", "label": "现价", "align": "right"},
    {"key": "marketCap", "label": "市值", "align": "right"},
    {"key": "qualityRating", "label": "质量", "kind": "badge"},
    {"key": "entryRating", "label": "买点", "kind": "badge"},
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
    {"key": "entryRating", "label": "买点", "kind": "badge"},
    {"key": "riskRating", "label": "风险", "kind": "badge"},
    {"key": "actionSummary", "label": "动作"},
    {"key": "dataStatus", "label": "数据"},
    {"key": "actions", "label": "查看"},
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
    "green": ("#F4FAF6", "#166534", "#DDEBE2"),
    "blue": ("#F4F7FB", "#36516F", "#DCE6F2"),
    "yellow": ("#FCFAF0", "#7A5C12", "#EEE6C8"),
    "orange": ("#FBF7F1", "#7C4A1D", "#ECDCC8"),
    "red": ("#FBF5F5", "#8A1F1F", "#ECD5D5"),
    "deepred": ("#FDF1F1", "#6F1111", "#E7B9B9"),
    "gray": ("#F8FAFC", "#475569", "#E4EAF1"),
}

DASHBOARD_SCORE_SCHEMA_VERSION = 5
LANE_FILTER_SESSION_KEY = "dashboard_active_lane_filter"
LANE_FILTER_LABELS = {
    "actionable": "可行动",
    "nearBuyZone": "接近击球区",
    "waitOrReview": "待确认",
    "noChaseHighRisk": "风险隔离",
}
DASHBOARD_BUY_ACTIONS = {"可小仓分批", "可正常分批"}
DASHBOARD_WAIT_ACTIONS = {"等回踩", "只观察", "财报后复核", "可小仓观察，待关键数据复核后再加仓", "待复核，暂不新增"}
DASHBOARD_BLOCKED_ACTIONS = {"禁止追高", "剔除", "数据不足，需复核", "待复核，暂不新增"}
DASHBOARD_NEAR_VALUATION_STATUSES = {"击球区附近", "回撤买点", "回撤后有吸引力", "合理偏便宜"}
DRAWER_SYMBOL_SESSION_KEY = "dashboard_drawer_symbol"
DRAWER_FOCUS_SESSION_KEY = "dashboard_drawer_focus"
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

    _render_market_strip(table)
    _render_summary_sections(table)
    _render_decision_table(table)
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
    command_cols = st.columns([0.72, 0.86, 2.95, 0.52], vertical_alignment="center")
    with command_cols[0]:
        if st.button("重新评分", width="stretch", help="不重新拉取数据，只基于当前缓存重新计算评分。", key="dashboard_recompute_score"):
            _clear_dashboard_table_cache()
            st.rerun()
    with command_cols[1]:
        if st.button("更新观察池", width="stretch", help="更新当前 watchlist 的数据，会逐只刷新。", key="dashboard_update_watchlist"):
            _clear_dashboard_table_cache()
            st.session_state["dashboard_force_fmp_refresh"] = True
            st.rerun()
    with command_cols[3]:
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
            if st.button("强制刷新 FMP 缓存", width="stretch", key="dashboard_force_refresh_fmp_cache"):
                _clear_dashboard_table_cache()
                st.session_state["dashboard_force_fmp_refresh"] = True
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
    price_cache = PriceCache()
    return pd.DataFrame(
        [_load_cached_dashboard_row(fundamental_cache, price_cache, ticker) for ticker in tickers]
    )


def _load_cached_dashboard_row(fundamental_cache: FundamentalCache, price_cache: PriceCache, ticker: str) -> dict:
    try:
        snapshot = fundamental_cache.get_snapshot(ticker, max_age_hours=24 * 3650)
        history = price_cache.get_history(f"FMP:{ticker}", max_age_hours=24 * 3650, min_rows=20)
        if snapshot is None and history is None:
            return _error_dashboard_row(ticker, RuntimeError("本地缓存暂无数据；点击“更新观察池”获取。"))

        snapshot = dict(snapshot or {"ticker": ticker, "symbol": ticker})
        snapshot.setdefault("ticker", ticker)
        snapshot.setdefault("symbol", ticker)
        snapshot["cache_note"] = "首页默认只读本地缓存；点击“更新观察池”才会联网刷新。"
        if history is None:
            history = _empty_price_history()

        technicals = latest_technical_snapshot(add_technical_indicators(history))
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
        history = add_technical_indicators(provider.get_price_history(ticker, force_refresh=force_refresh))
        technicals = latest_technical_snapshot(history)
        score = calculate_total_score(snapshot, technicals)
        data_quality = {"pct": score.data_quality_pct, "missing": score.missing_data}
        return _build_dashboard_row(ticker, snapshot, technicals, score, data_quality)
    except Exception as exc:
        return _error_dashboard_row(ticker, exc)


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


def _render_summary_sections(table: pd.DataFrame) -> None:
    summary_groups = _summary_lane_groups(table)

    st.markdown(
        '<section class="decision-terminal-head">'
        "<div><strong>决策台</strong><span>按行动优先级聚合当前观察池</span></div>"
        "</section>",
        unsafe_allow_html=True,
    )
    st.markdown(_dashboard_priority_strip_html(summary_groups), unsafe_allow_html=True)
    st.markdown('<div class="decision-lanes-marker"></div>', unsafe_allow_html=True)
    columns = st.columns(4, gap="small")
    for column, (lane_key, title, subtitle, rows, color) in zip(columns, summary_groups):
        with column:
            st.markdown(_summary_panel_head_html(title, subtitle, len(rows), color), unsafe_allow_html=True)
            st.markdown(_lane_stack_html(rows[:4]), unsafe_allow_html=True)
            if rows:
                _render_lane_more_button(lane_key)
    st.markdown('<div class="decision-lanes-end"></div>', unsafe_allow_html=True)


def _summary_lane_groups(table: pd.DataFrame) -> list[tuple[str, str, str, list[pd.Series], str]]:
    raw_groups = {
        "actionable": _actionable_rows(table),
        "nearBuyZone": _near_buy_zone_rows(table),
        "waitOrReview": _wait_or_confirm_rows(table),
        "noChaseHighRisk": _blocked_or_risky_rows(table),
    }
    priority = ["noChaseHighRisk", "actionable", "nearBuyZone", "waitOrReview"]
    assigned_symbols: set[str] = set()
    exclusive: dict[str, list[pd.Series]] = {key: [] for key in raw_groups}
    for lane_key in priority:
        for row in raw_groups[lane_key]:
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in assigned_symbols:
                continue
            assigned_symbols.add(symbol)
            exclusive[lane_key].append(row)
    return [
        ("actionable", "可行动", "可执行小仓或正常分批", exclusive["actionable"], "green"),
        ("nearBuyZone", "接近击球区", "回撤较深但仍需确认", exclusive["nearBuyZone"], "blue"),
        ("waitOrReview", "待确认", "等待更清晰的买点", exclusive["waitOrReview"], "yellow"),
        ("noChaseHighRisk", "风险隔离", "暂不新增，先看原因", exclusive["noChaseHighRisk"], "red"),
    ]


def _render_decision_table(table: pd.DataFrame) -> None:
    density = st.session_state.get("dashboard_density", "紧凑")
    table_class = "decision-table compact" if density == "紧凑" else "decision-table comfortable"
    st.markdown(
        '<section class="watchlist-head">'
        "<div><strong>观察名单</strong><span>主表只显示决策摘要，详细进入右侧面板</span></div>"
        "</section>",
        unsafe_allow_html=True,
    )
    st.markdown('<div id="watchlist-table"></div>', unsafe_allow_html=True)
    table = _filtered_table_for_active_lane(table)
    _render_active_lane_filter_status(table)
    header_html = "".join(_header_cell_html(definition["label"], definition.get("align")) for definition in WATCHLIST_COLUMNS)
    rows_html = "".join(_decision_table_row_html(row) for _, row in table.iterrows())
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


def _queue_stock_detail_drawer(symbol: str, focus: str | None = None) -> None:
    st.session_state[DRAWER_SYMBOL_SESSION_KEY] = str(symbol or "").upper()
    if focus:
        st.session_state[DRAWER_FOCUS_SESSION_KEY] = str(focus)
    else:
        st.session_state.pop(DRAWER_FOCUS_SESSION_KEY, None)


def _drawer_open_menu_html(symbol: str, label: str, focus: str | None = None) -> str:
    normalized_symbol = str(symbol or "").upper()
    safe_symbol = escape(normalized_symbol)
    focus_attr = f' data-dashboard-drawer-focus="{escape(focus)}"' if focus else ""
    focus_js = json.dumps(str(focus) if focus else None, ensure_ascii=False)
    onclick = (
        "event.preventDefault();event.stopPropagation();"
        f"if(window.__dashboardOpenDrawer){{window.__dashboardOpenDrawer({json.dumps(normalized_symbol, ensure_ascii=False)},{focus_js});}}"
        "return false;"
    )
    return (
        f'<button type="button" class="drawer-menu-link" data-dashboard-drawer-open="{safe_symbol}"{focus_attr} '
        f'onclick="{escape(onclick, quote=True)}" title="打开 {safe_symbol} 右侧详情面板">{escape(label)}</button>'
    )


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
        st.markdown(
            _score_card_html(
                "买点解释",
                str(row.get("entryRating") or "N/A"),
                [
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


def _render_client_stock_detail_drawers(table: pd.DataFrame) -> None:
    drawer_payload: dict[str, str] = {}
    for _, row in table.iterrows():
        symbol = str(row.get("symbol") or "").upper()
        if symbol:
            drawer_payload[symbol] = _drawer_html(row)
    if not drawer_payload:
        return
    auto_open_symbol = str(st.session_state.pop(DRAWER_SYMBOL_SESSION_KEY, "") or "").upper()
    auto_open_focus = st.session_state.pop(DRAWER_FOCUS_SESSION_KEY, None)
    components.html(
        f"""
        <script>
        (() => {{
          const win = window.parent;
          const doc = win.document;
          win.__dashboardDrawerPayload = {json.dumps(drawer_payload, ensure_ascii=False)};
          const autoOpenSymbol = {json.dumps(auto_open_symbol, ensure_ascii=False)};
          const autoOpenFocus = {json.dumps(auto_open_focus, ensure_ascii=False)};
          let root = doc.getElementById("dashboard-client-drawer-root");
          if (!root) {{
            root = doc.createElement("div");
            root.id = "dashboard-client-drawer-root";
            doc.body.appendChild(root);
          }}
          function closeDrawer() {{
            root.classList.remove("is-open");
            root.innerHTML = "";
            doc.body.classList.remove("dashboard-drawer-open");
          }}
          function showDrawerMessage(message) {{
            const note = root.querySelector("[data-dashboard-drawer-action-note]");
            if (!note) {{
              return;
            }}
            note.textContent = message || "";
            if (message) {{
              note.removeAttribute("hidden");
            }} else {{
              note.setAttribute("hidden", "hidden");
            }}
          }}
          function focusDrawerSection(focusKey) {{
            if (!focusKey) {{
              return;
            }}
            const target = root.querySelector(`[data-drawer-section="${{focusKey}}"]`);
            if (target && typeof target.scrollIntoView === "function") {{
              target.scrollIntoView({{behavior: "smooth", block: "start"}});
              target.classList.add("drawer-section-pulse");
              win.setTimeout(() => target.classList.remove("drawer-section-pulse"), 900);
            }}
          }}
          function openDrawer(symbol, focusKey) {{
            const key = String(symbol || "").toUpperCase();
            const html = win.__dashboardDrawerPayload && win.__dashboardDrawerPayload[key];
            if (!html) {{
              return;
            }}
            root.innerHTML = html;
            root.classList.add("is-open");
            doc.body.classList.add("dashboard-drawer-open");
            focusDrawerSection(focusKey);
          }}
          root.onclick = (event) => {{
            const rawTarget = event.target;
            const target = rawTarget instanceof win.Element ? rawTarget : rawTarget && rawTarget.parentElement;
            if (!(target instanceof win.Element)) {{
              return;
            }}
            const closer = target.closest("[data-dashboard-drawer-close]");
            const backdrop = target.closest(".drawer-backdrop");
            if (closer || backdrop) {{
              event.preventDefault();
              event.stopPropagation();
              closeDrawer();
              return;
            }}
            const messageAction = target.closest("[data-dashboard-drawer-message]");
            if (messageAction) {{
              event.preventDefault();
              event.stopPropagation();
              focusDrawerSection(messageAction.getAttribute("data-dashboard-drawer-focus"));
              showDrawerMessage(messageAction.getAttribute("data-dashboard-drawer-message"));
            }}
          }};
          win.__dashboardOpenDrawer = openDrawer;
          win.__dashboardCloseDrawer = closeDrawer;
          if (autoOpenSymbol) {{
            win.setTimeout(() => openDrawer(autoOpenSymbol, autoOpenFocus), 0);
          }}
          if (win.__dashboardDrawerClickHandler) {{
            doc.removeEventListener("click", win.__dashboardDrawerClickHandler, true);
          }}
          win.__dashboardDrawerClickHandler = (event) => {{
              const rawTarget = event.target;
              const target = rawTarget instanceof win.Element ? rawTarget : rawTarget && rawTarget.parentElement;
              if (!(target instanceof win.Element)) {{
                return;
              }}
              const opener = target.closest("[data-dashboard-drawer-open]");
              if (opener) {{
                event.preventDefault();
                event.stopPropagation();
                const open = win.__dashboardOpenDrawer || openDrawer;
                open(
                  opener.getAttribute("data-dashboard-drawer-open"),
                  opener.getAttribute("data-dashboard-drawer-focus")
                );
                return;
              }}
              const closer = target.closest("[data-dashboard-drawer-close]");
              if (closer) {{
                event.preventDefault();
                event.stopPropagation();
                closeDrawer();
                return;
              }}
              if (target.classList.contains("drawer-backdrop")) {{
                event.preventDefault();
                event.stopPropagation();
                closeDrawer();
                return;
              }}
              const messageAction = target.closest("[data-dashboard-drawer-message]");
              if (messageAction) {{
                event.preventDefault();
                event.stopPropagation();
                focusDrawerSection(messageAction.getAttribute("data-dashboard-drawer-focus"));
                showDrawerMessage(messageAction.getAttribute("data-dashboard-drawer-message"));
                return;
              }}
          }};
          doc.addEventListener("click", win.__dashboardDrawerClickHandler, true);
          if (!win.__dashboardDrawerKeydownBound) {{
            win.__dashboardDrawerKeydownBound = true;
            doc.addEventListener("keydown", (event) => {{
              if (event.key === "Escape") {{
                const close = win.__dashboardCloseDrawer || closeDrawer;
                close();
              }}
            }});
          }}
        }})();
        </script>
        """,
        height=0,
        width=0,
    )


def _render_stock_detail_drawer(table: pd.DataFrame) -> None:
    selected = st.session_state.get(DRAWER_SYMBOL_SESSION_KEY)
    if not selected:
        return
    matches = table[table["symbol"].astype(str) == str(selected)]
    if matches.empty:
        st.session_state.pop(DRAWER_SYMBOL_SESSION_KEY, None)
        return
    row = matches.iloc[0]
    st.markdown(_drawer_html(row), unsafe_allow_html=True)
    st.caption("右侧详情面板只做快速查看；数据补全和复核操作请进入专门页面执行，避免刷新总览。")


def _drawer_html(row: pd.Series) -> str:
    summary = row.get("humanReadableSummary")
    if not isinstance(summary, dict):
        summary = {}
    symbol = str(row.get("symbol") or "").upper()
    safe_symbol = escape(symbol)
    badges = [
        _badge_span_html(row.get("qualityRating"), _badge_color_for_cell("qualityRating", row.get("qualityRating"), row)),
        _badge_span_html(row.get("entryRating"), _badge_color_for_cell("entryRating", row.get("entryRating"), row)),
        _badge_span_html(row.get("riskRating"), _badge_color_for_cell("riskRating", row.get("riskRating"), row)),
        _badge_span_html(row.get("action"), _badge_color_for_cell("action", row.get("action"), row)),
    ]
    explanation_cards = [
        _drawer_card_html("公司质量解释", str(row.get("qualityRating") or "N/A"), [
            "主要加分：" + _translated_join(row.get("keyPositiveDrivers"), limit=4),
            "主要扣分：" + _translated_join(_quality_negative_items(row), limit=4),
            str(summary.get("quality") or ""),
        ]),
        _drawer_card_html("买点解释", str(row.get("entryRating") or "N/A"), [
            str(summary.get("valuation") or ""),
            str(summary.get("technical") or ""),
            str(summary.get("entry") or ""),
            _entry_context_note(row),
        ]),
        _drawer_card_html("风险解释", str(row.get("riskRating") or "N/A"), [
            "风险来源：" + _translated_join(_risk_items(row), limit=4),
            str(summary.get("risk") or ""),
            _risk_context_note(row),
        ]),
    ]
    return (
        '<div class="drawer-backdrop"></div>'
        '<aside class="stock-drawer">'
        '<a class="drawer-close-link" href="#" data-dashboard-drawer-close="1" title="关闭右侧详情面板">×</a>'
        '<div class="drawer-topline">右侧详情面板</div>'
        '<div class="drawer-head">'
        '<div>'
        f'<div class="drawer-symbol">{safe_symbol}</div>'
        f'<div class="drawer-company">{escape(str(row.get("companyName") or "公司名待补充"))}</div>'
        '</div>'
        f'<div class="drawer-price">{escape(str(row.get("price") or "N/A"))}</div>'
        '</div>'
        '<div class="drawer-meta-grid">'
        f'<span>模型：{escape(model_type_label(row.get("modelType")))}</span>'
        f'<span>市值：{escape(str(row.get("marketCap") or "N/A"))}</span>'
        f'<span>当前新增：{escape(str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A"))}</span>'
        f'<span>数据：{escape(str(row.get("dataStatus") or "N/A"))}</span>'
        '</div>'
        f'<div class="drawer-badges">{"".join(badges)}</div>'
        f'{_drawer_decision_summary_html(row)}'
        f'{_drawer_position_guidance_html(row)}'
        f'<div class="drawer-section">{"".join(explanation_cards)}</div>'
        f'{_drawer_industry_metrics_html(row)}'
        '<div class="drawer-section-title">数据复核状态</div>'
        f'{_drawer_review_summary_html(row)}'
        '<div data-drawer-section="resolution">'
        '<div class="drawer-section-title">数据补全状态</div>'
        f'{_drawer_resolution_html(row)}'
        '</div>'
        '<details class="drawer-raw"><summary>原始指标</summary>'
        f'{_drawer_raw_metrics_html(row)}'
        '</details>'
        '</aside>'
    )


def _drawer_decision_summary_html(row: pd.Series) -> str:
    symbol = str(row.get("symbol") or "该股票")
    model = model_type_label(row.get("modelType"))
    action = str(row.get("action") or "只观察")
    quality = str(row.get("qualityRating") or "N/A")
    entry = str(row.get("entryRating") or "N/A")
    risk = str(row.get("riskRating") or "N/A")
    summary = row.get("humanReadableSummary")
    if not isinstance(summary, dict):
        summary = {}
    conclusion = _decision_conclusion_text(row, symbol, model, action)
    why = _decision_why_text(row, quality, entry, risk, summary)
    wait_items = "".join(f"<li>{escape(item)}</li>" for item in _waiting_conditions(row))
    return (
        '<div class="drawer-decision-card">'
        '<div class="drawer-card-title">当前结论</div>'
        f'<div class="drawer-decision-headline">{escape(conclusion)}</div>'
        f'<p>{escape(why)}</p>'
        '<div class="drawer-decision-grid">'
        f'<span><b>当前新增建议</b><strong>{escape(str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A"))}</strong></span>'
        f'<span><b>组合仓位上限</b><strong>{escape(str(row.get("maxPortfolioWeight") or "N/A"))}</strong></span>'
        '</div>'
        '<div class="drawer-waiting"><b>等待条件</b><ul>'
        f'{wait_items or "<li>等待估值、趋势或关键经营数据进一步确认。</li>"}'
        '</ul></div>'
        '</div>'
    )


def _drawer_position_guidance_html(row: pd.Series) -> str:
    return (
        '<div class="drawer-position-card" data-drawer-section="position">'
        '<div>'
        '<span>当前新增建议</span>'
        f'<strong>{escape(str(row.get("currentAddLimit") or row.get("maxSuggestedPosition") or "N/A"))}</strong>'
        '<em>由买点评级、估值位置和当前趋势决定。</em>'
        '</div>'
        '<div>'
        '<span>组合仓位上限</span>'
        f'<strong>{escape(str(row.get("maxPortfolioWeight") or "N/A"))}</strong>'
        '<em>由公司质量和基本面风险决定。</em>'
        '</div>'
        '</div>'
    )


def _drawer_industry_metrics_html(row: pd.Series) -> str:
    model_type = str(row.get("modelType") or "")
    if model_type != "MEGA_CAP_PLATFORM":
        return ""
    resolution_rows = row.get("metricResolutionStatus")
    rows = [item for item in resolution_rows if isinstance(item, dict)] if isinstance(resolution_rows, (list, tuple)) else []
    definitions = [
        ("cloudRevenueGrowth", "云业务增长", "需抓取IR / 8-K", "关注云业务收入增速和订单延续性。"),
        ("azureCloudGrowth", "Azure / Cloud 增速", "需抓取IR / 8-K", "关注 Azure 或核心云业务增速。"),
        ("aiCapexOverbuildRisk", "AI资本开支压力", "规则推导", "基于 Capex、FCF 和 AI 投入压力推导。"),
        ("fcfMargin", "FCF转化率", "已计算", "优先使用自由现金流 / 收入。"),
        ("buybackDiscipline", "回购纪律", "规则推导", "基于回购金额、FCF覆盖和股本变化推导。"),
        ("segmentStrength", "分部业务强度", "规则推导", "基于分部增长、利润率和平台业务表现推导。"),
        ("regulatoryRisk", "监管风险", "半自动低置信度", "监管/反垄断风险需保留复核。"),
    ]
    items = []
    for key, label, fallback_status, fallback_explanation in definitions:
        item = _find_metric_resolution(rows, key)
        if item:
            status = resolution_status_label(item.get("resolutionStatus"))
            value = _resolution_value_text(item).split("｜", 1)[0]
            explanation = _clean_resolution_explanation(str(item.get("explanation") or fallback_explanation))
        else:
            status = fallback_status
            value = "待补齐" if "抓取" in fallback_status else "规则推导"
            explanation = fallback_explanation
        items.append(
            "<li>"
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            f"<em>{escape(status)}｜{escape(explanation)}</em>"
            "</li>"
        )
    return (
        '<div class="drawer-section-title">行业专属指标</div>'
        '<div class="drawer-industry-card"><ul>'
        f'{"".join(items)}'
        '</ul></div>'
    )


def _find_metric_resolution(rows: list[dict[str, object]], metric_key: str) -> dict[str, object] | None:
    target = metric_key.lower()
    for item in rows:
        key = str(item.get("metricKey") or "").lower()
        display = str(item.get("displayName") or "").lower()
        if key == target:
            return item
        if target == "azurecloudgrowth" and ("azure" in display or "cloud growth" in display):
            return item
        if target == "cloudrevenuegrowth" and "cloud revenue" in display:
            return item
    return None


def _decision_conclusion_text(row: pd.Series, symbol: str, model: str, action: str) -> str:
    quality = str(row.get("qualityRating") or "")
    risk = str(row.get("riskRating") or "")
    entry = str(row.get("entryRating") or "")
    if _is_high_quality_text(quality) and risk == "低" and _is_observe_or_wait_action(action, entry):
        return f"{symbol} 是高质量{model}，但当前买点一般，适合{_short_action_for_sentence(action)}。"
    if "数据" in str(row.get("dataStatus") or "") or row.get("dataConfidence") == "low":
        return f"{symbol} 的评分仍受数据置信度限制，先复核关键数据再提高仓位。"
    return f"{symbol} 当前动作是{_short_action_for_sentence(action)}，先按仓位纪律执行。"


def _decision_why_text(row: pd.Series, quality: str, entry: str, risk: str, summary: dict[str, str]) -> str:
    action = str(row.get("action") or "")
    if _is_high_quality_text(quality) and risk == "低" and _is_observe_or_wait_action(action, entry):
        return f"公司风险低，但买点评级为 {entry}，当前新增仓位受限；这不是公司质量问题，而是买点不够理想。"
    parts = [str(summary.get("entry") or ""), str(summary.get("technical") or ""), str(summary.get("valuation") or "")]
    text = " ".join(part for part in parts if part).strip()
    return text or "当前建议由质量、买点、风险、估值和数据置信度综合得出。"


def _waiting_conditions(row: pd.Series) -> list[str]:
    model_type = str(row.get("modelType") or "")
    action = str(row.get("action") or "")
    items: list[str] = []
    if _is_observe_or_wait_action(action, str(row.get("entryRating") or "")):
        items.append("重新站上 EMA200 或趋势明显修复")
        items.append("估值回落到更舒服区间")
    if model_type == "MEGA_CAP_PLATFORM":
        items.append("Azure / Cloud / AI 收入继续兑现")
    if row.get("dataConfidence") in {"low", "medium"}:
        items.append("关键经营指标和复核队列完成确认")
    return _dedupe_text(items)[:4]


def _entry_context_note(row: pd.Series) -> str:
    action = str(row.get("action") or "")
    if _is_high_quality_text(str(row.get("qualityRating") or "")) and _is_observe_or_wait_action(action, str(row.get("entryRating") or "")):
        return "只观察不是因为公司质量差，而是因为当前买点不够理想。"
    return ""


def _risk_context_note(row: pd.Series) -> str:
    if str(row.get("riskRating") or "") == "低":
        return "风险评级低代表公司基本面风险较低，不等于当前价格值得追。"
    return ""


def _is_high_quality_text(value: str) -> bool:
    return value.startswith(("A+", "A", "A-", "B+"))


def _is_observe_or_wait_action(action: str, entry: str) -> bool:
    return any(token in action for token in ("只观察", "等回踩", "待复核")) or entry.startswith(("B", "C", "D"))


def _short_action_for_sentence(action: str) -> str:
    if "等回踩" in action:
        return "等回踩"
    if "小仓" in action:
        return "小仓观察"
    if "分批" in action:
        return "分批执行"
    if "禁止" in action:
        return "禁止追高"
    if "复核" in action:
        return "待复核"
    return action or "只观察"


def _drawer_card_html(title: str, headline: str, lines: list[str]) -> str:
    items = "".join(f"<li>{escape(str(line))}</li>" for line in lines if line)
    return (
        '<div class="drawer-card">'
        f'<div class="drawer-card-title">{escape(title)}</div>'
        f'<div class="drawer-card-headline">{escape(str(headline))}</div>'
        f"<ul>{items}</ul>"
        "</div>"
    )


def _drawer_resolution_html(row: pd.Series) -> str:
    groups = _metric_resolution_groups(row.get("metricResolutionStatus"))
    if not groups:
        return '<div class="drawer-muted">暂无补全状态</div>'
    blocks: list[str] = []
    key_items = groups.get("关键待补齐", [])
    if key_items:
        rows = "".join(_drawer_actionable_resolution_row(item) for item in key_items[:6])
        blocks.append(f'<div class="drawer-resolution priority-high"><b>关键待补齐</b><ul>{rows}</ul></div>')

    auto_items = groups.get("可自动补齐", [])
    if auto_items:
        rows = "".join(_drawer_actionable_resolution_row(item) for item in auto_items[:6])
        blocks.append(f'<div class="drawer-resolution"><b>可自动补齐</b><ul>{rows}</ul></div>')

    calculated_items = groups.get("已计算摘要", [])
    if calculated_items:
        rows = "".join(_drawer_calculated_resolution_row(item) for item in calculated_items[:5])
        extra = ""
        if len(calculated_items) > 5:
            extra_rows = "".join(_drawer_calculated_resolution_row(item) for item in calculated_items[5:])
            extra = f'<details class="drawer-low-priority"><summary>展开全部已计算指标</summary><ul>{extra_rows}</ul></details>'
        blocks.append(f'<div class="drawer-resolution"><b>已计算摘要</b><ul>{rows}</ul>{extra}</div>')

    low_items = groups.get("低优先级 / 仅解释项", [])
    if low_items:
        rows = "".join(_drawer_low_priority_resolution_row(item) for item in low_items[:12])
        blocks.append(
            '<details class="drawer-resolution drawer-low-priority">'
            '<summary>低优先级 / 仅解释项</summary>'
            f'<ul>{rows}</ul>'
            '</details>'
        )
    return "".join(blocks) if blocks else '<div class="drawer-muted">暂无关键待补齐项。</div>'


def _drawer_review_summary_html(row: pd.Series) -> str:
    summary = row.get("reviewQueueSummary")
    if not isinstance(summary, dict) or not summary.get("total"):
        summary = row.get("disclosureReviewSummary")
    if not isinstance(summary, dict) or not summary.get("total"):
        return '<div class="drawer-muted">当前无待复核的自动抽取数据。部分指标仍可通过规则推导或人工补充完善。</div>'
    primary_items = [
        ("待确认", summary.get("pending_review", 0)),
        ("需要补齐", summary.get("needs_data", 0)),
        ("低置信度推导", summary.get("derived_low_confidence", 0)),
        ("定性风险", summary.get("qualitative_risk", 0)),
        ("AI建议修正", summary.get("ai_recommend_correct", 0)),
        ("AI建议驳回", summary.get("ai_recommend_reject", 0)),
        ("证据不足", summary.get("ai_not_enough_evidence", 0)),
    ]
    all_items = [
        *primary_items,
        ("已确认", summary.get("approved", 0)),
        ("已驳回", summary.get("rejected", 0)),
        ("AI自动确认", summary.get("auto_approved_by_ai", 0)),
        ("需要人工判断", summary.get("ai_needs_human_review", 0)),
    ]
    visible_items = [(label, int(value or 0)) for label, value in primary_items if int(value or 0) > 0]
    if not visible_items:
        visible_items = [("暂无关键复核项", 0)]
    counts = "".join(f"<li><span>{escape(label)}</span><strong>{value}</strong></li>" for label, value in visible_items)
    all_counts = "".join(f"<li><span>{escape(label)}</span><strong>{int(value or 0)}</strong></li>" for label, value in all_items)
    critical = row.get("criticalPendingReviewMetrics")
    note = ""
    if isinstance(critical, (list, tuple, set)) and [item for item in critical if item]:
        note = "<p>有关键数据待复核，评分置信度受限。</p>"
    if int(summary.get("ai_recommend_correct") or 0) or int(summary.get("ai_recommend_reject") or 0):
        note += "<p>存在AI标记的异常数据，评分需复核。</p>"
    action_bar = _drawer_review_action_bar_html(str(row.get("symbol") or ""))
    return (
        '<div class="drawer-review-summary" data-drawer-section="review">'
        f'<ul>{counts}</ul>{note}'
        '<details class="drawer-low-priority"><summary>展开全部状态</summary>'
        f'<ul>{all_counts}</ul>'
        '</details>'
        f'{action_bar}'
        '</div>'
    )


def _drawer_review_action_bar_html(symbol: str) -> str:
    safe_symbol = escape(str(symbol or "").upper())
    review_message = f"{safe_symbol} 的复核摘要已在当前面板中展示；如需批量处理，请在左侧进入数据复核中心的一键自动处理。"
    sync_message = f"{safe_symbol} 的复核队列同步属于后台批量动作，已保留在数据复核中心执行，当前不会切走总览。"
    fill_message = f"{safe_symbol} 的自动补全建议已在数据补全状态中展示；批量抓取 SEC / IR 建议在数据复核中心统一运行。"
    return (
        '<div class="drawer-review-actions">'
        '<button type="button" data-dashboard-drawer-focus="review" data-dashboard-drawer-message="'
        f'{escape(review_message)}">查看复核项</button>'
        '<button type="button" data-dashboard-drawer-message="'
        f'{escape(sync_message)}">同步复核队列</button>'
        '<button type="button" class="primary" data-dashboard-drawer-focus="resolution" data-dashboard-drawer-message="'
        f'{escape(fill_message)}">自动补全数据</button>'
        '<div class="drawer-action-note" data-dashboard-drawer-action-note hidden></div>'
        '</div>'
    )


def _drawer_raw_metrics_html(row: pd.Series) -> str:
    blocks = []
    for group_name, metrics in DETAIL_GROUPS:
        items = "".join(
            f'<li><span>{escape(label)}</span><strong>{escape(str(row.get(key, "N/A")))}</strong></li>'
            for key, label in metrics
            if not (key == "fcfMargin" and row.get(key) == "N/A")
        )
        blocks.append(f'<div class="drawer-metric-group"><b>{escape(group_name)}</b><ul>{items}</ul></div>')
    return "".join(blocks)


def _build_dashboard_row(ticker: str, snapshot: dict, technicals: dict, score, data_quality: dict) -> dict:
    high_risk_flags = sum(1 for flag in score.risk_flags if flag.severity == "high")
    medium_risk_flags = sum(1 for flag in score.risk_flags if flag.severity == "medium")
    anti_fomo = _signal_message(score.trading_signals, "anti_fomo")
    left_side_opportunity = _signal_message(score.trading_signals, "left_side_opportunity")
    price = _first_present(technicals.get("price"), snapshot.get("current_price"))
    fcf_metric = fcf_margin_metric(snapshot)
    direct_fcf_margin = fcf_metric.value if fcf_metric.sourceType != "derivedFromMarket" else None
    implied_fcf_margin = fcf_metric.value if fcf_metric.sourceType == "derivedFromMarket" else None
    final_decision = _derive_dashboard_final_decision(ticker, snapshot, technicals, score)
    current_add_limit = final_decision.currentAddLimitPercent
    max_portfolio_weight = final_decision.maxPortfolioWeightPercent

    return {
        "symbol": ticker,
        "companyName": snapshot.get("company_name") or snapshot.get("companyName") or "",
        "rawSnapshot": snapshot,
        "rawTechnicals": technicals,
        "modelType": score.scoring_model,
        "price": format_currency(price),
        "marketCap": _format_billions(snapshot.get("market_cap")),
        "drawdownFromHigh": format_percent(technicals.get("drawdown_from_high_pct")),
        "qualityRating": score.quality_rating,
        "entryRating": score.entry_rating,
        "riskRating": score.risk_rating,
        "valuationStatus": score.valuation_status,
        "action": score.action,
        "finalAction": final_decision.finalAction,
        "decisionLane": final_decision.decisionLane,
        "displayCategory": final_decision.displayCategory,
        "isActionable": final_decision.isActionable,
        "decisionBlockReasons": final_decision.blockReasons,
        "decisionReviewReasons": final_decision.reviewReasons,
        "scoreCurrentAddLimitPercent": getattr(score, "current_add_limit_percent", score.max_suggested_position_percent),
        "scoreMaxPortfolioWeightPercent": getattr(score, "max_portfolio_weight_percent", None),
        "maxSuggestedPositionPercent": score.max_suggested_position_percent,
        "maxPortfolioWeightPercent": max_portfolio_weight,
        "currentAddLimitPercent": current_add_limit,
        "maxSuggestedPosition": _position_limit_text(current_add_limit),
        "maxPortfolioWeight": _portfolio_weight_text(max_portfolio_weight),
        "currentAddLimit": _position_limit_text(current_add_limit),
        "dataConfidence": final_decision.dataConfidence,
        "proxyConfidence": score.proxy_confidence,
        "dataStatus": _data_status_label(score),
        "missingIndustryMetrics": score.missing_industry_metrics or [],
        "proxyMetricsUsed": score.proxy_metrics_used or [],
        "missingMetricImpact": getattr(score, "missing_metric_impacts", None) or [],
        "metricResolutionStatus": getattr(score, "metric_resolution_statuses", None) or [],
        "reviewQueueSummary": ReviewQueueStore().summary(ticker),
        "disclosureReviewSummary": snapshot.get("disclosureReviewSummary") or {},
        "criticalPendingReviewMetrics": snapshot.get("criticalPendingReviewMetrics") or [],
        "humanReadableSummary": getattr(score, "human_readable_summary", None) or {},
        "activeRiskDrivers": getattr(score, "active_risk_drivers", None) or getattr(score, "activeRiskDrivers", None) or [],
        "missingDataExplanation": getattr(score, "missing_data_explanation", None) or [],
        "ratingCap": getattr(score, "rating_cap", None),
        "keyPositiveDrivers": score.key_positives or [],
        "keyNegativeDrivers": score.key_risks or [],
        "trailingPe": format_multiple(snapshot.get("trailing_pe")),
        "forwardPe": format_multiple(snapshot.get("forward_pe")),
        "priceToSales": format_multiple(snapshot.get("price_to_sales")),
        "enterpriseToRevenue": format_multiple(snapshot.get("enterprise_to_revenue")),
        "priceToFcf": format_multiple(snapshot.get("price_to_fcf")),
        "freeCashFlowYield": format_percent(snapshot.get("free_cash_flow_yield"), already_percent=False),
        "revenueGrowth": format_percent(snapshot.get("revenue_growth"), already_percent=False),
        "operatingMargin": format_percent(snapshot.get("operating_margin"), already_percent=False),
        "returnOnInvestedCapital": format_percent(snapshot.get("return_on_invested_capital"), already_percent=False),
        "fcfMargin": format_percent(fcf_metric.value, already_percent=False),
        "directFcfMargin": format_percent(direct_fcf_margin, already_percent=False),
        "impliedFcfMargin": format_percent(implied_fcf_margin, already_percent=False),
        "fcfMarginLabel": "估算FCF利润率" if fcf_metric.sourceType == "derivedFromMarket" else "FCF利润率",
        "fcfMarginSourceType": fcf_metric.sourceType,
        "fcfMarginNote": fcf_margin_source_note(snapshot),
        "netDebtToEbitda": format_multiple(snapshot.get("net_debt_to_ebitda")),
        "currentRatio": format_multiple(snapshot.get("current_ratio")),
        "rsi14": _format_plain_number(technicals.get("rsi14")),
        "ema20": format_currency(technicals.get("ema20")),
        "ema50": format_currency(technicals.get("ema50")),
        "ema200": format_currency(technicals.get("ema200")),
        "gain20d": format_percent(technicals.get("gain_20d_pct")),
        "gain60d": format_percent(technicals.get("gain_60d_pct")),
        "dailyReturn": format_percent(technicals.get("daily_return_pct")),
        "priceVsEma20": format_percent(technicals.get("pct_above_ema20")),
        "priceVsEma50": format_percent(technicals.get("pct_above_ema50")),
        "fiftyTwoWeekHigh": format_currency(_first_present(technicals.get("fifty_two_week_high"), snapshot.get("fifty_two_week_high"))),
        "fiftyTwoWeekLow": format_currency(_first_present(technicals.get("fifty_two_week_low"), snapshot.get("fifty_two_week_low"))),
        "totalScore": score.total_score,
        "valueZone": score.value_zone,
        "rating": score.rating,
        "antiFomo": bool(anti_fomo),
        "leftSideOpportunity": bool(left_side_opportunity),
        "riskFlagCount": high_risk_flags + medium_risk_flags,
        "highRiskFlagCount": high_risk_flags,
        "dataQualityPct": data_quality["pct"],
        "dataNote": _data_note(snapshot, data_quality, score),
        "overheatScore": score.overheat_score,
        "overheatStatus": score.overheat_status,
        "overheatAction": score.overheat_action,
        "overheatRecommendation": score.overheat_recommendation,
        "overheatReasons": score.overheat_reasons or [],
    }


def _derive_dashboard_final_decision(ticker: str, snapshot: dict, technicals: dict, score):
    try:
        stock_data = {**snapshot, **technicals}
        price = _first_present(technicals.get("price"), snapshot.get("current_price"))
        if price is not None:
            stock_data["price"] = price
            stock_data.setdefault("current_price", price)
        buy_zone = generate_buy_zone(ticker, stock_data, score, score.scoring_model)
        plan = StockPlanStore().get_plan(ticker)
        return build_final_decision_bundle(score, buy_zone, manual_plan_override=plan, symbol=ticker)
    except Exception:
        return build_final_decision_bundle(score)


def _error_dashboard_row(ticker: str, exc: Exception) -> dict:
    row = {
        "symbol": ticker,
        "companyName": "",
        "price": "N/A",
        "marketCap": "N/A",
        "drawdownFromHigh": "N/A",
        "qualityRating": "数据不足",
        "entryRating": "数据不足",
        "riskRating": "数据不足",
        "valuationStatus": "数据不足",
        "action": "数据不足，需复核",
        "finalAction": "数据不足，需复核",
        "decisionLane": "review",
        "displayCategory": "需复核",
        "isActionable": False,
        "decisionBlockReasons": ["data_unavailable"],
        "decisionReviewReasons": [],
        "scoreCurrentAddLimitPercent": 0,
        "scoreMaxPortfolioWeightPercent": 0,
        "maxSuggestedPositionPercent": 0,
        "maxSuggestedPosition": "不建议新增",
        "maxPortfolioWeightPercent": 0,
        "currentAddLimitPercent": 0,
        "maxPortfolioWeight": "不建议配置",
        "currentAddLimit": "不建议新增",
        "dataConfidence": "low",
        "proxyConfidence": "low",
        "dataStatus": "数据不足",
        "missingIndustryMetrics": [],
        "proxyMetricsUsed": [],
        "missingMetricImpact": [],
        "metricResolutionStatus": [],
        "humanReadableSummary": {},
        "missingDataExplanation": [],
        "ratingCap": None,
        "keyPositiveDrivers": [],
        "keyNegativeDrivers": [str(exc)],
        "totalScore": 0,
        "valueZone": "数据不可用",
        "rating": "需要数据",
        "antiFomo": False,
        "leftSideOpportunity": False,
        "riskFlagCount": 0,
        "highRiskFlagCount": 0,
        "dataQualityPct": 0,
        "dataNote": str(exc),
        "fcfMarginNote": "",
        "overheatScore": 0,
        "overheatStatus": "数据不足",
        "overheatAction": "数据不足，需复核",
        "overheatRecommendation": "先补齐数据",
        "overheatReasons": [str(exc)],
    }
    for _, metrics in DETAIL_GROUPS:
        for key, _ in metrics:
            row[key] = "N/A"
    return row


def _signal_message(signals, kind: str) -> str:
    for signal in signals:
        if signal.kind == kind:
            return signal.message
    return ""


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


def _row_value(row: pd.Series, key: str) -> object | None:
    value = row.get(key)
    if _is_missing(value):
        return None
    return value


def _row_final_action(row: pd.Series) -> str:
    return str(_row_value(row, "finalAction") or _row_value(row, "action") or "")


def _row_decision_lane(row: pd.Series) -> str:
    lane = str(_row_value(row, "decisionLane") or "")
    if lane:
        return lane
    action = _row_final_action(row)
    if _row_is_actionable(row):
        return "actionable"
    if action in DASHBOARD_BLOCKED_ACTIONS:
        return "blocked"
    if action in DASHBOARD_WAIT_ACTIONS:
        return "wait"
    return ""


def _row_is_actionable(row: pd.Series) -> bool:
    explicit = _row_value(row, "isActionable")
    if explicit is not None:
        explicit_text = str(explicit).lower()
        if explicit_text in {"true", "false"}:
            return explicit_text == "true"
    return _row_final_action(row) in DASHBOARD_BUY_ACTIONS and row.get("dataConfidence") in {"medium", "high"}


def _row_current_add_text(row: pd.Series) -> str:
    return str(_row_value(row, "currentAddLimit") or _row_value(row, "maxSuggestedPosition") or "")


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


def _actionable_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if _row_is_actionable(row)
    ]
    return sorted(rows, key=lambda row: row.get("totalScore", 0), reverse=True)


def _near_buy_zone_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if _row_decision_lane(row) == "nearBuyZone"
        or (
            _row_decision_lane(row) in {"", "wait"}
            and row.get("valuationStatus") in DASHBOARD_NEAR_VALUATION_STATUSES
            and _row_final_action(row) not in {"可正常分批", "禁止追高", "剔除"}
        )
    ]
    return sorted(rows, key=lambda row: row.get("totalScore", 0), reverse=True)


def _wait_or_confirm_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if _row_decision_lane(row) in {"wait", "review"}
        or (
            not _row_value(row, "decisionLane")
            and _row_final_action(row) in DASHBOARD_WAIT_ACTIONS
        )
    ]
    return sorted(rows, key=lambda row: row.get("totalScore", 0), reverse=True)


def _blocked_or_risky_rows(table: pd.DataFrame) -> list[pd.Series]:
    rows = [
        row
        for _, row in table.iterrows()
        if _row_decision_lane(row) == "blocked"
        or _row_final_action(row) in DASHBOARD_BLOCKED_ACTIONS
        or row.get("riskRating") in {"高", "高风险"}
        or _numeric(row.get("overheatScore")) >= 60
        or row.get("highRiskFlagCount", 0) > 0
    ]
    return sorted(rows, key=lambda row: (_numeric(row.get("overheatScore")), row.get("highRiskFlagCount", 0)), reverse=True)


def _lane_filter_rows(table: pd.DataFrame, lane_key: str) -> list[pd.Series]:
    for current_key, _title, _subtitle, rows, _color in _summary_lane_groups(table):
        if lane_key == current_key:
            return rows
    return [row for _, row in table.iterrows()]


def _filtered_table_for_active_lane(table: pd.DataFrame) -> pd.DataFrame:
    lane_key = str(st.session_state.get(LANE_FILTER_SESSION_KEY) or "")
    if lane_key not in LANE_FILTER_LABELS or "symbol" not in table.columns:
        return table
    symbols = {str(row.get("symbol") or "").upper() for row in _lane_filter_rows(table, lane_key)}
    if not symbols:
        return table.iloc[0:0].copy()
    return table[table["symbol"].astype(str).str.upper().isin(symbols)].copy()


def _render_active_lane_filter_status(filtered_table: pd.DataFrame) -> None:
    lane_key = str(st.session_state.get(LANE_FILTER_SESSION_KEY) or "")
    label = LANE_FILTER_LABELS.get(lane_key)
    if not label:
        return
    left, _spacer, clear = st.columns([0.16, 0.78, 0.06], gap="small", vertical_alignment="top")
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
    if "只观察" in combined or "观察" in combined or "待复核" in combined or combined.startswith("C"):
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


def _dashboard_priority_strip_html(summary_groups: list[tuple[str, str, str, list[pd.Series], str]]) -> str:
    items: list[str] = []
    seen_symbols: set[str] = set()
    for lane_key, _title, _subtitle, rows, color in summary_groups:
        added_for_lane = 0
        for row in rows:
            if len(items) >= 5:
                break
            if added_for_lane >= 2:
                break
            symbol = str(row.get("symbol") or "").upper().strip()
            if not symbol or symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            items.append(_dashboard_priority_item_html(lane_key, row, color))
            added_for_lane += 1
        if len(items) >= 5:
            break
    if items:
        body = "".join(items)
    else:
        body = '<div class="dashboard-priority-empty">暂无明确可执行机会，优先等待回踩或复核数据。</div>'
    return (
        '<section class="dashboard-priority-strip">'
        '<div class="dashboard-priority-head"><strong>今日重点</strong><span>最多 5 项</span></div>'
        f'<div class="dashboard-priority-list">{body}</div>'
        "</section>"
    )


def _dashboard_priority_item_html(lane_key: str, row: pd.Series, color: str) -> str:
    label = _dashboard_priority_label(lane_key, row)
    symbol = str(row.get("symbol") or "").upper()
    safe_symbol = escape(symbol)
    action = _short_badge_text(_row_final_action(row) or row.get("valuationStatus") or "只观察")
    reason = _lane_short_reason(_lane_full_reason(row))
    return (
        f'<a class="dashboard-priority-row tone-{escape(color)}" href="?page=detail&symbol={safe_symbol}" target="_self" '
        f'aria-label="打开 {safe_symbol} 个股研究" '
        f'title="{escape(label)} · {safe_symbol} · {escape(str(action))} · {escape(reason)}">'
        f'<span class="dashboard-priority-status {escape(color)}" title="{escape(label)}"><i></i></span>'
        f'<strong>{safe_symbol}</strong>'
        f'<span>{escape(str(action))}</span>'
        f'<em>{escape(reason)}</em>'
        "</a>"
    )


def _dashboard_priority_label(lane_key: str, row: pd.Series) -> str:
    if lane_key == "actionable":
        return "可行动"
    if lane_key == "nearBuyZone":
        return "接近"
    if lane_key == "waitOrReview":
        action = _row_final_action(row)
        if "复核" in action or row.get("dataConfidence") == "low":
            return "复核"
        return "等待"
    if lane_key == "noChaseHighRisk":
        action = _row_final_action(row)
        if "数据" in action or row.get("dataConfidence") == "low":
            return "复核"
        return "风险"
    return "观察"


def _summary_panel_head_html(title: object, subtitle: object, count: int, color: str) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return (
        f'<div class="summary-panel-head tone-{escape(color)}">'
        "<div>"
        f'<div class="summary-panel-title">{escape(str(title))}</div>'
        f'<div class="summary-panel-subtitle">{escape(str(subtitle))}</div>'
        "</div>"
        f'<span class="summary-count" style="background:{background};color:{foreground};border:1px solid {border};">{count}</span>'
        "</div>"
    )


def _render_lane_more_button(lane_key: str) -> None:
    if st.button(_lane_more_label(), key=f"dashboard_lane_more_{lane_key}", width="stretch", help="筛选主表显示该分组"):
        st.session_state[LANE_FILTER_SESSION_KEY] = str(lane_key)


def _lane_more_label() -> str:
    return "查看全部"


def _lane_more_html(lane_key: str, hidden_count: int) -> str:
    label = LANE_FILTER_LABELS.get(str(lane_key), "该分组")
    legacy_label = f"还有 {int(hidden_count)} 只 · 查看全部"
    return (
        f'<span class="lane-more" title="原地聚焦主表：{escape(label)}" aria-label="{escape(legacy_label)}">'
        f"<span>+{int(hidden_count)} 未显示</span><b>查看全部</b>"
        "</span>"
    )


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
            --dash-bg: #F7F8FA;
            --dash-surface: #FFFFFF;
            --dash-surface-muted: #F3F4F6;
            --dash-border: #E5E7EB;
            --dash-text: #111827;
            --dash-secondary: #6B7280;
            --dash-muted: #9CA3AF;
        }
        .terminal-header,
        .terminal-title-group {
            max-width: 1440px;
            margin: 0 auto 0.55rem;
            padding: 0.65rem 0 0.6rem;
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
            margin: 0.35rem 0 0.65rem;
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
            font-size: 1.58rem;
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
        .st-key-dashboard_recompute_score button,
        .st-key-dashboard_update_watchlist button {
            min-height: 36px !important;
            height: 36px !important;
            border-radius: 10px !important;
            padding: 0 0.9rem !important;
            font-size: 0.84rem !important;
            font-weight: 720 !important;
            box-shadow: none !important;
        }
        .st-key-dashboard_recompute_score button {
            background: rgba(255,255,255,0.92) !important;
            color: var(--dash-text) !important;
            border: 1px solid var(--dash-border) !important;
        }
        .st-key-dashboard_update_watchlist button {
            background: #1F2937 !important;
            color: #FFFFFF !important;
            border: 1px solid #1F2937 !important;
        }
        .st-key-dashboard_update_watchlist button:hover {
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
            max-width: 1440px;
            margin: 0.65rem auto 0.85rem;
            border: 1px solid var(--dash-border);
            border-radius: 0.7rem;
            background: rgba(255,255,255,0.84);
            box-shadow: 0 14px 36px rgba(15, 23, 42, 0.05);
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
        .st-key-dashboard_clear_lane_filter {
            margin-top: 0.34rem;
            margin-bottom: 0.34rem;
        }
        .st-key-dashboard_clear_lane_filter button {
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
        .st-key-dashboard_clear_lane_filter button p {
            font-size: 11px !important;
            font-weight: 620 !important;
            line-height: 26px !important;
            margin: 0;
            white-space: nowrap !important;
            word-break: keep-all !important;
        }
        .st-key-dashboard_clear_lane_filter button:hover {
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
        .drawer-decision-card {
            margin: 0.78rem 0;
            padding: 0.85rem;
            border-radius: 0.7rem;
            border: 1px solid #BFDBFE;
            background: #EFF6FF;
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
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.55rem;
        }
        .drawer-decision-grid span,
        .drawer-position-card > div {
            display: grid;
            gap: 0.18rem;
            padding: 0.58rem;
            border-radius: 0.55rem;
            border: 1px solid rgba(191,219,254,0.92);
            background: rgba(255,255,255,0.72);
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
            margin: 0.75rem 0;
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
        .drawer-waiting {
            margin-top: 0.62rem;
        }
        .drawer-waiting ul {
            margin: 0.25rem 0 0 1rem;
            padding: 0;
        }
        .drawer-section {
            display: grid;
            gap: 0.65rem;
        }
        .drawer-card,
        .drawer-resolution,
        .drawer-review-summary,
        .drawer-industry-card,
        .drawer-raw {
            padding: 0.7rem 0.75rem;
            border: 1px solid var(--dash-border);
            border-radius: 0.55rem;
            background: var(--dash-surface);
        }
        .drawer-card-title,
        .drawer-section-title {
            color: var(--dash-secondary);
            font-size: 0.74rem;
            font-weight: 750;
        }
        .drawer-section-title {
            margin: 1rem 0 0.45rem;
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
            gap: 0.45rem;
            margin-top: 0.6rem;
        }
        .drawer-review-actions a,
        .drawer-review-actions button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 30px;
            padding: 0 0.7rem;
            border-radius: 999px;
            border: 1px solid var(--dash-border);
            background: var(--dash-surface);
            color: var(--dash-text);
            text-decoration: none;
            font-size: 0.76rem;
            font-weight: 740;
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
            background:#F6F8FB;
        }
        div.block-container {
            max-width: 1120px;
            padding-left: 1.8rem;
            padding-right: 1.8rem;
        }
        .terminal-header,
        .terminal-title-group,
        .terminal-notice,
        .terminal-refresh-card,
        .terminal-loading-shell,
        .market-ribbon,
        .decision-terminal-head,
        .dashboard-priority-strip,
        .watchlist-head,
        .decision-table,
        .table-filter-chip {
            max-width: 1080px;
            margin-left: auto;
            margin-right: auto;
            box-sizing: border-box;
        }
        .terminal-header,
        .terminal-title-group {
            max-width: 1080px;
        }
        .terminal-header {
            margin-bottom: 0.38rem;
            border-bottom-color: rgba(15,23,42,0.08);
        }
        .terminal-title {
            font-size: 1.62rem;
            font-weight: 780;
        }
        .terminal-subtitle {
            color:#64748B;
            font-size:13px;
        }
        .terminal-kicker {
            color:#2563EB;
        }
        .terminal-meta span {
            height:26px;
            border-color:rgba(148, 163, 184, 0.20);
            background:#FFFFFF;
            color:#64748B;
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
        .decision-terminal-head,
        .watchlist-head {
            display:flex;
            justify-content:space-between;
            align-items:flex-end;
            gap:0.8rem;
            margin-top:0.16rem;
            margin-bottom:0;
        }
        .decision-terminal-head {
            max-width:1080px;
            margin-left:auto;
            margin-right:auto;
        }
        .decision-terminal-head strong,
        .watchlist-head strong {
            display:block;
            color:#0F172A;
            font-size:15px;
            font-weight:760;
            line-height:1.2;
        }
        .decision-terminal-head span,
        .watchlist-head span {
            display:block;
            margin-top:0.12rem;
            color:#64748B;
            font-size:11.5px;
            font-weight:520;
        }
        .dashboard-priority-strip {
            display:grid;
            grid-template-columns:90px minmax(0, 1fr);
            align-items:stretch;
            min-height:42px;
            max-width:1080px;
            margin:0.18rem auto 0;
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
            font-size:10.5px;
            font-weight:520;
        }
        .dashboard-priority-list {
            display:flex;
            align-items:center;
            gap:0.34rem;
            min-width:0;
            margin:0;
            padding:0.34rem 0.46rem;
            border:0;
            background:transparent;
            overflow:hidden;
            box-sizing:border-box;
        }
        .dashboard-priority-row {
            display:grid;
            grid-template-columns:8px minmax(34px, max-content) minmax(44px, max-content) minmax(0, 1fr);
            align-items:center;
            gap:0.32rem;
            flex:1 1 0;
            min-height:30px;
            min-width:0;
            max-width:100%;
            padding:0 0.52rem;
            border:1px solid transparent;
            border-radius:5px;
            border-right:0;
            background:rgba(255,255,255,0.62);
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
            flex:1.28 1 0;
            background:#FFFFFF;
            border-color:rgba(148, 163, 184, 0.14);
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
            border-right:0;
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
        div[data-testid="stVerticalBlock"] > div:has(.decision-lanes-marker) + div [data-testid="stHorizontalBlock"] {
            max-width:1080px;
            margin:0 auto 0.3rem;
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
            max-width:1080px;
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
        .st-key-dashboard_clear_lane_filter {
            margin-top:0.34rem;
            margin-bottom:0.34rem;
        }
        .st-key-dashboard_clear_lane_filter button {
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
        .st-key-dashboard_clear_lane_filter button p {
            margin:0;
            font-size:11px !important;
            font-weight:620 !important;
            line-height:26px !important;
            letter-spacing:0;
            white-space:nowrap !important;
            word-break:keep-all !important;
        }
        .st-key-dashboard_clear_lane_filter button:hover {
            opacity:1;
            color:#8A1F1F !important;
            background:rgba(254, 226, 226, 0.78) !important;
            border-color:rgba(220, 38, 38, 0.30) !important;
        }
        .decision-table {
            display:block;
            width:100%;
            border:1px solid rgba(148, 163, 184, 0.18);
            border-radius:8px;
            overflow-x:auto;
            overflow-y:hidden;
            background:#FFFFFF;
            margin-top:0.28rem;
            margin-bottom:0.72rem;
            box-shadow:none;
            box-sizing:border-box;
        }
        .decision-grid {
            display:grid;
            grid-template-columns:
                minmax(100px, 0.66fr)
                minmax(128px, 0.82fr)
                minmax(72px, 0.44fr)
                minmax(126px, 0.78fr)
                minmax(64px, 0.42fr)
                minmax(170px, 0.90fr)
                minmax(68px, 0.42fr)
                60px;
            align-items:center;
            gap:0.56rem;
            min-height:44px;
            min-width:914px;
            width:100%;
            padding:0 12px;
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
            background:#F9FAFB;
            border-bottom:1px solid rgba(15, 23, 42, 0.045);
        }
        .decision-row {
            border-bottom:1px solid rgba(15, 23, 42, 0.042);
            cursor:pointer;
        }
        .decision-row:last-child {
            border-bottom:0;
        }
        .decision-row:hover {
            background:#FBFCFE;
        }
        .decision-header {
            min-height:30px;
            padding:0;
            color:#64748B;
            font-size:10.6px;
            font-weight:600;
            background:transparent;
            border-bottom:0;
            position:static;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            letter-spacing:0;
        }
        .decision-cell {
            display:flex;
            align-items:center;
            min-height:44px;
            padding:0;
            border-bottom:0;
            color:#0F172A;
            font-size:12px;
            font-variant-numeric:tabular-nums;
            min-width:0;
            max-width:100%;
            overflow:hidden;
            text-overflow:ellipsis;
            box-sizing:border-box;
        }
        .decision-table.compact .decision-cell {
            min-height:44px;
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
            font-variant-numeric:tabular-nums;
            letter-spacing:0;
        }
        .price-market-cell span {
            color:#5F6F82;
            font-weight:560;
        }
        .decision-cell-stack strong,
        .action-cell strong {
            max-width:100%;
            color:#0F172A;
            font-size:12px;
            font-weight:700;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .action-cell {
            width:100%;
            max-width:220px;
        }
        .decision-cell-stack span,
        .action-cell span {
            max-width:100%;
            color:#64748B;
            font-size:11px;
            font-weight:540;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
        }
        .stock-cell strong {
            font-size:13px;
            font-weight:700;
            letter-spacing:0;
            padding-left:1px;
        }
        .stock-cell span {
            color:#64748B;
            max-width:100%;
        }
        .decision-badge {
            display:inline-flex;
            align-items:center;
            height:18px;
            min-height:18px;
            max-width:100%;
            padding:0 6px;
            border-radius:999px;
            font-size:11px;
            font-weight:600;
            line-height:18px;
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
            display:inline-flex;
            align-items:center;
            gap:5px;
            height:21px;
            max-width:100%;
            min-width:0;
            padding:0 7px;
            border-radius:6px;
            box-sizing:border-box;
            overflow:hidden;
            white-space:nowrap;
        }
        .entry-rating-token strong {
            min-width:0;
            overflow:hidden;
            text-overflow:ellipsis;
            white-space:nowrap;
            color:inherit;
            font-size:11px;
            font-weight:650;
            line-height:21px;
        }
        .entry-rating-token em {
            flex:0 0 auto;
            color:#64748B;
            font-size:10px;
            font-style:normal;
            font-weight:650;
            line-height:21px;
            opacity:0.82;
            font-variant-numeric:tabular-nums;
        }
        .dashboard-dot-status {
            display:inline-flex;
            align-items:center;
            gap:0.28rem;
            color:#475569;
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
            max-width:64px;
            justify-self:stretch;
        }
        .dashboard-view-action {
            display:inline-flex;
            align-items:center;
            justify-content:center;
            gap:0.12rem;
            min-width:32px;
            height:22px;
            padding:0 0.18rem;
            border:1px solid transparent;
            border-radius:4px;
            background:transparent;
            color:#64748B;
            font-size:11.5px;
            font-weight:560;
            text-decoration:none !important;
            white-space:nowrap;
            overflow:hidden;
            text-overflow:ellipsis;
            box-sizing:border-box;
        }
        .dashboard-view-action i {
            color:#94A3B8;
            font-style:normal;
            font-size:14px;
            line-height:1;
        }
        .dashboard-view-action:hover {
            color:#334155;
            border-color:transparent;
            background:transparent;
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
                grid-template-columns:100px 128px 72px 126px 64px 170px 68px 60px;
                min-width:888px;
                gap:8px;
                min-height:44px;
                padding:0 8px;
                font-size:11.5px;
            }
            .decision-grid-head {
                min-height:29px;
            }
            .decision-cell,
            .decision-table.compact .decision-cell {
                min-height:44px;
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


def _header_cell_html(value: object, align: object = None) -> str:
    align_class = " align-right" if align == "right" else ""
    return f'<div class="decision-header{align_class}">{escape(str(value))}</div>'


def _decision_table_row_html(row: pd.Series) -> str:
    symbol = str(row.get("symbol", "")).upper()
    safe_symbol = escape(symbol)
    cells = "".join(_decision_table_cell_html(row, definition, symbol) for definition in WATCHLIST_COLUMNS)
    return (
        f'<div class="decision-grid decision-row" data-dashboard-drawer-open="{safe_symbol}" '
        f'title="打开 {safe_symbol} 右侧详情面板">{cells}</div>'
    )


def _decision_table_cell_html(row: pd.Series, definition: dict, symbol: str) -> str:
    key = str(definition["key"])
    align_class = " align-right" if definition.get("align") == "right" else ""
    if key == "symbol":
        return (
            '<div class="decision-cell decision-cell-stack stock-cell">'
            f'<strong>{escape(symbol)}</strong>'
            "</div>"
        )
    if key == "priceMarket":
        price = _display_table_text(_safe_table_value("price", row.get("price")), fallback="当前价待补")
        market_cap = _display_table_text(_safe_table_value("marketCap", row.get("marketCap")), fallback="市值待补")
        return (
            '<div class="decision-cell decision-cell-stack price-market-cell">'
            f'<strong>{escape(price)}</strong>'
            f'<span>{escape(market_cap)}</span>'
            "</div>"
        )
    if key == "actionSummary":
        action = _display_table_text(_safe_table_value("action", _row_final_action(row)), fallback="待复核")
        valuation = _display_table_text(_safe_table_value("valuationStatus", row.get("valuationStatus")), fallback="估值待确认")
        position = _display_table_text(_row_current_add_text(row), fallback="")
        secondary_parts = [_short_badge_text(valuation)]
        if position and position not in {"不建议新增", "待补"}:
            secondary_parts.append(position)
        elif position == "不建议新增":
            secondary_parts.append(position)
        return (
            '<div class="decision-cell decision-cell-stack action-cell">'
            f'<strong>{escape(_short_badge_text(action))}</strong>'
            f'<span>{escape(" · ".join(secondary_parts))}</span>'
            "</div>"
        )
    if key == "dataStatus":
        value = _display_table_text(_safe_table_value(key, row.get(key)), fallback="待复核")
        return f'<div class="decision-cell">{_data_status_dot_html(value)}</div>'
    if key == "actions":
        return f'<div class="decision-cell action-view-cell">{_dashboard_view_action_html(symbol)}</div>'
    if key == "entryRating":
        return _entry_rating_cell_html(row)
    value = _safe_table_value(key, row.get(key, ""))
    value = _display_table_text(value, fallback="待补")
    if definition.get("kind") == "badge":
        return _badge_cell_html(_compact_watchlist_badge_text(key, value), _badge_color_for_cell(key, value, row), title=value)
    return f'<div class="decision-cell{align_class}">{escape(str(value))}</div>'


def _display_table_text(value: object, fallback: str = "待补") -> str:
    if _looks_like_technical_error(value):
        return "数据异常"
    text = str(value or "").strip()
    if not text or text.lower() in {"n/a", "none", "nan", "null"}:
        return fallback
    return text


def _compact_watchlist_badge_text(key: str, value: object) -> str:
    text = str(value or "").strip()
    if key == "qualityRating":
        first = text.split(" ", 1)[0].strip()
        if first:
            return first
    return _short_badge_text(text)


def _entry_rating_cell_html(row: pd.Series) -> str:
    label, grade, title = _entry_rating_display_parts(row)
    tone = _buy_point_label_tone(label)
    background, foreground, border = BADGE_STYLES.get(tone, BADGE_STYLES["gray"])
    display_text = _entry_rating_chip_text(label, grade)
    return (
        '<div class="decision-cell entry-rating-cell">'
        f'<span class="entry-rating-token" title="{escape(title)}" '
        f'style="background:{background};color:{foreground};border:1px solid {border};">'
        f"<strong>{escape(display_text)}</strong>"
        "</span></div>"
    )


def _entry_rating_chip_text(label: object, grade: object) -> str:
    label_text = str(label or "").strip()
    grade_text = str(grade or "").strip().upper()
    if grade_text and label_text:
        return f"{grade_text} · {label_text}"
    return label_text or grade_text or "待确认"


def _buy_point_label_tone(label: object) -> str:
    text = str(label or "").strip()
    if "极贵" in text:
        return "deepred"
    if "偏贵" in text:
        return "orange"
    if "击球区" in text or "回撤买点" in text or "合理偏便宜" in text:
        return "green"
    if "等回踩" in text or "接近" in text:
        return "blue"
    if "只观察" in text or "观察" in text or "待复核" in text:
        return "yellow"
    if "数据" in text:
        return "gray"
    return "gray"


def _entry_rating_display_parts(row: pd.Series) -> tuple[str, str, str]:
    raw = _display_table_text(_safe_table_value("entryRating", row.get("entryRating")), fallback="待确认")
    normalized = raw.replace("－", "-").replace("–", "-").replace("—", "-").strip()
    grade = ""
    remainder = normalized
    first_token = normalized.split(" ", 1)[0].strip()
    if _looks_like_rating_token(first_token):
        grade = first_token.upper()
        remainder = normalized[len(first_token) :].strip()
    elif "-" in normalized:
        prefix, suffix = normalized.split("-", 1)
        if _looks_like_rating_token(prefix.strip()):
            grade = prefix.strip().upper()
            remainder = suffix.strip()
    remainder = remainder.lstrip("-").strip()
    valuation_label = _entry_rating_text_label(row.get("valuationStatus"))
    if valuation_label == "极贵":
        return valuation_label, grade, raw
    label = _entry_rating_text_label(remainder)
    if not label:
        label = valuation_label
    if not label:
        label = _entry_label_from_grade(grade)
    return label or "待确认", grade, raw


def _looks_like_rating_token(value: object) -> bool:
    token = str(value or "").strip().upper()
    if not token:
        return False
    core = token.rstrip("+-")
    return core in {"A", "B", "C", "D"} and len(token) <= 3


def _entry_rating_text_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "数据" in text:
        return "数据不足"
    if "极贵" in text:
        return "极贵"
    if "击球区" in text:
        return "击球区附近"
    if "回撤" in text:
        return "回撤买点"
    if "合理偏便宜" in text:
        return "合理偏便宜"
    if "等回踩" in text or "可等回踩" in text:
        return "等回踩"
    if "待复核" in text or "复核" in text:
        return "待复核"
    if "只观察" in text or "观察" in text:
        return "只观察"
    if "偏贵" in text:
        return "偏贵"
    return ""


def _entry_label_from_grade(grade: str) -> str:
    normalized = str(grade or "").upper()
    if normalized.startswith("A"):
        return "回撤买点"
    if normalized == "B+":
        return "接近买点"
    if normalized.startswith("B"):
        return "等回踩"
    if normalized.startswith("C"):
        return "只观察"
    if normalized.startswith("D"):
        return "偏贵"
    return "待确认"


def _data_status_dot_html(value: object) -> str:
    label = _compact_data_status_label(value)
    tone = _data_status_tone(value)
    return f'<span class="dashboard-dot-status {escape(tone)}"><i></i>{escape(label)}</span>'


def _compact_data_status_label(value: object) -> str:
    text = str(value or "")
    if "完整" in text or text == "高":
        return "完整"
    if "中" in text:
        return "中"
    if "低" in text:
        return "低"
    if "缓存" in text:
        return "缓存"
    if "异常" in text:
        return "异常"
    if "不足" in text:
        return "不足"
    return "复核"


def _data_status_tone(value: object) -> str:
    text = str(value or "")
    if "完整" in text or text == "高":
        return "green"
    if "中" in text:
        return "blue"
    if "低" in text:
        return "orange"
    if "异常" in text or "不足" in text or "缓存" in text:
        return "yellow"
    return "gray"


def _dashboard_view_action_html(symbol: str) -> str:
    normalized_symbol = str(symbol or "").upper()
    safe_symbol = escape(normalized_symbol)
    onclick = (
        "event.preventDefault();event.stopPropagation();"
        f"if(window.__dashboardOpenDrawer){{window.__dashboardOpenDrawer({json.dumps(normalized_symbol, ensure_ascii=False)},null);}}"
        "return false;"
    )
    return (
        f'<a class="dashboard-view-action" href="#" data-dashboard-drawer-open="{safe_symbol}" '
        f'onclick="{escape(onclick, quote=True)}" title="打开 {safe_symbol} 右侧详情面板"><span>查看</span><i>›</i></a>'
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


def _badge_html(value: object, color: str, symbol: str | None = None) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return _dashboard_cell_link(
        '<div class="decision-cell">'
        f'<span class="decision-badge" style="background:{background};color:{foreground};border:1px solid {border};">'
        f"{escape(str(value))}"
        "</span></div>",
        symbol,
    )


def _badge_cell_html(value: object, color: str, title: object | None = None) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    title_attr = f' title="{escape(str(title))}"' if title else ""
    return (
        '<div class="decision-cell">'
        f'<span class="decision-badge"{title_attr} style="background:{background};color:{foreground};border:1px solid {border};">'
        f"{escape(str(value))}"
        "</span></div>"
    )


def _badge_span_html(value: object, color: str, extra_class: str = "") -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    class_name = "decision-badge"
    if extra_class:
        class_name = f"{class_name} {escape(extra_class)}"
    return (
        f'<span class="{class_name}" style="background:{background};color:{foreground};border:1px solid {border};">'
        f"{escape(str(value))}"
        "</span>"
    )


def _summary_badge_html(symbol: object, action: object, color: str) -> str:
    background, foreground, border = BADGE_STYLES.get(color, BADGE_STYLES["gray"])
    return (
        f'<span class="summary-badge" style="background:{background};color:{foreground};border:1px solid {border};">'
        f"<span>{escape(str(symbol))}</span><span>{escape(str(action))}</span>"
        "</span>"
    )


def _lane_item_html(row: pd.Series) -> str:
    state = str(row.get("valuationStatus") or row.get("entryRating") or "待确认")
    state_color = _badge_color_for_cell("valuationStatus", state, row)
    symbol = str(row.get("symbol") or "")
    full_reason = _lane_full_reason(row)
    short_reason = _lane_short_reason(full_reason)
    return (
        f'<a class="lane-item" href="#" data-dashboard-drawer-open="{escape(symbol)}" title="{escape(full_reason)}">'
        f'<span class="lane-symbol">{escape(symbol)}</span>'
        f'<span class="lane-reason">{escape(short_reason)}</span>'
        f'{_badge_span_html(_short_badge_text(state), state_color, "lane-state-badge")}'
        "</a>"
    )


def _lane_stack_html(rows: list[pd.Series]) -> str:
    if not rows:
        body = '<div class="summary-empty">暂无</div>'
    else:
        body = "".join(_lane_item_html(row) for row in rows[:4])
    return f'<div class="lane-row-stack">{body}</div>'


def _short_badge_text(value: object) -> str:
    text = str(value)
    replacements = {
        "可小仓观察，待关键数据复核后再加仓": "待复核",
        "可小仓分批": "可小仓",
        "可正常分批": "可分批",
        "回撤后有吸引力": "回撤买点",
        "数据不足，需复核": "数据不足",
    }
    return replacements.get(text, text)


def _lane_reason(row: pd.Series) -> str:
    return _lane_short_reason(_lane_full_reason(row))


def _lane_full_reason(row: pd.Series) -> str:
    reasons = _list_value(row.get("overheatReasons"))
    if row.get("dataConfidence") == "low":
        return "关键数据待复核"
    if reasons and _numeric(row.get("overheatScore")) >= 40:
        return _translate_factor(str(reasons[0])).rstrip("。")
    positives = _translated_list(_list_value(row.get("keyPositiveDrivers")), limit=1)
    if positives:
        return positives[0]
    risks = _translated_list(_quality_negative_items(row), limit=1)
    if risks:
        return risks[0]
    return str(row.get("valuationStatus") or "等待确认")


def _lane_short_reason(reason: object) -> str:
    text = str(reason or "").strip().rstrip("。")
    if not text:
        return "等待确认"
    if "今日下跌只是短期冷却" in text or "不等于进入击球区" in text:
        return "短期冷却，未到买点"
    if "关键数据待复核" in text or ("数据" in text and "复核" in text):
        return "数据待复核"
    if "RSI" in text and any(keyword in text for keyword in ["高于", "偏热", "过热", "极高"]):
        return "RSI仍偏热"
    if "回撤" in text and any(keyword in text for keyword in ["深", "较大", "超过", "距高点"]):
        return "回撤较深"
    if "收入增速" in text or "收入增长" in text:
        return "收入增速"
    if "自由现金流" in text or "FCF" in text:
        return "FCF支撑"
    if len(text) <= 18:
        return text
    return text[:17] + "…"


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
