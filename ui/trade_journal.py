from __future__ import annotations

from datetime import date
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
                "决策快照 ID（可选）",
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

    headers = ["日期", "股票", "操作", "数量 / 价格", "期权参数", "决策快照", "备注"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    row_html = "".join(_entry_row_html(entry) for entry in entries)
    st.markdown(
        (
            '<div class="trade-journal-table-wrap">'
            '<table class="trade-journal-table">'
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{row_html}</tbody>"
            "</table>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )


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
                "<strong>当前周期暂无完整复盘样本，刷新 outcome 后再查看统计。</strong>"
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
    _render_error_tag_management(decision_store, outcome_store, error_tag_store, selected)


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
    _render_snapshot_rows(snapshots, outcome_store, error_tag_store, horizon)
    selected_snapshot = _selected_snapshot(snapshots)
    if selected_snapshot:
        _render_error_tag_editor(selected_snapshot, error_tag_store)


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


def _render_snapshot_rows(
    snapshots: list[dict],
    outcome_store: DecisionOutcomeStore,
    error_tag_store: DecisionErrorTagStore,
    horizon: str,
) -> None:
    st.markdown(
        '<div class="trade-snapshot-list-head"><span>股票</span><span>日期</span><span>系统动作</span><span>周期状态</span><span>错误标签</span><span>操作</span></div>',
        unsafe_allow_html=True,
    )
    for snapshot in snapshots:
        snapshot_id = int(snapshot.get("id") or 0)
        tags = error_tag_store.list_tags_for_snapshot(snapshot_id)
        outcome = outcome_store.get_outcome(snapshot_id, horizon) if snapshot_id else None
        cols = st.columns([0.8, 0.9, 1.2, 0.9, 1.35, 0.8])
        cols[0].markdown(
            f'<div class="trade-snapshot-cell"><b>{escape(_text(snapshot.get("symbol")))}</b></div>',
            unsafe_allow_html=True,
        )
        cols[1].markdown(
            f'<div class="trade-snapshot-cell"><b>{escape(_text(snapshot.get("decision_date")))}</b></div>',
            unsafe_allow_html=True,
        )
        cols[2].markdown(
            f'<div class="trade-snapshot-cell"><b>{escape(_final_action_label(snapshot.get("final_action")))}</b>'
            f'<span>{escape(_lane_label(snapshot.get("decision_lane")))}</span></div>',
            unsafe_allow_html=True,
        )
        cols[3].markdown(
            f'<div class="trade-snapshot-cell"><b>{escape(_outcome_status_text(outcome))}</b>'
            f'<span>{escape(horizon)}</span></div>',
            unsafe_allow_html=True,
        )
        cols[4].markdown(
            f'<div class="trade-error-chip-line">{_tag_chip_html(tags)}</div>',
            unsafe_allow_html=True,
        )
        if cols[5].button("标记错误", key=f"trade-error-select-{snapshot_id}", width="stretch"):
            st.session_state["trade_error_snapshot_id"] = snapshot_id
            st.session_state.pop("trade_error_edit_tag", None)
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
            st.session_state.pop("trade_error_snapshot_id", None)
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


def _complete_stat_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if int(row.get("sampleCount") or 0) > 0]


def _selected_snapshot(snapshots: list[dict]) -> dict | None:
    selected_id = int(st.session_state.get("trade_error_snapshot_id") or 0)
    if selected_id:
        for snapshot in snapshots:
            if int(snapshot.get("id") or 0) == selected_id:
                return snapshot
    return None


def _tag_chip_html(tags: list[dict]) -> str:
    if not tags:
        return '<span class="trade-error-chip empty">未标记</span>'
    return "".join(
        f'<span class="trade-error-chip">{escape(_error_tag_label(tag.get("tag")))}</span>'
        for tag in tags[:3]
    )


def _error_tag_label(value: object) -> str:
    return ERROR_TAG_LABELS.get(str(value or ""), "未识别")


def _final_action_label(value: object) -> str:
    text = str(value or "").strip()
    return FINAL_ACTION_LABELS.get(text, text or BLANK_TEXT)


def _lane_label(value: object) -> str:
    text = str(value or "").strip()
    return LANE_LABELS.get(text, text or BLANK_TEXT)


def _outcome_status_text(outcome: dict | None) -> str:
    status = str((outcome or {}).get("status") or "").strip()
    if status == "complete":
        return "已完成"
    if status == "missing" or not outcome:
        return "缺失"
    return status or "缺失"


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
        "</tr>"
    )


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
        return "决策快照 ID 需要填写整数。"
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
        .trade-snapshot-cell {
            display: grid;
            gap: 0.08rem;
            min-height: 2.25rem;
            align-content: center;
            padding: 0.18rem 0;
            border-bottom: 1px solid rgba(15, 23, 42, 0.055);
        }
        .trade-snapshot-cell b {
            color: #0f172a;
            font-size: 0.76rem;
            line-height: 1.1;
        }
        .trade-snapshot-cell span {
            color: #7b8798;
            font-size: 0.66rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-error-chip-line {
            display: flex;
            align-items: center;
            gap: 0.22rem;
            min-height: 2.25rem;
            border-bottom: 1px solid rgba(15, 23, 42, 0.055);
            overflow: hidden;
        }
        .trade-error-chip {
            display: inline-flex;
            align-items: center;
            height: 22px;
            padding: 0 0.48rem;
            border: 1px solid rgba(181, 106, 50, 0.16);
            border-radius: 999px;
            background: rgba(181, 106, 50, 0.07);
            color: #8A4B00;
            font-size: 0.64rem;
            font-weight: 780;
            white-space: nowrap;
        }
        .trade-error-chip.empty {
            border-color: rgba(15, 23, 42, 0.08);
            background: #F8FAFC;
            color: #94a3b8;
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
            grid-template-columns: 0.8fr 0.9fr 1.2fr 0.9fr 1.35fr 0.8fr;
            gap: 0.55rem;
            align-items: center;
            min-height: 30px;
            margin-top: 0.3rem;
            padding: 0 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 8px 8px 0 0;
            background: rgba(248, 250, 252, 0.82);
        }
        .trade-snapshot-list-head span {
            color: #7b8798;
            font-size: 0.65rem;
            font-weight: 780;
            white-space: nowrap;
        }
        .trade-journal-table-wrap {
            overflow-x: auto;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: #FFFFFF;
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.035);
        }
        .trade-journal-table-wrap.signal {
            margin-top: 0.35rem;
        }
        .trade-journal-table {
            width: 100%;
            min-width: 1060px;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 0.72rem;
        }
        .trade-journal-table.signal {
            min-width: 620px;
        }
        .trade-journal-table th {
            height: 30px;
            padding: 0.32rem 0.58rem;
            border-bottom: 1px solid rgba(15, 23, 42, 0.08);
            background: #FAFBFC;
            color: #7b8798;
            font-size: 0.65rem;
            font-weight: 760;
            text-align: left;
        }
        .trade-journal-table td {
            height: 46px;
            padding: 0.36rem 0.58rem;
            border-bottom: 1px solid rgba(15, 23, 42, 0.06);
            color: #0f172a;
            vertical-align: middle;
        }
        .trade-journal-table tr:last-child td {
            border-bottom: 0;
        }
        .trade-journal-table tr:hover td {
            background: #FBFCFE;
        }
        .trade-journal-table .symbol {
            width: 96px;
            font-size: 0.82rem;
            font-weight: 860;
        }
        .trade-journal-table .notes {
            max-width: 260px;
            color: #64748b;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-journal-table .empty-row {
            height: 54px;
            color: #94a3b8;
            text-align: center;
        }
        .trade-journal-cell {
            display: grid;
            gap: 0.08rem;
            min-width: 0;
        }
        .trade-journal-cell b {
            color: #0f172a;
            font-size: 0.75rem;
            line-height: 1.1;
            font-weight: 820;
        }
        .trade-journal-cell span {
            color: #7b8798;
            font-size: 0.66rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-action-badge {
            display: inline-flex;
            align-items: center;
            height: 24px;
            padding: 0 0.55rem;
            border-radius: 999px;
            border: 1px solid rgba(15, 23, 42, 0.08);
            background: #F8FAFC;
            color: #52657f;
            font-size: 0.66rem;
            font-weight: 800;
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
