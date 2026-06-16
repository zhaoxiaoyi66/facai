from __future__ import annotations

from collections.abc import Callable
from html import escape
from typing import Any

import streamlit as st

from data.trade_intent import (
    BUY_INTENT_QUESTIONS,
    SELL_INTENT_QUESTIONS,
    buy_intent_attention_points,
    intent_title,
    sell_intent_attention_points,
)


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
        if side == "buy":
            st.caption("买入前先记录这笔交易的原因。\n\n这不是拦截，只是为了以后复盘。")
            _render_buy_intent_body(
                ticker=ticker,
                action_label=action_label,
                key_prefix=key_prefix,
                on_confirm=on_confirm,
                on_cancel=on_cancel,
            )
            return
        if side == "sell":
            st.caption("卖出前先记录这笔交易的原因。\n\n这不是拦截，只是为了以后复盘。")
            _render_sell_intent_body(
                ticker=ticker,
                action_label=action_label,
                key_prefix=key_prefix,
                on_confirm=on_confirm,
                on_cancel=on_cancel,
            )
            return
        st.caption("只记录这笔交易的真实意图，方便之后复盘；不评价对错，也不会改变保存逻辑。")
        symbol_text = str(ticker or "").strip().upper() or "未选择"
        action_text = str(action_label or "").strip() or "记录交易"
        st.markdown(f"**交易对象：{symbol_text}｜{action_text}**")
        cols = st.columns(2)
        if cols[0].button("确认并记录", type="primary", width="stretch", key=f"{key_prefix}-confirm-fallback"):
            on_confirm({"intent_side": side})
        if cols[1].button("返回修改", width="stretch", key=f"{key_prefix}-cancel-fallback"):
            on_cancel()

    if hasattr(st, "dialog"):
        @st.dialog(title)
        def dialog_body() -> None:
            body()

        dialog_body()
    else:  # pragma: no cover - compatibility for older Streamlit runtimes
        st.warning(title)
        body()


def _render_buy_intent_body(
    *,
    ticker: str,
    action_label: str,
    key_prefix: str,
    on_confirm: Callable[[dict[str, str]], None],
    on_cancel: Callable[[], None],
) -> None:
    symbol_text = str(ticker or "").strip().upper() or "未选择"
    action_text = str(action_label or "").strip() or "记录交易"
    st.markdown(f"**交易对象：{symbol_text}｜{action_text}**")
    payload: dict[str, str] = {"intent_side": "buy"}
    for index, item in enumerate(BUY_INTENT_QUESTIONS, start=1):
        field = str(item["field"])
        options = list(item["options"])
        payload[field] = st.radio(
            f"问题 {index}：{item['question']}",
            options,
            index=2,
            horizontal=False,
            key=f"{key_prefix}-{field}",
        )

    st.info("本次买入意图将随交易记录保存，用于日后复盘。")
    attention_points = buy_intent_attention_points(payload)
    if attention_points:
        points = "、".join(attention_points)
        st.warning(f"本次记录存在复盘关注点：{points}")

    cols = st.columns(2)
    if cols[0].button("确认并记录", type="primary", width="stretch", key=f"{key_prefix}-confirm"):
        on_confirm(payload)
    if cols[1].button("返回修改", width="stretch", key=f"{key_prefix}-cancel"):
        on_cancel()


def _render_sell_intent_body(
    *,
    ticker: str,
    action_label: str,
    key_prefix: str,
    on_confirm: Callable[[dict[str, str]], None],
    on_cancel: Callable[[], None],
) -> None:
    symbol_text = str(ticker or "").strip().upper() or "未选择"
    action_text = str(action_label or "").strip() or "记录交易"
    st.markdown(f"**交易对象：{symbol_text}｜{action_text}**")
    payload: dict[str, str] = {"intent_side": "sell"}
    for index, item in enumerate(SELL_INTENT_QUESTIONS, start=1):
        field = str(item["field"])
        options = list(item["options"])
        payload[field] = st.radio(
            f"问题 {index}：{item['question']}",
            options,
            index=2,
            horizontal=False,
            key=f"{key_prefix}-{field}",
        )

    st.info("本次卖出意图将随交易记录保存，用于日后复盘。")
    attention_points = sell_intent_attention_points(payload)
    if attention_points:
        points = "、".join(attention_points)
        st.warning(f"本次记录存在复盘关注点：{points}")

    cols = st.columns(2)
    if cols[0].button("确认并记录", type="primary", width="stretch", key=f"{key_prefix}-confirm"):
        on_confirm(payload)
    if cols[1].button("返回修改", width="stretch", key=f"{key_prefix}-cancel"):
        on_cancel()


def intent_record_html(intent: dict[str, Any] | None) -> str:
    if not intent:
        return '<div class="trade-intent-empty">这条交易尚未保存交易意图记录。</div>'
    side = str(intent.get("intent_side") or "")
    title = intent_title(side)
    if side == "buy":
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else intent
        items = [(str(item["question"]), payload.get(str(item["field"]))) for item in BUY_INTENT_QUESTIONS]
        body = "".join(
            f"<div><span>{escape(label)}</span><strong>{escape(str(value or '未记录'))}</strong></div>"
            for label, value in items
        )
        return (
            '<section class="trade-intent-record">'
            "<h4>交易意图记录</h4>"
            f'<p class="trade-intent-title">{escape(title)}</p>'
            f'<div class="trade-intent-grid buy">{body}</div>'
            f"{_attention_html(intent)}"
            f"{_snapshot_html(intent)}"
            "</section>"
        )
    if side == "sell":
        payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else intent
        items = [(str(item["question"]), payload.get(str(item["field"]))) for item in SELL_INTENT_QUESTIONS]
        body = "".join(
            f"<div><span>{escape(label)}</span><strong>{escape(str(value or '未记录'))}</strong></div>"
            for label, value in items
        )
        return (
            '<section class="trade-intent-record">'
            "<h4>交易意图记录</h4>"
            f'<p class="trade-intent-title">{escape(title)}</p>'
            f'<div class="trade-intent-grid sell">{body}</div>'
            f"{_attention_html(intent)}"
            f"{_snapshot_html(intent)}"
            "</section>"
        )
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
        "<h4>交易意图记录</h4>"
        f'<p class="trade-intent-title">{escape(title)}</p>'
        f'<div class="trade-intent-grid">{body}</div>'
        f"{_attention_html(intent)}"
        f"{_snapshot_html(intent)}"
        "</section>"
    )


def _attention_html(intent: dict[str, Any]) -> str:
    flags = intent.get("attention_flags")
    if not isinstance(flags, list):
        flags = []
    if not flags:
        return '<div class="trade-intent-attention muted">复盘关注点：无</div>'
    chips = "".join(f"<span>{escape(str(flag))}</span>" for flag in flags)
    return f'<div class="trade-intent-attention"><b>复盘关注点</b><div>{chips}</div></div>'


def _snapshot_html(intent: dict[str, Any]) -> str:
    items = [
        ("当时 Setup 评分", intent.get("setup_score_snapshot")),
        ("技术结构", intent.get("technical_structure_score_snapshot")),
        ("量能承接", intent.get("volume_acceptance_score_snapshot")),
        ("风险收益", intent.get("risk_reward_score_snapshot")),
    ]
    body = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(_snapshot_value(value))}</strong></div>"
        for label, value in items
    )
    return f'<div class="trade-intent-snapshot">{body}</div>'


def _snapshot_value(value: object) -> str:
    if value is None or value == "":
        return "未记录"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"
