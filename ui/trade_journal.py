from __future__ import annotations

from datetime import date, timedelta
from html import escape

import streamlit as st

from data.decision_log import (
    DecisionErrorTagStore,
    DecisionLogStore,
    DecisionOutcomeStore,
    TradeJournalStore,
    build_decision_signal_stats,
    refresh_decision_outcomes,
)
from formatting import format_currency, format_percent
from ui.theme import render_page_header, render_section_title


ACTION_OPTIONS = {
    "买入": "buy",
    "卖出": "sell",
    "加仓": "add",
    "减仓": "trim",
    "卖 Put": "sell_put",
    "Covered Call": "covered_call",
    "放弃操作": "skip",
}
ACTION_LABELS = {value: label for label, value in ACTION_OPTIONS.items()}
FINAL_ACTION_LABELS = {
    "add": "加仓",
    "buy": "买入",
    "wait": "等待",
    "review": "复核",
    "blocked": "禁止",
    "可小仓分批": "可小仓分批",
    "可正常分批": "可正常分批",
    "只观察": "只观察",
    "等回踩": "等回踩",
    "禁止追高": "禁止追高",
    "待复核，暂不新增": "待复核",
    "unknown": "未标记",
}
LANE_LABELS = {
    "actionable": "可执行",
    "blocked": "禁止追高",
    "review": "需复核",
    "wait": "等待观察",
    "unknown": "未标记",
}
ERROR_TAG_OPTIONS = {
    "估值过高": "valuation_too_high",
    "数据低置信": "low_confidence_data",
    "财报前误判": "pre_earnings_misread",
    "技术破位": "technical_breakdown",
    "宏观冲击": "macro_shock",
    "投资假设破裂": "thesis_broken",
    "仓位过重": "position_too_large",
    "忽略系统警告": "ignored_system_warning",
}
ERROR_TAG_LABELS = {value: label for label, value in ERROR_TAG_OPTIONS.items()}
BLANK_TEXT = "—"
OUTCOME_HORIZON_DAYS = {"1d": 1, "1w": 7, "1m": 30, "3m": 90, "6m": 180}


def render() -> None:
    _render_styles()
    render_page_header("交易日志", "手动记录真实操作和放弃动作，保留执行上下文。")

    store = TradeJournalStore()
    decision_store = DecisionLogStore()
    outcome_store = DecisionOutcomeStore()
    error_tag_store = DecisionErrorTagStore()
    _render_notice()
    st.markdown('<div class="trade-workbench-section">交易记录</div>', unsafe_allow_html=True)
    toolbar_cols = st.columns([3.8, 1])
    toolbar_cols[0].markdown(
        '<div class="trade-journal-toolbar-note">执行优先，日志用于复盘，不做收益统计。</div>',
        unsafe_allow_html=True,
    )
    if toolbar_cols[1].button("新增记录", key="trade-journal-open", width="stretch"):
        st.session_state["trade_journal_editor_open"] = True
    _render_editor(store)

    symbols = store.list_symbols()
    entries = _load_entries(store, symbols)
    _render_summary(entries)
    _render_entry_delete_confirmation(store)
    _render_entries(symbols, entries)
    _render_signal_replay(decision_store, outcome_store, error_tag_store)


def _render_editor(store: TradeJournalStore) -> None:
    editor_open = bool(st.session_state.get("trade_journal_editor_open", False))
    with st.expander("新增交易记录", expanded=editor_open):
        st.session_state["trade_journal_editor_open"] = False
        with st.form("trade-journal-form"):
            top_cols = st.columns([1.1, 1.2, 1])
            symbol = top_cols[0].text_input("股票代码", key="trade-journal-symbol").strip().upper()
            action_label = top_cols[1].selectbox("操作类型", list(ACTION_OPTIONS), key="trade-journal-action")
            trade_date = top_cols[2].date_input("日期", value=date.today(), key="trade-journal-date")

            trade_cols = st.columns(3)
            quantity = trade_cols[0].text_input("数量", key="trade-journal-quantity")
            price = trade_cols[1].text_input("价格", key="trade-journal-price")
            decision_snapshot_id = trade_cols[2].text_input(
                "关联信号 ID（可选）",
                key="trade-journal-snapshot-id",
            )

            option_cols = st.columns(3)
            premium = option_cols[0].text_input("权利金", key="trade-journal-premium")
            strike_price = option_cols[1].text_input("行权价", key="trade-journal-strike")
            expiry_date = option_cols[2].text_input("到期日", placeholder="YYYY-MM-DD", key="trade-journal-expiry")

            notes = st.text_area("备注", height=86, key="trade-journal-notes")
            submitted = st.form_submit_button("保存记录", width="stretch")
            if submitted:
                _save_entry(
                    store,
                    symbol,
                    {
                        "trade_date": trade_date.isoformat(),
                        "action_type": ACTION_OPTIONS[action_label],
                        "quantity": quantity,
                        "price": price,
                        "premium": premium,
                        "strike_price": strike_price,
                        "expiry_date": expiry_date,
                        "decision_snapshot_id": decision_snapshot_id,
                        "notes": notes,
                    },
                )


def _save_entry(store: TradeJournalStore, symbol: str, values: dict) -> None:
    try:
        saved = store.save_entry(symbol, values)
    except ValueError as exc:
        st.session_state["trade_journal_notice"] = ("error", _friendly_error(str(exc)))
        st.rerun()
    st.session_state["trade_journal_notice"] = ("success", f"{saved['symbol']} 交易记录已保存。")
    st.rerun()


def _render_notice() -> None:
    notice = st.session_state.pop("trade_journal_notice", None)
    if not notice:
        return
    level, message = notice
    if level == "success":
        st.success(message)
    else:
        st.error(message)


def _load_entries(store: TradeJournalStore, symbols: list[str]) -> list[dict]:
    filter_cols = st.columns([1, 3.4])
    options = ["全部股票", *symbols]
    selected = filter_cols[0].selectbox("股票筛选", options, key="trade-journal-symbol-filter")
    filter_cols[1].markdown(
        '<div class="trade-journal-filter-note">只记录执行动作，不计算收益、胜率或图表。</div>',
        unsafe_allow_html=True,
    )
    if selected == "全部股票":
        return store.list_entries()
    return store.list_entries(selected)


def _render_summary(entries: list[dict]) -> None:
    option_count = sum(1 for entry in entries if entry.get("action_type") in {"sell_put", "covered_call"})
    skip_count = sum(1 for entry in entries if entry.get("action_type") == "skip")
    stock_count = len({str(entry.get("symbol") or "") for entry in entries if entry.get("symbol")})
    latest = entries[0].get("trade_date") if entries else None
    items = [
        ("记录数", str(len(entries)), "ENTRIES"),
        ("覆盖股票", str(stock_count), "SYMBOLS"),
        ("期权动作", str(option_count), "OPTIONS"),
        ("放弃操作", str(skip_count), "SKIPPED"),
        ("最近日期", str(latest or BLANK_TEXT), "LATEST"),
    ]
    html = "".join(
        (
            '<div class="trade-journal-summary-item">'
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            f"<em>{escape(caption)}</em>"
            "</div>"
        )
        for label, value, caption in items
    )
    st.markdown(f'<div class="trade-journal-summary">{html}</div>', unsafe_allow_html=True)


