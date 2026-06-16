from __future__ import annotations

from collections.abc import Callable
from html import escape
from typing import Any

import streamlit as st

from data.trade_intent import BUY_INTENT_FIELDS, INTENT_FIELD_LABELS, SELL_INTENT_FIELDS, intent_title


def render_trade_intent_dialog(
    *,
    side: str,
    ticker: str,
    action_label: str,
    key_prefix: str,
    on_confirm: Callable[[dict[str, str]], None],
    on_cancel: Callable[[], None],
) -> None:
    title = intent_title(side)

    def body() -> None:
        st.markdown(f"#### {title}")
        st.caption("只记录这笔交易的真实意图，方便之后复盘；不评价对错，也不会改变保存逻辑。")
        symbol_text = str(ticker or "").strip().upper() or "未选择"
        action_text = str(action_label or "").strip() or "记录交易"
        st.markdown(f"**交易对象：{symbol_text}｜{action_text}**")
        with st.form(f"{key_prefix}-form"):
            payload: dict[str, str] = {"intent_side": "sell" if side == "sell" else "buy"}
            fields = SELL_INTENT_FIELDS if side == "sell" else BUY_INTENT_FIELDS
            for field, options in fields.items():
                payload[field] = st.radio(
                    INTENT_FIELD_LABELS[field],
                    options,
                    horizontal=True,
                    key=f"{key_prefix}-{field}",
                )
            cols = st.columns(2)
            confirm = cols[0].form_submit_button("确认并继续保存", type="primary", width="stretch")
            cancel = cols[1].form_submit_button("取消", width="stretch")
        if confirm:
            on_confirm(payload)
        if cancel:
            on_cancel()

    if hasattr(st, "dialog"):
        @st.dialog(title)
        def dialog_body() -> None:
            body()

        dialog_body()
    else:  # pragma: no cover - compatibility for older Streamlit runtimes
        st.warning(title)
        body()


def intent_record_html(intent: dict[str, Any] | None) -> str:
    if not intent:
        return '<div class="trade-intent-empty">这条交易尚未保存交易前意图。</div>'
    side = str(intent.get("intent_side") or "")
    title = intent_title(side)
    items = [
        ("主要意图", intent.get("primary_intent")),
        ("仓位意图", intent.get("position_intent")),
        ("触发原因", intent.get("timing_intent")),
        ("当下状态", intent.get("risk_intent")),
    ]
    body = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(str(value or '未记录'))}</strong></div>"
        for label, value in items
    )
    return (
        '<section class="trade-intent-record">'
        f"<h4>{title}</h4>"
        f'<div class="trade-intent-grid">{body}</div>'
        "</section>"
    )
