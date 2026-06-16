from __future__ import annotations

from collections.abc import Callable
from html import escape
from typing import Any

import streamlit as st

from data.trade_intent import (
    BUY_BEHAVIOR_OPTIONS,
    BUY_INTENT_QUESTIONS,
    SELL_INTENT_QUESTIONS,
    SELL_BEHAVIOR_OPTIONS,
    STOCK_STAGE_OPTIONS,
    buy_intent_attention_points,
    intent_title,
    sell_intent_discipline_tags,
    sell_intent_attention_points,
)
from data.portfolio_roles import (
    BUY_ROLE_FORM_OPTIONS,
    ROLE_UNDEFINED,
    SELL_ROLE_REMINDERS,
    normalize_portfolio_role,
    portfolio_role_core_tactical_split,
    portfolio_role_description,
    portfolio_role_label,
    portfolio_role_target_weight,
)


def render_trade_intent_dialog(
    *,
    side: str,
    ticker: str,
    action_label: str,
    key_prefix: str,
    on_confirm: Callable[[dict[str, str]], None],
    on_cancel: Callable[[], None],
    portfolio_role_context: dict[str, Any] | None = None,
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
                portfolio_role_context=portfolio_role_context,
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
                portfolio_role_context=portfolio_role_context,
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
    portfolio_role_context: dict[str, Any] | None = None,
) -> None:
    symbol_text = str(ticker or "").strip().upper() or "未选择"
    action_text = str(action_label or "").strip() or "记录交易"
    st.markdown(f"**交易对象：{symbol_text}｜{action_text}**")
    payload: dict[str, str] = {"intent_side": "buy"}
    _render_trade_label_section(payload, key_prefix=key_prefix, behavior_options=BUY_BEHAVIOR_OPTIONS, behavior_label="自我判断：本次买入行为类型")
    _render_buy_portfolio_role_selector(payload, key_prefix=key_prefix, context=portfolio_role_context)
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
        if portfolio_role_context is not None and payload.get("portfolio_role") == ROLE_UNDEFINED:
            st.warning("请先定义这只股票在组合里的角色。")
            return
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
    portfolio_role_context: dict[str, Any] | None = None,
) -> None:
    symbol_text = str(ticker or "").strip().upper() or "未选择"
    action_text = str(action_label or "").strip() or "记录交易"
    st.markdown(f"**交易对象：{symbol_text}｜{action_text}**")
    payload: dict[str, str] = {"intent_side": "sell"}
    _render_trade_label_section(payload, key_prefix=key_prefix, behavior_options=SELL_BEHAVIOR_OPTIONS, behavior_label="自我判断：本次卖出行为类型")
    _render_sell_portfolio_role_notice(payload, context=portfolio_role_context)
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
    discipline_tags = sell_intent_discipline_tags(payload)
    if discipline_tags:
        tags = "、".join(discipline_tags)
        st.info(f"本次交易标签：{tags}")
    attention_points = sell_intent_attention_points(payload)
    if attention_points:
        points = "、".join(attention_points)
        st.warning(f"本次记录存在复盘关注点：{points}")

    cols = st.columns(2)
    if cols[0].button("确认并记录", type="primary", width="stretch", key=f"{key_prefix}-confirm"):
        on_confirm(payload)
    if cols[1].button("返回修改", width="stretch", key=f"{key_prefix}-cancel"):
        on_cancel()


def _render_trade_label_section(
    payload: dict[str, str],
    *,
    key_prefix: str,
    behavior_options: list[str],
    behavior_label: str,
) -> None:
    st.markdown("##### 交易标签")
    payload["stock_stage_self_judgment"] = st.radio(
        "自我判断：股票当前阶段",
        STOCK_STAGE_OPTIONS,
        index=len(STOCK_STAGE_OPTIONS) - 1,
        horizontal=False,
        key=f"{key_prefix}-stock-stage-self-judgment",
    )
    payload["trade_behavior_self_judgment"] = st.radio(
        behavior_label,
        behavior_options,
        index=len(behavior_options) - 1,
        horizontal=False,
        key=f"{key_prefix}-trade-behavior-self-judgment",
    )
    st.caption("这些是复盘标签，不参与评分，也不会影响交易保存。")