def _render_entries(symbols: list[str], entries: list[dict]) -> None:
    render_section_title("交易日志列表", "按日期倒序，手动记录真实执行。")
    if not symbols:
        st.markdown(
            (
                '<div class="trade-journal-empty">'
                "<strong>暂无交易记录</strong>"
                "<span>先新增一次真实操作，后续再做复盘。</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        return

    headers = ["日期", "股票", "操作", "数量 / 价格", "期权参数", "关联信号", "备注", "操作"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    row_html = "".join(_entry_row_html(entry) for entry in entries)
    st.markdown(
        (
            '<div id="trade-journal-list"></div>'
            '<div class="trade-journal-table-wrap trade-terminal-table-wrap">'
            '<table class="trade-journal-table trade-terminal-table">'
            "<colgroup>"
            '<col style="width:12%"><col style="width:9%"><col style="width:9%"><col style="width:13%">'
            '<col style="width:13%"><col style="width:10%"><col style="width:auto"><col style="width:120px">'
            "</colgroup>"
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{row_html}</tbody>"
            "</table>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_entry_delete_confirmation(store: TradeJournalStore) -> None:
    entry_id = _query_int("deleteTrade")
    if entry_id is None:
        return
    entry = store.get_entry(entry_id)
    if not entry:
        _clear_trade_delete_query()
        st.session_state["trade_journal_notice"] = ("error", "交易记录不存在或已删除。")
        st.rerun()

    st.markdown(
        (
            '<div class="trade-delete-confirm">'
            '<div>'
            "<span>确认删除交易记录</span>"
            f"<strong>{escape(_entry_delete_summary(entry))}</strong>"
            "</div>"
            "<em>删除后仅移除这条手动记录，不影响系统信号样本。</em>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    cols = st.columns([1, 1, 4.2])
    if cols[0].button("确认删除", key=f"trade-entry-delete-confirm-{entry_id}", width="stretch"):
        deleted = store.delete_entry(entry_id)
        _clear_trade_delete_query()
        st.session_state["trade_journal_notice"] = (
            "success" if deleted else "error",
            "交易记录已删除。" if deleted else "交易记录不存在或已删除。",
        )
        st.rerun()
    if cols[1].button("取消", key=f"trade-entry-delete-cancel-{entry_id}", width="stretch"):
        _clear_trade_delete_query()
        st.rerun()


def _render_signal_replay(
    decision_store: DecisionLogStore,
    outcome_store: DecisionOutcomeStore,
    error_tag_store: DecisionErrorTagStore,
) -> None:
    st.markdown('<div class="trade-workbench-section replay">系统信号复盘</div>', unsafe_allow_html=True)
    render_section_title("系统信号复盘", "按历史系统信号和后续表现聚合，不做交易收益统计。")
    _render_refresh_outcomes_toolbar()
    stats = build_decision_signal_stats()
    horizons = [str(horizon) for horizon in stats.get("horizons", ["1d", "1w", "1m", "3m", "6m"])]
    if not horizons:
        horizons = ["1d", "1w", "1m", "3m", "6m"]
    selected = st.radio("复盘周期", horizons, horizontal=True, key="trade-journal-signal-horizon")
    horizon_stats = (stats.get("byHorizon") or {}).get(selected, {})
    summary = horizon_stats.get("summary") or {}
    has_complete_samples = int(summary.get("sampleCount") or 0) > 0
    if not has_complete_samples:
        st.markdown(
            (
                '<div class="trade-journal-empty signal-empty">'
                "<strong>当前周期暂无完整复盘样本，刷新复盘结果后再查看统计。</strong>"
                "<span>可先记录系统信号，再刷新复盘结果。</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
    else:
        _render_signal_summary(summary)
        with st.expander("查看统计明细", expanded=False):
            final_action_rows = _complete_stat_rows(horizon_stats.get("byFinalAction") or [])
            decision_lane_rows = _complete_stat_rows(horizon_stats.get("byDecisionLane") or [])
            table_cols = st.columns(2)
            with table_cols[0]:
                st.markdown("##### 按系统动作统计")
                if final_action_rows:
                    st.markdown(_stats_table_html(final_action_rows, FINAL_ACTION_LABELS), unsafe_allow_html=True)
                else:
                    st.caption("暂无系统动作明细。")
            with table_cols[1]:
                st.markdown("##### 按决策通道统计")
                if decision_lane_rows:
                    st.markdown(_stats_table_html(decision_lane_rows, LANE_LABELS), unsafe_allow_html=True)
                else:
                    st.caption("暂无决策通道明细。")
            error_tag_rows = horizon_stats.get("byErrorTag") or []
            if error_tag_rows:
                st.markdown("##### 按错误标签统计")
                st.markdown(_error_stats_table_html(error_tag_rows, _error_tag_group_label), unsafe_allow_html=True)
                cross_cols = st.columns(2)
                with cross_cols[0]:
                    st.markdown("##### 系统动作 × 错误标签")
                    st.markdown(
                        _error_stats_table_html(
                            horizon_stats.get("byFinalActionErrorTag") or [],
                            _final_action_error_tag_group_label,
                        ),
                        unsafe_allow_html=True,
                    )
                with cross_cols[1]:
                    st.markdown("##### 决策通道 × 错误标签")
                    st.markdown(
                        _error_stats_table_html(
                            horizon_stats.get("byDecisionLaneErrorTag") or [],
                            _decision_lane_error_tag_group_label,
                        ),
                        unsafe_allow_html=True,
                    )
    _render_error_tag_management(decision_store, outcome_store, error_tag_store, selected, has_complete_samples)


def _render_refresh_outcomes_toolbar() -> None:
    cols = st.columns([3.6, 1])
    cols[0].markdown(
        '<div class="trade-journal-refresh-note">手动刷新历史信号的后续表现，不会启动自动任务。</div>',
        unsafe_allow_html=True,
    )
    refresh_summary = None
    with cols[1]:
        if st.button("刷新复盘结果", key="trade-journal-refresh-outcomes", width="stretch"):
            refresh_summary = refresh_decision_outcomes()
    if refresh_summary:
        _render_refresh_outcome_result(refresh_summary)


def _render_refresh_outcome_result(summary: dict) -> None:
    items = [
        ("刷新信号数", _int_text(summary.get("snapshotCount"))),
        ("生成/更新复盘数", _int_text(summary.get("outcomeCount"))),
        ("缺失数", _int_text(summary.get("missingCount"))),
    ]
    html = "".join(
        (
            '<div class="trade-refresh-result-item">'
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            "</div>"
        )
        for label, value in items
    )
    st.markdown(f'<div class="trade-refresh-result">{html}</div>', unsafe_allow_html=True)


def _render_error_tag_management(
    decision_store: DecisionLogStore,
    outcome_store: DecisionOutcomeStore,
    error_tag_store: DecisionErrorTagStore,
    horizon: str,
    has_complete_samples: bool,
) -> None:
    st.markdown('<div class="trade-journal-subsection">错误标签摘要</div>', unsafe_allow_html=True)
    counts = error_tag_store.tag_counts()
    recent = error_tag_store.recent_tags(limit=5)
    _render_error_tag_summary(counts, recent)

    st.markdown('<div class="trade-journal-subsection">系统信号样本</div>', unsafe_allow_html=True)
    snapshots = decision_store.list_recent_snapshots(limit=24)
    if not snapshots:
        st.markdown(
            (
                '<div class="trade-journal-empty signal-empty">'
                "<strong>暂无系统信号样本</strong>"
                "<span>有系统信号快照后，可以在这里手动标记错误原因。</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        return
    _render_missing_outcome_brief(snapshots, outcome_store, horizon, has_complete_samples)
    _render_snapshot_rows(snapshots, decision_store, outcome_store, error_tag_store, horizon)
    selected_snapshot = _selected_snapshot(snapshots)
    if selected_snapshot:
        _render_signal_snapshot_drawer(selected_snapshot, outcome_store, error_tag_store, horizon)


def _render_error_tag_summary(counts: list[dict], recent: list[dict]) -> None:
    if not counts and not recent:
        st.markdown(
            '<div class="trade-error-compact-empty">暂无错误标签。标记后会汇总各标签数量和最近案例。</div>',
            unsafe_allow_html=True,
        )
        return
    left, right = st.columns([1, 1.45])
    with left:
        if counts:
            items = "".join(
                (
                    '<div class="trade-error-count-row">'
                    f"<span>{escape(_error_tag_label(row.get('tag')))}</span>"
                    f"<strong>{escape(_int_text(row.get('count')))}</strong>"
                    "</div>"
                )
                for row in counts
            )
        else:
            items = '<div class="trade-error-muted">暂无错误标签</div>'
        st.markdown(f'<div class="trade-error-summary-card">{items}</div>', unsafe_allow_html=True)
    with right:
        if recent:
            cases = "".join(_recent_error_case_html(row) for row in recent)
        else:
            cases = '<div class="trade-error-muted">暂无最近错误案例</div>'
        st.markdown(f'<div class="trade-error-summary-card recent">{cases}</div>', unsafe_allow_html=True)


def _recent_error_case_html(row: dict) -> str:
    title = f"{_text(row.get('symbol'))} · {_error_tag_label(row.get('tag'))}"
    meta = f"{_text(row.get('decision_date'))} / {_final_action_label(row.get('final_action'))} / {_lane_label(row.get('decision_lane'))}"
    notes = _text(row.get("notes"))
    return (
        '<div class="trade-error-case-row">'
        f"<strong>{escape(title)}</strong>"
        f"<span>{escape(meta)}</span>"
        f"<em>{escape(notes)}</em>"
        "</div>"
    )


def _render_missing_outcome_brief(
    snapshots: list[dict],
    outcome_store: DecisionOutcomeStore,
    horizon: str,
    has_complete_samples: bool,
) -> None:
    missing_items: list[str] = []
    for snapshot in snapshots:
        snapshot_id = int(snapshot.get("id") or 0)
        outcome = outcome_store.get_outcome(snapshot_id, horizon) if snapshot_id else None
        status = _outcome_status_text(outcome, horizon, snapshot)
        if status == "已完成":
            continue
        symbol = _text(snapshot.get("symbol"))
        date_text = _text(snapshot.get("decision_date"))
        detail = _outcome_status_reason(outcome, horizon, snapshot)
        missing_items.append(
            f'<span><b>{escape(symbol)}</b><em>{escape(date_text)} · {escape(detail)}</em></span>'
        )
    if not missing_items:
        return
    tone = "muted" if has_complete_samples else "empty"
    body = "".join(missing_items[:8])
    more = len(missing_items) - 8
    more_html = f'<i>另有 {more} 条</i>' if more > 0 else ""
    st.markdown(
        (
            f'<div class="trade-missing-brief {tone}">'
            "<strong>缺失结果</strong>"
            f"<div>{body}{more_html}</div>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


def _render_snapshot_rows(
    snapshots: list[dict],
    decision_store: DecisionLogStore,
    outcome_store: DecisionOutcomeStore,
    error_tag_store: DecisionErrorTagStore,
    horizon: str,
) -> None:
    st.markdown(
        '<div class="trade-snapshot-table trade-terminal-table-wrap">'
        '<div class="trade-snapshot-list-head"><span>股票</span><span>日期</span><span>系统动作</span><span>周期状态</span><span>操作</span></div>'
        '</div>',
        unsafe_allow_html=True,
    )
    for snapshot in snapshots:
        snapshot_id = int(snapshot.get("id") or 0)
        tags = error_tag_store.list_tags_for_snapshot(snapshot_id)
        outcome = outcome_store.get_outcome(snapshot_id, horizon) if snapshot_id else None
        cols = st.columns([5.2, 1.05], gap="small", vertical_alignment="center")
        cols[0].markdown(
            (
                '<div class="trade-snapshot-row">'
                f'<div class="trade-snapshot-cell"><b>{escape(_text(snapshot.get("symbol")))}</b></div>'
                f'<div class="trade-snapshot-cell"><b>{escape(_text(snapshot.get("decision_date")))}</b></div>'
                f'<div class="trade-snapshot-cell"><b>{escape(_final_action_label(snapshot.get("final_action")))}</b>'
                f'<span>{escape(_lane_label(snapshot.get("decision_lane")))}</span></div>'
                f'<div class="trade-snapshot-cell"><b>{escape(_outcome_status_text(outcome, horizon, snapshot))}</b>'
                f'<span>{_outcome_status_detail_html(outcome, horizon, snapshot, tags)}</span></div>'
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        with cols[1]:
            action_cols = st.columns([1, 1], gap="small", vertical_alignment="center")
            if action_cols[0].button("查看", key=f"trade-snapshot-view-{snapshot_id}", width="stretch"):
                st.session_state["trade_error_snapshot_id"] = snapshot_id
                st.session_state.pop("trade_error_edit_tag", None)
                st.rerun()
            if action_cols[1].button("删除", key=f"trade-snapshot-delete-{snapshot_id}", width="stretch"):
                if decision_store.delete_snapshot(snapshot_id):
                    if st.session_state.get("trade_error_snapshot_id") == snapshot_id:
                        st.session_state.pop("trade_error_snapshot_id", None)
                    st.session_state.pop("trade_error_edit_tag", None)
                    st.session_state["trade_journal_notice"] = ("success", "系统信号样本已删除。")
                else:
                    st.session_state["trade_journal_notice"] = ("error", "系统信号样本不存在或已删除。")
                st.rerun()


def _render_error_tag_editor(snapshot: dict, error_tag_store: DecisionErrorTagStore) -> None:
    snapshot_id = int(snapshot.get("id") or 0)
    current_tags = error_tag_store.list_tags_for_snapshot(snapshot_id)
    editing_tag = str(st.session_state.get("trade_error_edit_tag") or "")
    tag_values = list(ERROR_TAG_OPTIONS.values())
    default_value = editing_tag if editing_tag in tag_values else tag_values[0]
    default_label = ERROR_TAG_LABELS.get(default_value, "估值过高")
    existing = next((tag for tag in current_tags if tag.get("tag") == editing_tag), {})

    st.markdown(
        (
            '<div class="trade-error-editor-head">'
            f"<strong>{escape(_text(snapshot.get('symbol')))} · 错误标签</strong>"
            f"<span>{escape(_text(snapshot.get('decision_date')))} / "
            f"{escape(_final_action_label(snapshot.get('final_action')))} / "
            f"{escape(_lane_label(snapshot.get('decision_lane')))}</span>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )

    if current_tags:
        for tag in current_tags:
            cols = st.columns([1.1, 2.7, 0.55, 0.55])
            cols[0].markdown(f"**{_error_tag_label(tag.get('tag'))}**")
            cols[1].markdown(escape(_text(tag.get("notes"))), unsafe_allow_html=True)
            if cols[2].button("编辑", key=f"trade-error-edit-{snapshot_id}-{tag.get('tag')}", width="stretch"):
                st.session_state["trade_error_edit_tag"] = str(tag.get("tag") or "")
                st.rerun()
            if cols[3].button("删除", key=f"trade-error-delete-{snapshot_id}-{tag.get('tag')}", width="stretch"):
                error_tag_store.delete_tag(snapshot_id, str(tag.get("tag") or ""))
                if st.session_state.get("trade_error_edit_tag") == tag.get("tag"):
                    st.session_state.pop("trade_error_edit_tag", None)
                st.session_state["trade_journal_notice"] = ("success", "错误标签已删除。")
                st.rerun()
    else:
        st.caption("当前系统信号还没有错误标签。")

    with st.form(f"trade-error-tag-form-{snapshot_id}"):
        default_index = list(ERROR_TAG_OPTIONS).index(default_label)
        tag_label = st.selectbox("错误原因", list(ERROR_TAG_OPTIONS), index=default_index)
        notes = st.text_area("备注", value=str(existing.get("notes") or ""), height=76)
        submitted = st.form_submit_button("保存错误标签", width="stretch")
        if submitted:
            try:
                error_tag_store.save_tag(snapshot_id, ERROR_TAG_OPTIONS[tag_label], notes)
            except ValueError:
                st.session_state["trade_journal_notice"] = ("error", "请选择有效的错误标签。")
                st.rerun()
            st.session_state.pop("trade_error_edit_tag", None)
            st.session_state["trade_journal_notice"] = ("success", "错误标签已保存。")
            st.rerun()


def _render_signal_summary(summary: dict) -> None:
    items = [
        ("样本数", _int_text(summary.get("sampleCount")), "已完成"),
        ("胜率", _percent_or_dash(summary.get("winRate")), "盈利占比"),
        ("平均收益", _percent_or_dash(summary.get("averageReturnPct")), "平均"),
        ("中位数收益", _percent_or_dash(summary.get("medianReturnPct")), "中位数"),
        ("平均最大回撤", _percent_or_dash(summary.get("averageMaxDrawdownPct")), "回撤"),
        ("缺失样本数", _int_text(summary.get("missingCount")), "缺失"),
    ]
    html = "".join(
        (
            '<div class="trade-journal-summary-item signal">'
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            f"<em>{escape(caption)}</em>"
            "</div>"
        )
        for label, value, caption in items
    )
    st.markdown(f'<div class="trade-journal-summary signal">{html}</div>', unsafe_allow_html=True)


def _stats_table_html(rows: list[dict], labels: dict[str, str]) -> str:
    headers = ["分组", "样本数", "胜率", "平均收益", "中位数收益", "平均回撤", "缺失数"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    if not rows:
        row_html = '<tr><td colspan="7" class="empty-row">暂无数据</td></tr>'
    else:
        row_html = "".join(_stats_row_html(row, labels) for row in rows)
    return (
        '<div class="trade-journal-table-wrap signal">'
        '<table class="trade-journal-table signal">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{row_html}</tbody>"
        "</table>"
        "</div>"
    )


def _stats_row_html(row: dict, labels: dict[str, str]) -> str:
    group = str(row.get("group") or "unknown")
    return (
        "<tr>"
        f'<td class="symbol">{escape(labels.get(group, group))}</td>'
        f"<td>{escape(_int_text(row.get('sampleCount')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('winRate')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('averageReturnPct')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('medianReturnPct')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('averageMaxDrawdownPct')))}</td>"
        f"<td>{escape(_int_text(row.get('missingCount')))}</td>"
        "</tr>"
    )


def _error_stats_table_html(rows: list[dict], labeler) -> str:
    headers = ["分组", "标签数", "完整样本", "平均收益", "平均回撤", "缺失数"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    if not rows:
        row_html = '<tr><td colspan="6" class="empty-row">暂无错误标签统计</td></tr>'
    else:
        row_html = "".join(_error_stats_row_html(row, labeler) for row in rows)
    return (
        '<div class="trade-journal-table-wrap signal error-stats">'
        '<table class="trade-journal-table signal">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{row_html}</tbody>"
        "</table>"
        "</div>"
    )


def _error_stats_row_html(row: dict, labeler) -> str:
    return (
        "<tr>"
        f'<td class="symbol">{escape(labeler(row))}</td>'
        f"<td>{escape(_int_text(row.get('totalCount')))}</td>"
        f"<td>{escape(_int_text(row.get('sampleCount')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('averageReturnPct')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('averageMaxDrawdownPct')))}</td>"
        f"<td>{escape(_int_text(row.get('missingCount')))}</td>"
        "</tr>"
    )


def _error_tag_group_label(row: dict) -> str:
    return _error_tag_label(row.get("group"))


def _final_action_error_tag_group_label(row: dict) -> str:
    return f"{_final_action_label(row.get('finalAction'))} × {_error_tag_label(row.get('errorTag'))}"


def _decision_lane_error_tag_group_label(row: dict) -> str:
    return f"{_lane_label(row.get('decisionLane'))} × {_error_tag_label(row.get('errorTag'))}"


def _complete_stat_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if int(row.get("sampleCount") or 0) > 0]


def _selected_snapshot(snapshots: list[dict]) -> dict | None:
    selected_id = int(st.session_state.get("trade_error_snapshot_id") or 0)
    if selected_id:
        for snapshot in snapshots:
            if int(snapshot.get("id") or 0) == selected_id:
                return snapshot
    return None


def _render_signal_snapshot_drawer(
    snapshot: dict,
    outcome_store: DecisionOutcomeStore,
    error_tag_store: DecisionErrorTagStore,
    active_horizon: str,
) -> None:
    snapshot_id = int(snapshot.get("id") or 0)
    symbol = _text(snapshot.get("symbol"))
    with st.container(key="trade-signal-drawer-container"):
        st.markdown('<div class="trade-signal-drawer-marker"></div>', unsafe_allow_html=True)
        head_cols = st.columns([1, 0.22], vertical_alignment="center")
        head_cols[0].markdown(
            (
                '<div class="trade-signal-drawer-head">'
                "<span>系统信号详情</span>"
                f"<strong>{escape(symbol)}</strong>"
                f"<em>{escape(_text(snapshot.get('decision_date')))}</em>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        if head_cols[1].button("关闭", key=f"trade-signal-drawer-close-{snapshot_id}", width="stretch"):
            st.session_state.pop("trade_error_snapshot_id", None)
            st.session_state.pop("trade_error_edit_tag", None)
            st.rerun()

        st.markdown(_signal_snapshot_summary_html(snapshot), unsafe_allow_html=True)
        st.markdown(_signal_snapshot_reasons_html(snapshot), unsafe_allow_html=True)
        st.markdown(
            _signal_snapshot_outcomes_html(snapshot, outcome_store, active_horizon),
            unsafe_allow_html=True,
        )
        _render_error_tag_editor(snapshot, error_tag_store)


def _signal_snapshot_summary_html(snapshot: dict) -> str:
    items = [
        ("当时价格", _money_text(snapshot.get("price"))),
        ("系统动作", _final_action_label(snapshot.get("final_action"))),
        ("决策通道", _lane_label(snapshot.get("decision_lane"))),
        ("当前可加", _percent_or_dash(snapshot.get("current_add_pct"))),
        ("系统仓位上限", _percent_or_dash(snapshot.get("max_position_pct"))),
    ]
    rows = "".join(
        f"<span>{escape(label)}</span><strong>{escape(value)}</strong>"
        for label, value in items
    )
    return f'<section class="trade-signal-drawer-card summary-grid">{rows}</section>'


def _signal_snapshot_reasons_html(snapshot: dict) -> str:
    reasons = [
        *[_signal_reason_label(item) for item in snapshot.get("block_reasons", []) if str(item).strip()],
        *[_signal_reason_label(item) for item in snapshot.get("review_reasons", []) if str(item).strip()],
    ]
    if not reasons and str(snapshot.get("reason_text") or "").strip():
        raw_reasons = [item.strip() for item in str(snapshot.get("reason_text") or "").replace("；", ",").split(",")]
        reasons = [_signal_reason_label(item) for item in raw_reasons if item]
    if reasons:
        body = "".join(f"<li>{escape(reason)}</li>" for reason in reasons[:6])
    else:
        body = "<li>暂无阻断或复核原因。</li>"
    return (
        '<section class="trade-signal-drawer-card">'
        "<h4>阻断 / 复核原因</h4>"
        f"<ul>{body}</ul>"
        "</section>"
    )


def _signal_reason_label(value: object) -> str:
    text = str(value or "").strip()
    labels = {
        "buy_zone": "买区阻断",
        "data_confidence": "数据置信度",
        "valuation_status": "估值状态",
        "entry_rating": "入场评级",
        "risk_rating": "风险评级",
    }
    return labels.get(text, text)


def _signal_snapshot_outcomes_html(
    snapshot: dict,
    outcome_store: DecisionOutcomeStore,
    active_horizon: str,
) -> str:
    snapshot_id = int(snapshot.get("id") or 0)
    rows = []
    for horizon in OUTCOME_HORIZON_DAYS:
        outcome = outcome_store.get_outcome(snapshot_id, horizon) if snapshot_id else None
        active = " active" if horizon == active_horizon else ""
        rows.append(
            (
                f'<div class="trade-signal-outcome-row{active}">'
                f"<b>{escape(horizon)}</b>"
                f"<span>{escape(_outcome_status_text(outcome, horizon, snapshot))}</span>"
                f"<strong>{escape(_percent_or_dash((outcome or {}).get('return_pct')))}</strong>"
                f"<em>{escape(_percent_or_dash((outcome or {}).get('max_drawdown_pct')))}</em>"
                "</div>"
            )
        )
    return (
        '<section class="trade-signal-drawer-card outcomes">'
        "<h4>各周期复盘结果</h4>"
        '<div class="trade-signal-outcome-head"><span>周期</span><span>状态</span><span>收益</span><span>最大回撤</span></div>'
        f"{''.join(rows)}"
        "</section>"
    )


def _outcome_status_detail_html(outcome: dict | None, horizon: str, snapshot: dict, tags: list[dict]) -> str:
    detail = escape(_outcome_status_detail(outcome, horizon, snapshot))
    return f"{detail}{_tag_inline_html(tags)}"


def _tag_inline_html(tags: list[dict]) -> str:
    if not tags:
        return ""
    labels = [_error_tag_label(tag.get("tag")) for tag in tags]
    title = " / ".join(labels)
    suffix = f" +{len(labels) - 1}" if len(labels) > 1 else ""
    return (
        f'<i class="trade-error-inline" title="{escape(title, quote=True)}">'
        f"{escape(labels[0])}{escape(suffix)}</i>"
    )


def _tag_chip_html(tags: list[dict]) -> str:
    if not tags:
        return '<span class="trade-error-chip empty">未标记</span>'
    labels = [_error_tag_label(tag.get("tag")) for tag in tags]
    title = " / ".join(labels)
    suffix = f" +{len(labels) - 1}" if len(labels) > 1 else ""
    return (
        f'<span class="trade-error-chip" title="{escape(title, quote=True)}">'
        f"{escape(labels[0])}{escape(suffix)}</span>"
    )


def _error_tag_label(value: object) -> str:
    return ERROR_TAG_LABELS.get(str(value or ""), "未识别")


def _final_action_label(value: object) -> str:
    text = str(value or "").strip()
    return FINAL_ACTION_LABELS.get(text, text or BLANK_TEXT)


def _lane_label(value: object) -> str:
    text = str(value or "").strip()
    return LANE_LABELS.get(text, text or BLANK_TEXT)


def _outcome_status_text(outcome: dict | None, horizon: str, snapshot: dict) -> str:
    status = str((outcome or {}).get("status") or "").strip()
    if status == "complete":
        return "已完成"
    if _observation_window_pending(snapshot, horizon):
        return "观察期未到"
    if (outcome or {}).get("start_price") is None:
        return "缺少起始价格"
    return "缺少后续价格"


def _outcome_status_detail(outcome: dict | None, horizon: str, snapshot: dict) -> str:
    if str((outcome or {}).get("status") or "").strip() == "complete":
        return horizon
    return f"{horizon} / {_outcome_status_reason(outcome, horizon, snapshot)}"


def _outcome_status_reason(outcome: dict | None, horizon: str, snapshot: dict) -> str:
    if _observation_window_pending(snapshot, horizon):
        due = _outcome_due_date(snapshot, horizon)
        return "观察期未到" if due is None else f"观察期未到，预计 {due.isoformat()}"
    if (outcome or {}).get("start_price") is None:
        return "缺少起始价格"
    if not outcome:
        return "尚未刷新复盘结果"
    if outcome.get("end_price") is None:
        return "缺少后续价格"
    return "样本未完成"


def _observation_window_pending(snapshot: dict, horizon: str) -> bool:
    due = _outcome_due_date(snapshot, horizon)
    return bool(due and due > date.today())


def _outcome_due_date(snapshot: dict, horizon: str) -> date | None:
    days = OUTCOME_HORIZON_DAYS.get(str(horizon))
    decision_date = _parse_iso_date(snapshot.get("decision_date"))
    if days is None or decision_date is None:
        return None
    return decision_date + timedelta(days=days)


def _parse_iso_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _entry_row_html(entry: dict) -> str:
    return (
        "<tr>"
        f"<td>{_cell_html(_text(entry.get('trade_date')), _created_text(entry))}</td>"
        f'<td class="symbol">{escape(_text(entry.get("symbol")))}</td>'
        f"<td>{_action_badge(entry)}</td>"
        f"<td>{_cell_html(_quantity_text(entry.get('quantity')), _money_text(entry.get('price')))}</td>"
        f"<td>{_option_text(entry)}</td>"
        f"<td>{escape(_snapshot_text(entry.get('decision_snapshot_id')))}</td>"
        f'<td class="notes">{escape(_text(entry.get("notes")))}</td>'
        f'<td class="trade-entry-actions"><span class="zhx-action-group trade-entry-action-group">{_entry_delete_action_html(entry)}</span></td>'
        "</tr>"
    )


def _entry_delete_action_html(entry: dict) -> str:
    entry_id = int(entry.get("id") or 0)
    if entry_id <= 0:
        return BLANK_TEXT
    return (
        f'<a class="trade-entry-delete-link" href="?page=trade-journal&deleteTrade={entry_id}#trade-journal-list" '
        'target="_self" title="删除这条交易记录">删除</a>'
    )


def _entry_delete_summary(entry: dict) -> str:
    parts = [
        _text(entry.get("trade_date")),
        _text(entry.get("symbol")),
        ACTION_LABELS.get(str(entry.get("action_type") or ""), "未识别"),
    ]
    quantity = _quantity_text(entry.get("quantity"))
    price = _money_text(entry.get("price"))
    if quantity != BLANK_TEXT:
        parts.append(f"{quantity} 股")
    if price != BLANK_TEXT:
        parts.append(price)
    return " · ".join(part for part in parts if part and part != BLANK_TEXT)


def _cell_html(primary: str, secondary: str) -> str:
    return (
        '<div class="trade-journal-cell">'
        f"<b>{escape(primary)}</b>"
        f"<span>{escape(secondary)}</span>"
        "</div>"
    )


def _action_badge(entry: dict) -> str:
    action = str(entry.get("action_type") or "")
    label = ACTION_LABELS.get(action, "未识别")
    tone = {
        "buy": "buy",
        "add": "buy",
        "sell": "sell",
        "trim": "sell",
        "sell_put": "option",
        "covered_call": "option",
        "skip": "skip",
    }.get(action, "skip")
    return f'<span class="trade-action-badge {escape(tone)}">{escape(label)}</span>'


def _option_text(entry: dict) -> str:
    premium = _money_text(entry.get("premium"))
    strike = _money_text(entry.get("strike_price"))
    expiry = _text(entry.get("expiry_date"))
    if premium == BLANK_TEXT and strike == BLANK_TEXT and expiry == BLANK_TEXT:
        return BLANK_TEXT
    return (
        '<div class="trade-journal-cell">'
        f"<b>{escape('权利金 ' + premium)}</b>"
        f"<span>{escape('行权价 ' + strike + ' / 到期 ' + expiry)}</span>"
        "</div>"
    )


def _created_text(entry: dict) -> str:
    created = str(entry.get("created_at") or "")
    return created[:16].replace("T", " ") if created else BLANK_TEXT


def _quantity_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:,.4g}"


def _money_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return format_currency(number)


def _percent_or_dash(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return format_percent(number)


def _int_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "0"
    return str(int(number))


def _snapshot_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return str(int(number))


def _query_int(key: str) -> int | None:
    value = st.query_params.get(key)
    try:
        number = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _clear_trade_delete_query() -> None:
    if "deleteTrade" in st.query_params:
        st.query_params.pop("deleteTrade")


def _text(value: object) -> str:
    text = str(value or "").strip()
    return text if text else BLANK_TEXT


def _number(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _friendly_error(message: str) -> str:
    if "symbol is required" in message:
        return "请填写股票代码。"
    if "action_type is invalid" in message:
        return "请选择有效的操作类型。"
    if "must be a number" in message:
        return "数量、价格、权利金和行权价需要填写数字。"
    if "cannot be negative" in message:
        return "数量、价格、权利金和行权价不能为负数。"
    if "must be an integer" in message:
        return "关联信号 ID 需要填写整数。"
    return "保存失败，请检查输入。"


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .trade-journal-filter-note {
            height: 100%;
            display: flex;
            align-items: end;
            justify-content: flex-end;
            padding-top: 1.55rem;
            color: var(--zhx-muted);
            font-size: 0.78rem;
        }
        .trade-journal-toolbar-note {
            display: flex;
            align-items: center;
            min-height: 2.15rem;
            color: var(--zhx-muted);
            font-size: 0.8rem;
        }
        .trade-workbench-section {
            margin: 0.68rem 0 0.42rem;
            padding: 0.35rem 0 0.28rem;
            border-top: 1px solid rgba(15, 23, 42, 0.07);
            color: #0f172a;
            font-size: 0.92rem;
            font-weight: 860;
            letter-spacing: 0;
        }
        .trade-workbench-section.replay {
            margin-top: 1rem;
        }
        .trade-journal-refresh-note {
            display: flex;
            align-items: center;
            min-height: 2.15rem;
            color: #7b8798;
            font-size: 0.76rem;
        }
        .trade-refresh-result {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.45rem;
            margin: 0.45rem 0 0.75rem;
            padding: 0.45rem;
            border: 1px solid rgba(79, 157, 120, 0.16);
            border-radius: 8px;
            background: rgba(79, 157, 120, 0.065);
        }
        .trade-refresh-result-item {
            padding: 0.45rem 0.58rem;
            border-right: 1px solid rgba(79, 157, 120, 0.14);
        }
        .trade-refresh-result-item:last-child {
            border-right: 0;
        }
        .trade-refresh-result-item span {
            display: block;
            color: #64748b;
            font-size: 0.68rem;
            font-weight: 760;
        }
        .trade-refresh-result-item strong {
            display: block;
            margin-top: 0.12rem;
            color: #0f172a;
            font-size: 0.98rem;
            font-weight: 860;
        }
        .trade-journal-summary {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.5rem;
            margin: 0.7rem 0 1rem;
            padding: 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 8px;
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.74), rgba(248, 250, 252, 0.84));
        }
        .trade-journal-summary-item {
            min-height: 66px;
            padding: 0.55rem 0.65rem;
            border-right: 1px solid rgba(15, 23, 42, 0.07);
        }
        .trade-journal-summary-item:last-child {
            border-right: 0;
        }
        .trade-journal-summary-item span {
            display: block;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 760;
        }
        .trade-journal-summary-item strong {
            display: block;
            margin-top: 0.18rem;
            color: #0f172a;
            font-size: 1.18rem;
            font-weight: 860;
            line-height: 1.1;
        }
        .trade-journal-summary-item em {
            display: block;
            margin-top: 0.18rem;
            color: #a1aab8;
            font-size: 0.64rem;
            font-style: normal;
            font-weight: 760;
        }
        .trade-journal-summary.signal {
            grid-template-columns: repeat(6, minmax(0, 1fr));
            margin-top: 0.55rem;
        }
        .trade-journal-summary-item.signal strong {
            font-size: 1.06rem;
        }
        .trade-journal-subsection {
            margin: 0.95rem 0 0.42rem;
            color: #0f172a;
            font-size: 0.86rem;
            font-weight: 820;
        }
        .trade-error-compact-empty {
            display: flex;
            align-items: center;
            min-height: 40px;
            padding: 0.48rem 0.62rem;
            border: 1px dashed rgba(15, 23, 42, 0.12);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.74);
            color: #7b8798;
            font-size: 0.74rem;
        }
        .trade-missing-brief {
            display: grid;
            grid-template-columns: 88px minmax(0, 1fr);
            align-items: start;
            gap: 0.55rem;
            margin: 0.3rem 0 0.5rem;
            padding: 0.5rem 0.62rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.68);
        }
        .trade-missing-brief.empty {
            border-style: dashed;
            background: rgba(248, 250, 252, 0.48);
        }
        .trade-missing-brief strong {
            color: #334155;
            font-size: 0.72rem;
            font-weight: 780;
        }
        .trade-missing-brief div {
            display: flex;
            flex-wrap: wrap;
            gap: 0.32rem;
            min-width: 0;
        }
        .trade-missing-brief span,
        .trade-missing-brief i {
            display: inline-flex;
            align-items: center;
            gap: 0.25rem;
            min-height: 22px;
            padding: 0 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 999px;
            background: #FFFFFF;
            color: #64748b;
            font-size: 0.66rem;
            font-style: normal;
            white-space: nowrap;
        }
        .trade-missing-brief span b {
            color: #0f172a;
            font-size: 0.68rem;
            font-weight: 820;
        }
        .trade-missing-brief span em {
            color: #64748b;
            font-style: normal;
        }
        .trade-error-summary-card {
            min-height: 124px;
            padding: 0.62rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.78);
        }
        .trade-error-count-row,
        .trade-error-case-row {
            display: grid;
            gap: 0.08rem;
            padding: 0.35rem 0;
            border-bottom: 1px solid rgba(15, 23, 42, 0.06);
        }
        .trade-error-count-row {
            grid-template-columns: 1fr auto;
            align-items: center;
        }
        .trade-error-count-row:last-child,
        .trade-error-case-row:last-child {
            border-bottom: 0;
        }
        .trade-error-count-row span,
        .trade-error-case-row span,
        .trade-error-case-row em {
            color: #7b8798;
            font-size: 0.68rem;
            font-style: normal;
        }
        .trade-error-count-row strong,
        .trade-error-case-row strong {
            color: #0f172a;
            font-size: 0.76rem;
            font-weight: 820;
        }
        .trade-error-muted {
            display: flex;
            align-items: center;
            min-height: 82px;
            color: #94a3b8;
            font-size: 0.76rem;
        }
        .trade-terminal-table-wrap {
            --trade-terminal-border: rgba(15, 23, 42, 0.08);
            --trade-terminal-line: rgba(15, 23, 42, 0.055);
            --trade-terminal-head: #F8FAFC;
            --trade-terminal-hover: #FBFCFE;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) {
            display: grid !important;
            grid-template-columns: minmax(610px, 1fr) 110px;
            gap: 0 !important;
            min-height: 42px;
            margin: -1px 0 0 !important;
            border-right: 1px solid var(--trade-terminal-border, rgba(15, 23, 42, 0.08));
            border-bottom: 1px solid var(--trade-terminal-line, rgba(15, 23, 42, 0.055));
            border-left: 1px solid var(--trade-terminal-border, rgba(15, 23, 42, 0.08));
            background: #FFFFFF;
            box-sizing: border-box;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row):hover {
            background: var(--trade-terminal-hover, #FBFCFE);
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) > div {
            padding: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) > div:last-child {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 110px;
            min-width: 110px;
            min-height: 42px;
            padding: 0 8px !important;
            border-left: 0;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) > div:last-child
        div[data-testid="stHorizontalBlock"] {
            gap: 8px !important;
            width: max-content;
            min-width: 92px;
            padding: 0;
            border: 0;
            border-radius: 0;
            background: transparent;
        }
        .trade-snapshot-row {
            display: grid;
            grid-template-columns: 80px 110px 160px minmax(260px, 1fr);
            gap: 0;
            align-items: center;
            min-height: 42px;
            padding: 0 12px;
            border: 0;
            background: transparent;
        }
        .trade-snapshot-cell {
            display: grid;
            gap: 0.08rem;
            min-height: 38px;
            align-content: center;
            padding: 0;
            border-bottom: 0;
            background: transparent;
        }
        .trade-snapshot-cell b {
            color: #0f172a;
            font-size: 12px;
            line-height: 1.1;
            font-weight: 700;
        }
        .trade-snapshot-cell span {
            color: #64748B;
            font-size: 11px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-error-chip-line {
            display: flex;
            align-items: center;
            gap: 0;
            min-height: 38px;
            padding: 0;
            border-bottom: 0;
            background: transparent;
            overflow: hidden;
        }
        .trade-error-chip {
            display: inline-flex;
            align-items: center;
            max-width: 86px;
            height: 18px;
            padding: 0 0.32rem;
            border: 1px solid rgba(82, 101, 127, 0.10);
            border-radius: 999px;
            background: rgba(82, 101, 127, 0.035);
            color: #64748b;
            font-size: 11px;
            font-weight: 620;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .trade-error-chip.empty {
            border-color: transparent;
            background: transparent;
            color: #94a3b8;
            padding-left: 0;
            padding-right: 0;
        }
        .trade-error-inline {
            display: inline-flex;
            align-items: center;
            max-width: 96px;
            height: 17px;
            margin-left: 0.42rem;
            padding: 0 0.32rem;
            border: 1px solid rgba(82, 101, 127, 0.10);
            border-radius: 999px;
            background: rgba(82, 101, 127, 0.035);
            color: #64748b;
            font-size: 11px;
            font-style: normal;
            font-weight: 620;
            line-height: 1;
            vertical-align: middle;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .trade-error-editor-head {
            margin: 0.72rem 0 0.55rem;
            padding: 0.58rem 0.72rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
        }
        .trade-error-editor-head strong,
        .trade-error-editor-head span {
            display: block;
        }
        .trade-error-editor-head strong {
            color: #0f172a;
            font-size: 0.86rem;
        }
        .trade-error-editor-head span {
            margin-top: 0.12rem;
            color: #7b8798;
            font-size: 0.7rem;
        }
        .trade-snapshot-list-head {
            display: grid;
            grid-template-columns: 80px 110px 160px minmax(260px, 1fr) 110px;
            gap: 0;
            align-items: center;
            min-height: 30px;
            padding: 0 12px;
            border: 0;
            border-bottom: 1px solid var(--trade-terminal-line, rgba(15, 23, 42, 0.055));
            border-radius: 0;
            background: var(--trade-terminal-head, #F8FAFC);
        }
        .trade-snapshot-list-head span {
            color: #64748B;
            font-size: 11px;
            font-weight: 650;
            white-space: nowrap;
        }
        .trade-snapshot-list-head span:last-child {
            text-align: center;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) [data-testid="stButton"] button {
            min-height: 26px !important;
            height: 26px !important;
            padding: 0 0.16rem !important;
            border-radius: 4px !important;
            border-color: transparent !important;
            background: transparent !important;
            color: #475569 !important;
            box-shadow: none !important;
            text-decoration: none !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) [data-testid="stButton"] button p {
            font-size: 12px !important;
            font-weight: 720 !important;
            line-height: 1;
            text-decoration: none !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) [class*="st-key-trade-snapshot-view-"] button {
            color: #334155 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) [class*="st-key-trade-snapshot-delete-"] button {
            padding: 0 0.1rem !important;
            border-color: transparent !important;
            background: transparent !important;
            color: #64748B !important;
            font-weight: 650 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) [class*="st-key-trade-snapshot-delete-"] button p {
            font-weight: 650 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(.trade-snapshot-row) [data-testid="stButton"] button:hover {
            border-color: rgba(15, 23, 42, 0.08) !important;
            background: #FFFFFF !important;
            color: #0f172a !important;
        }
        div[class*="st-key-trade-signal-drawer-container"] {
            position: fixed;
            top: 0;
            right: 0;
            z-index: 99999;
            width: min(440px, calc(100vw - 28px));
            height: 100vh;
            padding: 1rem 1rem 1.2rem !important;
            overflow-y: auto;
            border-left: 1px solid rgba(15, 23, 42, 0.12);
            background: #FFFFFF;
            box-shadow: -18px 0 36px rgba(15, 23, 42, 0.12);
        }
        div[class*="st-key-trade-signal-drawer-container"] [data-testid="stVerticalBlock"] {
            gap: 0.58rem;
        }
        div[class*="st-key-trade-signal-drawer-container"] [data-testid="stButton"] button {
            min-height: 28px !important;
            height: 28px !important;
            padding: 0 0.48rem !important;
            border-radius: 6px !important;
            border-color: rgba(15, 23, 42, 0.10) !important;
            background: #FFFFFF !important;
            color: #52657F !important;
            box-shadow: none !important;
        }
        div[class*="st-key-trade-signal-drawer-container"] [data-testid="stButton"] button p {
            font-size: 12px !important;
            font-weight: 680 !important;
        }
        div[class*="st-key-trade-signal-drawer-container"] [data-testid="stButton"] button:hover {
            border-color: rgba(15, 23, 42, 0.16) !important;
            color: #0F172A !important;
        }
        .trade-signal-drawer-marker {
            height: 0;
            margin: 0;
            padding: 0;
        }
        .trade-signal-drawer-head {
            padding-bottom: 0.72rem;
            border-bottom: 1px solid rgba(15, 23, 42, 0.08);
        }
        .trade-signal-drawer-head span,
        .trade-signal-drawer-head em {
            display: block;
            color: #64748B;
            font-size: 11px;
            font-style: normal;
            font-weight: 600;
        }
        .trade-signal-drawer-head strong {
            display: block;
            margin-top: 0.14rem;
            color: #0F172A;
            font-size: 26px;
            line-height: 1;
            font-weight: 780;
        }
        .trade-signal-drawer-card {
            margin-top: 0.68rem;
            padding: 0.72rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
        }
        .trade-signal-drawer-card h4 {
            margin: 0 0 0.5rem;
            color: #0F172A;
            font-size: 12px;
            font-weight: 760;
        }
        .trade-signal-drawer-card.summary-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.52rem 0.7rem;
        }
        .trade-signal-drawer-card.summary-grid span {
            display: block;
            color: #64748B;
            font-size: 11px;
            font-weight: 600;
        }
        .trade-signal-drawer-card.summary-grid strong {
            display: block;
            margin-top: 0.12rem;
            color: #0F172A;
            font-size: 13px;
            font-weight: 740;
        }
        .trade-signal-drawer-card ul {
            margin: 0;
            padding-left: 1rem;
            color: #475569;
            font-size: 12px;
            line-height: 1.55;
        }
        .trade-signal-outcome-head,
        .trade-signal-outcome-row {
            display: grid;
            grid-template-columns: 42px minmax(0, 1fr) 68px 72px;
            align-items: center;
            gap: 0.42rem;
            min-height: 28px;
            border-bottom: 1px solid rgba(15, 23, 42, 0.055);
        }
        .trade-signal-outcome-head {
            color: #64748B;
            font-size: 10.5px;
            font-weight: 650;
        }
        .trade-signal-outcome-row:last-child {
            border-bottom: 0;
        }
        .trade-signal-outcome-row.active {
            background: rgba(37, 99, 235, 0.035);
        }
        .trade-signal-outcome-row b,
        .trade-signal-outcome-row strong,
        .trade-signal-outcome-row em,
        .trade-signal-outcome-row span {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            font-size: 11.5px;
            font-style: normal;
        }
        .trade-signal-outcome-row b,
        .trade-signal-outcome-row strong {
            color: #0F172A;
            font-weight: 720;
        }
        .trade-signal-outcome-row span,
        .trade-signal-outcome-row em {
            color: #64748B;
            font-weight: 600;
        }
        .trade-journal-table-wrap {
            overflow-x: auto;
            margin-top: 0.28rem;
            border: 1px solid var(--trade-terminal-border, rgba(15, 23, 42, 0.08));
            border-radius: 8px;
            background: #FFFFFF;
            box-shadow: none;
        }
        .trade-terminal-table-wrap {
            margin-top: 0.28rem;
            border: 1px solid var(--trade-terminal-border, rgba(15, 23, 42, 0.08));
            border-radius: 8px;
            background: #FFFFFF;
            overflow: hidden;
            box-shadow: none;
        }
        .trade-journal-table-wrap.signal {
            margin-top: 0.35rem;
        }
        .trade-journal-table {
            width: 100%;
            min-width: 1040px;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 12px;
        }
        .trade-journal-table.signal {
            min-width: 620px;
        }
        .trade-journal-table th {
            height: 30px;
            padding: 0 12px;
            border-bottom: 1px solid var(--trade-terminal-line, rgba(15, 23, 42, 0.055));
            background: var(--trade-terminal-head, #F8FAFC);
            color: #64748B;
            font-size: 11px;
            font-weight: 650;
            text-align: left;
        }
        .trade-journal-table td {
            height: 42px;
            padding: 0 12px;
            border-bottom: 1px solid var(--trade-terminal-line, rgba(15, 23, 42, 0.055));
            color: #0f172a;
            vertical-align: middle;
        }
        .trade-journal-table tr:last-child td {
            border-bottom: 0;
        }
        .trade-journal-table tr:hover td {
            background: var(--trade-terminal-hover, #FBFCFE);
        }
        .trade-journal-table .symbol {
            width: 96px;
            font-size: 12px;
            font-weight: 780;
        }
        .trade-journal-table .notes {
            max-width: 100%;
            color: #64748b;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-entry-actions {
            text-align: center;
            width: 120px;
        }
        .trade-entry-action-group {
            min-width: 72px;
            padding: 0;
            border: 0;
            background: transparent;
            margin: 0 auto;
        }
        .trade-entry-actions::after {
            content: "";
            display: inline-flex;
            vertical-align: middle;
        }
        .trade-entry-delete-link,
        .trade-entry-delete-link:visited {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            height: 26px;
            min-width: 38px;
            padding: 0 0.2rem;
            border: 1px solid transparent;
            border-radius: 4px;
            background: transparent;
            color: #52657F;
            font-size: 12px;
            font-weight: 650;
            text-decoration: none !important;
            white-space: nowrap;
        }
        .trade-entry-delete-link:hover {
            border-color: rgba(15, 23, 42, 0.08);
            background: #FFFFFF;
            color: #0F172A;
            text-decoration: none !important;
        }
        .trade-delete-confirm {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.8rem;
            margin: 0.45rem 0 0.35rem;
            padding: 0.62rem 0.72rem;
            border: 1px solid rgba(181, 106, 50, 0.16);
            border-radius: 8px;
            background: linear-gradient(180deg, rgba(255, 251, 235, 0.82), rgba(255, 247, 237, 0.66));
        }
        .trade-delete-confirm span,
        .trade-delete-confirm em {
            color: #8A4B00;
            font-size: 0.68rem;
            font-style: normal;
        }
        .trade-delete-confirm strong {
            display: block;
            margin-top: 0.1rem;
            color: #0f172a;
            font-size: 0.78rem;
            font-weight: 820;
        }
        .trade-journal-table .empty-row {
            height: 54px;
            color: #94a3b8;
            text-align: center;
        }
        .trade-journal-cell {
            display: grid;
            gap: 0.04rem;
            min-width: 0;
            line-height: 1.12;
        }
        .trade-journal-cell b {
            color: #0f172a;
            font-size: 12px;
            line-height: 1.1;
            font-weight: 720;
        }
        .trade-journal-cell span {
            color: #64748B;
            font-size: 11px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-action-badge {
            display: inline-flex;
            align-items: center;
            height: 18px;
            min-height: 18px;
            padding: 0 0.42rem;
            border-radius: 999px;
            border: 1px solid rgba(15, 23, 42, 0.08);
            background: #F8FAFC;
            color: #52657f;
            font-size: 11px;
            font-weight: 650;
            white-space: nowrap;
        }
        .trade-action-badge.buy {
            border-color: rgba(79, 157, 120, 0.18);
            background: rgba(79, 157, 120, 0.08);
            color: #276749;
        }
        .trade-action-badge.sell {
            border-color: rgba(181, 106, 50, 0.18);
            background: rgba(181, 106, 50, 0.08);
            color: #8A4B00;
        }
        .trade-action-badge.option {
            border-color: rgba(82, 101, 127, 0.16);
            background: rgba(82, 101, 127, 0.08);
            color: #475569;
        }
        .trade-action-badge.skip {
            color: #7b8798;
        }
        .trade-journal-empty {
            padding: 1rem;
            border: 1px dashed rgba(15, 23, 42, 0.14);
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
            text-align: center;
        }
        .trade-journal-empty strong {
            display: block;
            color: #0f172a;
            font-size: 0.95rem;
        }
        .trade-journal-empty span {
            display: block;
            margin-top: 0.25rem;
            color: #7b8798;
            font-size: 0.78rem;
        }
        .trade-journal-empty.signal-empty {
            margin-top: 0.5rem;
        }
        .trade-journal-empty.signal-empty strong {
            font-size: 0.92rem;
        }
        [data-testid="stRadio"] label {
            color: var(--zhx-muted);
            font-size: 0.76rem;
        }
        [data-testid="stRadio"] [role="radiogroup"] {
            gap: 0.25rem;
        }
        [data-testid="stRadio"] [role="radiogroup"] label {
            min-height: 30px;
            padding: 0.16rem 0.58rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 999px;
            background: #FFFFFF;
        }
        [data-testid="stExpander"] {
            border-color: rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.78);
        }
        [data-testid="stFormSubmitButton"] button {
            background: #0B1220 !important;
            border-color: #0B1220 !important;
            color: #F8FAFC !important;
        }
        @media (max-width: 1100px) {
            .trade-journal-summary,
            .trade-journal-summary.signal {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            .trade-journal-summary-item {
                border-right: 0;
                border-bottom: 1px solid rgba(15, 23, 42, 0.06);
            }
        }
        @media (max-width: 720px) {
            .trade-journal-summary,
            .trade-journal-summary.signal {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
