from __future__ import annotations

from datetime import date, timedelta
from html import escape

import pandas as pd
import streamlit as st

from data.signal_performance import (
    RESULT_LABELS,
    SIGNAL_TYPE_OPTIONS,
    SignalPerformanceStore,
    refresh_signal_outcomes,
    signal_performance_summary,
    signal_performance_table_rows,
)
from settings import load_watchlist
from ui.theme import render_page_header


def render() -> None:
    _render_styles()
    render_page_header("信号表现", "追踪系统信号后的真实收益表现，用后验数据验证判断质量。")
    store = SignalPerformanceStore()

    _render_manual_signal_form(store)
    all_records = store.list_signals()
    filters = _render_filters(all_records)
    records = store.list_signals(
        symbol=filters.get("symbol"),
        signal_type=filters.get("signal_type"),
        result_label=filters.get("result_label"),
        start_date=filters.get("start_date"),
        end_date=filters.get("end_date"),
    )
    if filters.get("core_only"):
        records = [record for record in records if "核心" in str(record.get("position_context") or "")]
    _render_actions(store)
    _render_summary(signal_performance_summary(records))
    _render_signal_table(records)


def _render_actions(store: SignalPerformanceStore) -> None:
    cols = st.columns([1.2, 4.8], vertical_alignment="center")
    if cols[0].button("刷新后验收益", key="signal-performance-refresh", width="stretch"):
        result = refresh_signal_outcomes(store)
        st.success(f"已刷新 {int(result.get('updated', 0))} 条信号后验收益。")
        st.rerun()
    cols[1].caption("页面打开只读取缓存；点击刷新后才用本地日线重新计算 1/3/5/10/20 日表现。")


def _render_manual_signal_form(store: SignalPerformanceStore) -> None:
    with st.expander("手动记录信号", expanded=False):
        tickers = load_watchlist()
        cols = st.columns([1.0, 1.0, 1.0, 1.2])
        symbol = cols[0].selectbox("股票", options=tickers or [""], index=0, key="signal-manual-symbol")
        signal_date = cols[1].date_input("日期", value=date.today(), key="signal-manual-date")
        signal_price = cols[2].number_input("信号价", min_value=0.0, value=0.0, step=0.01, format="%.2f", key="signal-manual-price")
        signal_label = cols[3].selectbox("信号类型", options=SIGNAL_TYPE_OPTIONS, key="signal-manual-label")
        cols2 = st.columns([1.2, 1.0, 2.8])
        price_source = cols2[0].selectbox("价格来源", options=["手动", "本地日线", "研报中心", "历史回撤档案", "周末价差"], key="signal-manual-source")
        confidence = cols2[1].number_input("置信度，可选", min_value=0.0, max_value=100.0, value=0.0, step=1.0, key="signal-manual-confidence")
        position_context = cols2[2].text_input("持仓/价格区间，可选", placeholder="例如：核心仓、观察承接区、未持仓", key="signal-manual-context")
        note = st.text_area("备注，可选", placeholder="记录当时为什么出现这个信号。", key="signal-manual-note")
        if st.button("保存信号", key="signal-manual-save", type="primary"):
            try:
                store.save_signal(
                    symbol=symbol,
                    signal_date=signal_date,
                    signal_type=signal_label,
                    signal_label=signal_label,
                    signal_price=signal_price,
                    price_source=price_source,
                    confidence_score=confidence if confidence > 0 else None,
                    position_context=position_context,
                    note=note,
                )
                st.success("信号已保存。点击“刷新后验收益”后会补齐表现。")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _render_filters(records: list[dict]) -> dict:
    symbols = ["全部"] + sorted({str(record.get("symbol") or "") for record in records if record.get("symbol")})
    signal_types = ["全部"] + sorted({str(record.get("signal_type") or "") for record in records if record.get("signal_type")})
    results = ["全部"] + RESULT_LABELS
    default_start = date.today() - timedelta(days=180)
    default_end = date.today()
    with st.expander("筛选", expanded=False):
        cols = st.columns([1.0, 1.0, 1.0, 1.0, 0.9])
        start_date = cols[0].date_input("开始日期", value=default_start, key="signal-filter-start")
        end_date = cols[1].date_input("结束日期", value=default_end, key="signal-filter-end")
        symbol = cols[2].selectbox("股票", options=symbols, key="signal-filter-symbol")
        signal_type = cols[3].selectbox("信号类型", options=signal_types, key="signal-filter-type")
        result_label = cols[4].selectbox("结果判定", options=results, key="signal-filter-result")
        core_only = st.checkbox("只看核心仓相关", value=False, key="signal-filter-core")
    return {
        "start_date": start_date,
        "end_date": end_date,
        "symbol": "" if symbol == "全部" else symbol,
        "signal_type": "" if signal_type == "全部" else signal_type,
        "result_label": "" if result_label == "全部" else result_label,
        "core_only": core_only,
    }


def _render_summary(summary: dict) -> None:
    cards = [
        ("信号总数", _int_text(summary.get("total"))),
        ("1日平均收益", _pct_text(summary.get("avg_1d_pct"))),
        ("3日平均收益", _pct_text(summary.get("avg_3d_pct"))),
        ("5日平均收益", _pct_text(summary.get("avg_5d_pct"))),
        ("20日平均收益", _pct_text(summary.get("avg_20d_pct"))),
        ("胜率", _pct_text(summary.get("win_rate_pct"))),
        ("平均最大回撤", _pct_text(summary.get("avg_max_drawdown_pct"))),
        ("最好信号类型", str(summary.get("best_signal_type") or "数据不足")),
        ("最差信号类型", str(summary.get("worst_signal_type") or "数据不足")),
    ]
    html = "".join(
        f'<div class="signal-summary-card"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
        for label, value in cards
    )
    st.markdown(f'<section class="signal-summary-strip">{html}</section>', unsafe_allow_html=True)


def _render_signal_table(records: list[dict]) -> None:
    rows = signal_performance_table_rows(records)
    st.markdown("### 信号明细")
    if not rows:
        st.info("还没有符合筛选条件的信号。可以先手动记录一条，或从其他页面记录当前信号。")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _pct_text(value: object) -> str:
    try:
        if value is None:
            return "数据不足"
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return "数据不足"


def _int_text(value: object) -> str:
    try:
        return str(int(value or 0))
    except (TypeError, ValueError):
        return "0"


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .signal-summary-strip {
            display:grid;
            grid-template-columns:repeat(5,minmax(0,1fr));
            gap:0.55rem;
            margin:0.85rem 0 0.8rem;
        }
        .signal-summary-card {
            min-height:4.2rem;
            padding:0.62rem 0.68rem;
            border:1px solid rgba(15,23,42,0.08);
            border-radius:0.5rem;
            background:#fff;
            box-shadow:0 10px 24px rgba(15,23,42,0.04);
        }
        .signal-summary-card span {
            display:block;
            color:#64748b;
            font-size:0.74rem;
            font-weight:700;
        }
        .signal-summary-card strong {
            display:block;
            margin-top:0.32rem;
            color:#0f172a;
            font-size:1rem;
            line-height:1.2;
            font-weight:820;
            overflow-wrap:anywhere;
        }
        @media (max-width: 1100px) {
            .signal-summary-strip { grid-template-columns:repeat(2,minmax(0,1fr)); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
