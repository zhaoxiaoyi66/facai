from __future__ import annotations

from datetime import date
from html import escape

import streamlit as st

from data.decision_log import TradeJournalStore
from formatting import format_currency
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
BLANK_TEXT = "—"


def render() -> None:
    _render_styles()
    render_page_header("交易日志", "手动记录真实操作和放弃动作，保留执行上下文。")

    store = TradeJournalStore()
    _render_notice()
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
        .trade-journal-table-wrap {
            overflow-x: auto;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: #FFFFFF;
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.035);
        }
        .trade-journal-table {
            width: 100%;
            min-width: 1060px;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 0.72rem;
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
            .trade-journal-summary {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            .trade-journal-summary-item {
                border-right: 0;
                border-bottom: 1px solid rgba(15, 23, 42, 0.06);
            }
        }
        @media (max-width: 720px) {
            .trade-journal-summary {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