def _render_buy_portfolio_role_selector(
    payload: dict[str, str],
    *,
    key_prefix: str,
    context: dict[str, Any] | None,
) -> None:
    if context is None:
        return
    st.markdown("##### 持仓角色")
    options = list(BUY_ROLE_FORM_OPTIONS)
    current_role = normalize_portfolio_role(context.get("selected_role") or context.get("current_role"), default=ROLE_UNDEFINED)
    current_label = next((label for label, role in BUY_ROLE_FORM_OPTIONS.items() if role == current_role), options[0])
    selected_label = st.selectbox(
        "这次买入后，这只股票在组合里的角色是什么？",
        options,
        index=options.index(current_label) if current_label in options else 0,
        key=f"{key_prefix}-portfolio-role",
    )
    role = BUY_ROLE_FORM_OPTIONS.get(str(selected_label), ROLE_UNDEFINED)
    payload["portfolio_role"] = role
    payload["trade_role"] = role
    payload["role_label"] = portfolio_role_label(role)
    payload["role_target_weight"] = portfolio_role_target_weight(role)
    payload["core_tactical_split"] = portfolio_role_core_tactical_split(role)
    if role == ROLE_UNDEFINED:
        st.warning("新增股票必须先定义角色；观察仓不计入正式 6 只上限。")
        return
    target = portfolio_role_target_weight(role) or "未设"
    split = portfolio_role_core_tactical_split(role) or "未设"
    description = portfolio_role_description(role)
    st.caption(f"{portfolio_role_label(role)}：目标仓位 {target}；核心 / 战术 {split}。{description}")
    warning = str((context.get("warnings_by_role") or {}).get(role) or "").strip()
    if warning:
        st.warning(warning)


def _render_sell_portfolio_role_notice(payload: dict[str, str], *, context: dict[str, Any] | None) -> None:
    if context is None:
        return
    role = normalize_portfolio_role(context.get("current_role"), default=ROLE_UNDEFINED) or ROLE_UNDEFINED
    payload["portfolio_role"] = role
    payload["trade_role"] = role
    payload["role_label"] = portfolio_role_label(role)
    payload["role_target_weight"] = portfolio_role_target_weight(role)
    payload["core_tactical_split"] = portfolio_role_core_tactical_split(role)
    target = portfolio_role_target_weight(role) or "未设"
    split = portfolio_role_core_tactical_split(role)
    detail = f"目标仓位 {target}"
    if split:
        detail += f"；核心 / 战术 {split}"
    reminder = SELL_ROLE_REMINDERS.get(role, SELL_ROLE_REMINDERS[ROLE_UNDEFINED])
    st.info(f"当前持仓角色：{portfolio_role_label(role)}。{detail}。{reminder}")


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
        label_html = _intent_label_html(intent)
        return (
            '<section class="trade-intent-record">'
            "<h4>交易意图记录</h4>"
            f'<p class="trade-intent-title">{escape(title)}</p>'
            f"{label_html}"
            f'<div class="trade-intent-grid buy">{body}</div>'
            f"{_discipline_html(intent)}"
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
        label_html = _intent_label_html(intent)
        return (
            '<section class="trade-intent-record">'
            "<h4>交易意图记录</h4>"
            f'<p class="trade-intent-title">{escape(title)}</p>'
            f"{label_html}"
            f'<div class="trade-intent-grid sell">{body}</div>'
            f"{_discipline_html(intent)}"
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
        f"{_intent_label_html(intent)}"
        f'<div class="trade-intent-grid">{body}</div>'
        f"{_discipline_html(intent)}"
        f"{_attention_html(intent)}"
        f"{_snapshot_html(intent)}"
        "</section>"
    )


def _intent_label_html(intent: dict[str, Any]) -> str:
    payload = intent.get("payload") if isinstance(intent.get("payload"), dict) else {}
    stock_stage = intent.get("stock_stage_self_judgment") or payload.get("stock_stage_self_judgment")
    behavior = intent.get("trade_behavior_self_judgment") or payload.get("trade_behavior_self_judgment")
    role_label = intent.get("role_label") or payload.get("role_label")
    target = intent.get("role_target_weight") or payload.get("role_target_weight")
    split = intent.get("core_tactical_split") or payload.get("core_tactical_split")
    role_detail = " / ".join(str(value) for value in (role_label, target, split) if str(value or "").strip())
    items = [
        ("股票当前阶段", stock_stage),
        ("本次交易行为", behavior),
        ("持仓角色", role_detail),
    ]
    body = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(str(value or '未记录'))}</strong></div>"
        for label, value in items
    )
    return f'<div class="trade-intent-label-grid">{body}</div>'


def _discipline_html(intent: dict[str, Any]) -> str:
    tags = intent.get("discipline_tags")
    if not isinstance(tags, list):
        tags = []
    if not tags:
        return ""
    chips = "".join(f"<span>{escape(str(tag))}</span>" for tag in tags)
    return f'<div class="trade-intent-discipline"><b>纪律标签</b><div>{chips}</div></div>'


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
