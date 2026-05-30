from __future__ import annotations

import math
from datetime import date, timedelta
from html import escape

import streamlit as st

from buy_zone_engine import generate_buy_zone
from data.cache_read_model import CacheReadModel
from data.decision_log import (
    DecisionErrorTagStore,
    DecisionLogStore,
    DecisionOutcomeStore,
    TradeJournalStore,
    build_decision_signal_stats,
    refresh_decision_outcomes,
)
from data.portfolio_trade_sync import (
    POSITION_AFFECTING_ACTIONS,
    apply_trade_to_portfolio,
    preview_trade_values_portfolio_effect,
)
from data.sell_fly_review import build_sell_fly_review_results
from data.stock_plan import StockPlanStore
from data.trading_discipline import evaluate_trading_discipline
from data.trading_discipline_stats import build_trading_discipline_summary
from formatting import format_currency, format_percent
from indicators.technicals import add_technical_indicators, latest_technical_snapshot
from scoring.total_score import calculate_total_score
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
SELL_DISCIPLINE_ACTIONS = {"sell", "trim"}
CLASSIFICATION_ACTIONS = {"buy", "add"}
POSITION_CLASS_OPTIONS = {
    "未分类": "",
    "A 类核心股": "A",
    "B 类赔率股": "B",
    "C 类交易股": "C",
}
POSITION_CLASS_COPY = {
    "": "未设置分类，后续卖出不会自动套 A 类核心仓纪律。",
    "A": "长期核心，禁止宏观恐慌清仓。",
    "B": "有逻辑但不做核心，允许波段。",
    "C": "短线 / 情绪 / 高波动，快进快出。",
}
POSITION_CLASS_DEFAULTS = {
    "A": (0.60, 0.40),
    "B": (0.00, 1.00),
    "C": (0.00, 1.00),
    "": (None, None),
}
SELL_REASON_OPTIONS = {
    "宏观风险": "macro",
    "技术破位 / 过热": "technical",
    "估值过高": "valuation",
    "仓位过重": "position_size",
    "投资假设破裂": "thesis_broken",
}
DECISION_MOOD_OPTIONS = {
    "请选择": "",
    "深思熟虑": "well_reasoned",
    "按计划执行": "plan_execution",
    "FOMO / 怕错过": "fomo",
    "焦虑 / 怕回撤": "anxiety",
    "宏观恐慌": "macro_fear",
    "报复性交易": "revenge_trade",
    "手痒交易": "boredom_trade",
    "恐慌卖出": "panic_sell",
    "卖飞后追回": "regret_chase",
    "不确定但想操作": "uncertainty",
}
DECISION_MOOD_LABELS = {value: label for label, value in DECISION_MOOD_OPTIONS.items() if value}
SELL_EMOTIONAL_MOODS = {"anxiety", "macro_fear", "panic_sell", "regret_chase"}
BUY_EMOTIONAL_MOODS = {"fomo", "regret_chase"}
FIX_REQUIRED_BLOCKERS = {"planned_actual_sell_pct_mismatch", "reentry_plan_required_before_trim_or_sell"}
DISCIPLINE_STATUS_LABELS = {
    "allowed": "允许执行",
    "warning": "需要复核",
    "blocked": "纪律不建议执行",
    "hold": "无需卖出",
}
DISCIPLINE_STATUS_COMPACT_LABELS = {
    "allowed": "通过",
    "warning": "警告",
    "blocked": "阻断",
    "hold": "无",
}
DISCIPLINE_BLOCKER_LABELS = {
    "now_style_error_risk": "NOW 式错误风险：A 类核心股在投资逻辑未破坏时，不应因宏观恐慌或情绪压力卖出核心仓。若你不愿右侧追回，就不能全卖低位买到的好公司。",
    "a_class_core_clear_requires_thesis_break": "A 类核心仓不能在投资逻辑未破裂时清仓。",
    "a_class_core_sale_blocked_while_gain_0_to_25_pct": "A 类持仓在 0-25% 浮盈区间不建议卖核心仓。",
    "sell_level_does_not_allow_core_sale": "当前卖出等级不允许动核心仓。",
    "macro_risk_cannot_trigger_single_name_exit": "宏观风险不能单独触发个股清仓。",
    "reentry_plan_required_before_trim_or_sell": "减仓 / 卖出前需要明确回补计划。",
    "planned_sell_pct_exceeds_sell_level_limit": "计划卖出比例超过当前纪律等级上限。",
}
DISCIPLINE_BLOCKER_LABELS.update(
    {
        "planned_actual_sell_pct_mismatch": "计划卖出比例与实际卖出数量不一致。",
        "a_class_macro_or_emotional_sell_exceeds_20_pct": "A 类核心股在宏观或情绪压力下，默认最多只能卖出 20%。",
        "a_class_core_floor_breached": "该操作会打穿 A 类核心仓底仓保护。",
    }
)
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
SELL_REASON_LABELS = {value: label for label, value in SELL_REASON_OPTIONS.items()}
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
    _render_weekly_discipline_summary()
    st.markdown('<div class="trade-workbench-section">交易记录</div>', unsafe_allow_html=True)
    toolbar_cols = st.columns([3.8, 1])
    toolbar_cols[0].markdown(
        '<div class="trade-journal-toolbar-note">执行优先，日志用于复盘，不做收益统计。</div>',
        unsafe_allow_html=True,
    )
    if toolbar_cols[1].button("新增记录", key="trade-journal-open", width="stretch"):
        _clear_trade_edit_query()
        st.session_state["trade_journal_editor_open"] = True
    _render_editor(store)

    symbols = store.list_symbols()
    entries = _load_entries(store, symbols)
    _render_summary(entries)
    _render_entry_delete_confirmation(store)
    _render_entry_detail(store)
    _render_entries(symbols, entries)
    _render_sell_fly_review()
    _render_signal_replay(decision_store, outcome_store, error_tag_store)


def _render_editor(store: TradeJournalStore) -> None:
    editing_id = _query_int("editTrade")
    editing_entry = store.get_entry(editing_id) if editing_id is not None else None
    if editing_id is not None and editing_entry is None:
        _clear_trade_edit_query()
        st.session_state["trade_journal_notice"] = ("error", "交易记录不存在或已删除。")
        st.rerun()
    editor_open = bool(st.session_state.get("trade_journal_editor_open", False)) or editing_entry is not None
    title = "编辑交易记录" if editing_entry else "新增交易记录"
    st.markdown('<div id="trade-journal-editor"></div>', unsafe_allow_html=True)
    with st.expander(title, expanded=editor_open):
        st.session_state["trade_journal_editor_open"] = False
        top_cols = st.columns([1.1, 1.2, 1])
        symbol = top_cols[0].text_input("股票代码", value=_entry_value(editing_entry, "symbol"), key=_editor_key("symbol", editing_id)).strip().upper()
        action_default = _action_label_for_entry(editing_entry)
        action_label = top_cols[1].selectbox("操作类型", list(ACTION_OPTIONS), index=list(ACTION_OPTIONS).index(action_default), key=_editor_key("action", editing_id))
        trade_date = top_cols[2].date_input("日期", value=_entry_date(editing_entry), key=_editor_key("date", editing_id))
        action_type = ACTION_OPTIONS[action_label]

        stock_plan = _load_stock_discipline_profile(symbol)

        trade_cols = st.columns([1, 1, 1.2, 1])
        quantity = trade_cols[0].text_input("数量", value=_entry_number_text(editing_entry, "quantity"), key=_editor_key("quantity", editing_id))
        price = trade_cols[1].text_input("价格", value=_entry_number_text(editing_entry, "price"), key=_editor_key("price", editing_id))
        mood_default = _decision_mood_label_for_entry(editing_entry)
        mood_label = trade_cols[2].selectbox("交易心理标签", list(DECISION_MOOD_OPTIONS), index=list(DECISION_MOOD_OPTIONS).index(mood_default), key=_editor_key("decision-mood", editing_id))
        decision_mood = DECISION_MOOD_OPTIONS.get(mood_label, "")
        decision_snapshot_id = trade_cols[3].text_input(
            "关联信号 ID（可选）",
            value=_entry_int_text(editing_entry, "decision_snapshot_id"),
            key=_editor_key("snapshot-id", editing_id),
        )

        option_cols = st.columns(3)
        premium = option_cols[0].text_input("权利金", value=_entry_number_text(editing_entry, "premium"), key=_editor_key("premium", editing_id))
        strike_price = option_cols[1].text_input("行权价", value=_entry_number_text(editing_entry, "strike_price"), key=_editor_key("strike", editing_id))
        expiry_date = option_cols[2].text_input("到期日", value=_entry_value(editing_entry, "expiry_date"), placeholder="YYYY-MM-DD", key=_editor_key("expiry", editing_id))

        portfolio_preview = _portfolio_sync_preview(symbol, action_type, quantity, price)
        discipline_result = None

        if action_type in CLASSIFICATION_ACTIONS:
            _render_buy_classification_editor(symbol, editing_entry=editing_entry, stock_plan=stock_plan, key_suffix=str(editing_id or "new"))

        if action_type in SELL_DISCIPLINE_ACTIONS:
            discipline_result = _render_trading_discipline_check(
                symbol,
                action_type,
                decision_mood=decision_mood,
                trade_quantity=quantity,
                trade_price=price,
                current_quantity=portfolio_preview.get("currentQuantity"),
                editing_entry=editing_entry,
                stock_plan=stock_plan,
                key_suffix=str(editing_id or "new"),
            )

        sync_to_portfolio = _render_portfolio_sync_option(
            symbol,
            action_type,
            quantity,
            price,
            default_checked=editing_entry is None,
            key_suffix=str(editing_id or "new"),
            preview=portfolio_preview,
            discipline_blocked=_discipline_result_blocked(discipline_result),
        )

        notes = st.text_area("备注", value=_entry_value(editing_entry, "notes"), height=86, key=_editor_key("notes", editing_id))
        button_label = "保存修改" if editing_entry else "保存记录"
        if st.button(button_label, key=_editor_key("save", editing_id), width="stretch"):
            entry_values = {
                "trade_date": trade_date.isoformat(),
                "action_type": action_type,
                "quantity": quantity,
                "price": price,
                "premium": premium,
                "strike_price": strike_price,
                "expiry_date": expiry_date,
                "decision_mood": decision_mood,
                "decision_snapshot_id": decision_snapshot_id,
                "notes": notes,
                "syncToPortfolio": sync_to_portfolio,
            }
            entry_values.update(_trade_discipline_form_values(action_type, key_suffix=str(editing_id or "new")))
            entry_values.update(_buy_classification_form_values(action_type, key_suffix=str(editing_id or "new")))
            if editing_entry:
                _update_entry(store, int(editing_id or 0), symbol, entry_values)
            else:
                _save_entry(store, symbol, entry_values)
        if editing_entry and st.button("取消编辑", key=_editor_key("cancel", editing_id), width="stretch"):
            _clear_trade_edit_query()
            st.rerun()


def _render_buy_classification_editor(
    symbol: str,
    *,
    editing_entry: dict | None = None,
    stock_plan: dict | None = None,
    key_suffix: str = "new",
):
    st.markdown('<div class="trade-discipline-title">股票纪律分类</div>', unsafe_allow_html=True)
    default_class = _default_position_class(editing_entry, stock_plan)
    default_label = _position_class_label(default_class)
    class_key = f"trade-class-position-{key_suffix}"
    cols = st.columns([1.2, 0.92, 0.92, 1.6], gap="small")
    class_label = cols[0].selectbox(
        "股票纪律分类",
        list(POSITION_CLASS_OPTIONS),
        index=list(POSITION_CLASS_OPTIONS).index(default_label),
        key=class_key,
    )
    position_class = POSITION_CLASS_OPTIONS.get(class_label, "")
    core_default, trading_default = _classification_ratio_defaults(position_class, editing_entry, stock_plan)
    class_key_part = position_class or "none"
    cols[1].text_input(
        "核心仓最低 %",
        value=_ratio_percent_text(core_default),
        disabled=not position_class,
        key=f"trade-class-core-min-{key_suffix}-{class_key_part}",
    )
    cols[2].text_input(
        "交易仓上限 %",
        value=_ratio_percent_text(trading_default),
        disabled=not position_class,
        key=f"trade-class-trading-max-{key_suffix}-{class_key_part}",
    )
    note_default = _entry_value(editing_entry, "classification_note") or str((stock_plan or {}).get("classification_note") or "")
    cols[3].text_input(
        "分类备注",
        value=note_default,
        placeholder="例如：核心云平台，除非投资逻辑破裂不清仓",
        key=f"trade-class-note-{key_suffix}",
    )
    summary = _classification_summary(position_class)
    source = "默认来自股票纪律档案，可在本次买入里更新。" if _profile_position_class(stock_plan) else "未找到股票纪律档案，可从本次买入开始建立。"
    st.markdown(
        f"""
        <div class="trade-classification-summary">
          <strong>{escape(summary)}</strong>
          <span>{escape(POSITION_CLASS_COPY.get(position_class, POSITION_CLASS_COPY[""]))}</span>
          <em>{escape(source)}</em>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_portfolio_sync_option(
    symbol: str,
    action_type: str,
    quantity: object,
    price: object,
    *,
    default_checked: bool,
    key_suffix: str,
    preview: dict | None = None,
    discipline_blocked: bool = False,
) -> bool:
    if action_type not in POSITION_AFFECTING_ACTIONS:
        return False
    sync_key = f"trade-portfolio-sync-{key_suffix}"
    if discipline_blocked:
        st.session_state[sync_key] = False
    checked = st.checkbox(
        "保存后同步到组合持仓",
        value=False if discipline_blocked else default_checked,
        key=sync_key,
        disabled=discipline_blocked,
    )
    current_preview = preview or _portfolio_sync_preview(symbol, action_type, quantity, price)
    _render_portfolio_sync_preview(current_preview, checked=checked, discipline_blocked=discipline_blocked)
    return False if discipline_blocked else checked


def _portfolio_sync_preview(symbol: str, action_type: str, quantity: object, price: object) -> dict:
    if action_type not in POSITION_AFFECTING_ACTIONS:
        return {}
    return preview_trade_values_portfolio_effect(
        symbol,
        {"action_type": action_type, "quantity": quantity, "price": price},
    )


def _discipline_result_blocked(result: object) -> bool:
    return str(getattr(result, "disciplineStatus", "") or "") == "blocked"


def _render_portfolio_sync_preview(preview: dict, *, checked: bool, discipline_blocked: bool = False) -> None:
    tone = "warning" if preview.get("status") == "failed" or discipline_blocked else "ok"
    title = "组合持仓同步预览" if checked else "仅保存交易日志，不同步持仓"
    if discipline_blocked:
        title = "纪律门禁阻止同步"
    rows = [
        ("当前持股", _quantity_text(preview.get("currentQuantity"))),
        ("当前均价", _money_text(preview.get("currentAverageCost"))),
        ("本次股数", _quantity_text(preview.get("tradeQuantity"))),
        ("成交价格", _money_text(preview.get("tradePrice"))),
        ("同步后持股", _quantity_text(preview.get("afterQuantity"))),
        ("同步后均价", _money_text(preview.get("afterAverageCost"))),
    ]
    if preview.get("afterMarketValue") is not None:
        rows.append(("同步后市值", _money_text(preview.get("afterMarketValue"))))
    if preview.get("afterPositionPct") is not None:
        rows.append(("同步后仓位", _percent_or_dash(preview.get("afterPositionPct"))))
    content = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in rows
    )
    error = str(preview.get("error") or "").strip()
    hint = error if error else "勾选后，保存成功会同步一次；已同步交易不会重复作用到持仓。"
    if discipline_blocked:
        hint = "当前交易未通过纪律门禁，只能保存为违规记录，不能同步到组合持仓。"
    st.markdown(
        f"""
        <section class="trade-portfolio-sync-card {escape(tone)}">
          <div><b>{escape(title)}</b><span>{escape(hint)}</span></div>
          <div class="trade-portfolio-sync-grid">{content}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_trading_discipline_check(
    symbol: str,
    action_type: str,
    *,
    decision_mood: str | None = None,
    trade_quantity: object = None,
    trade_price: object = None,
    current_quantity: object = None,
    editing_entry: dict | None = None,
    stock_plan: dict | None = None,
    key_suffix: str = "new",
) -> None:
    st.markdown('<div class="trade-discipline-title">交易纪律检查</div>', unsafe_allow_html=True)
    cols = st.columns([0.72, 0.92, 1.2, 0.86, 0.86, 0.92], gap="small")
    position_default = _default_position_class(editing_entry, stock_plan)
    position_default_label = _position_class_label(position_default)
    reason_default = _sell_reason_label_for_entry(editing_entry)
    position_label = cols[0].selectbox(
        "股票分类",
        list(POSITION_CLASS_OPTIONS),
        index=list(POSITION_CLASS_OPTIONS).index(position_default_label),
        key=f"trade-discipline-position-class-{key_suffix}",
    )
    position_class = POSITION_CLASS_OPTIONS.get(position_label, "")
    actual_sell_pct = _actual_sell_pct(trade_quantity, current_quantity)
    st.session_state[f"trade-discipline-actual-sell-pct-{key_suffix}"] = actual_sell_pct
    st.session_state[f"trade-discipline-current-quantity-{key_suffix}"] = _number(current_quantity)
    planned_sell_pct = cols[1].text_input("计划卖出比例（%）", value=_entry_percent_text(editing_entry, "planned_sell_pct", "10"), key=f"trade-discipline-planned-sell-pct-{key_suffix}")
    reason_label = cols[2].selectbox("卖出原因", list(SELL_REASON_OPTIONS), index=list(SELL_REASON_OPTIONS).index(reason_default), key=f"trade-discipline-sell-reason-{key_suffix}")
    thesis_broken = cols[3].checkbox("投资逻辑破裂", value=_entry_bool(editing_entry, "thesis_broken"), key=f"trade-discipline-thesis-broken-{key_suffix}")
    position_over_limit = cols[4].checkbox("仓位超限", value=_entry_bool(editing_entry, "position_over_limit"), key=f"trade-discipline-position-over-limit-{key_suffix}")
    cols[5].markdown('<div class="trade-reentry-state">回补计划由下方字段判断</div>', unsafe_allow_html=True)
    core_pct, trading_pct = _classification_ratio_defaults(position_class, editing_entry, stock_plan)
    st.session_state[f"trade-discipline-core-min-{key_suffix}"] = core_pct
    st.session_state[f"trade-discipline-trading-max-{key_suffix}"] = trading_pct
    discipline_context = _discipline_gate_context(
        position_class=position_class,
        current_quantity=current_quantity,
        trade_quantity=trade_quantity,
        planned_sell_pct=planned_sell_pct,
        actual_sell_pct=actual_sell_pct,
        core_pct=core_pct,
    )
    if position_class:
        st.caption("股票分类默认来自股票纪律档案；本次卖出/减仓仍可临时覆盖。")
    else:
        st.caption("未设置分类：本次不会按 A 类核心仓纪律检查，建议先补股票纪律档案。")

    gate_result = evaluate_trading_discipline(
        symbol=symbol,
        positionClass=position_class or "C",
        corePositionPct=core_pct,
        tradingPositionPct=trading_pct,
        unrealizedGainPct=None,
        plannedAction=action_type,
        plannedSellPct=_parse_optional_float(planned_sell_pct),
        sellReasonType=SELL_REASON_OPTIONS[reason_label],
        thesisBroken=thesis_broken,
        positionOverLimit=position_over_limit,
        hasReentryPlan=True,
        actualSellPct=actual_sell_pct,
        decisionMood=decision_mood or _entry_value(editing_entry, "decision_mood"),
    )
    gate_conclusion = _discipline_gate_conclusion(gate_result)
    hard_blocked = gate_conclusion in {"BLOCK", "FIX_REQUIRED"}
    if hard_blocked:
        st.session_state[f"trade-discipline-hard-block-{key_suffix}"] = True
        st.session_state[f"trade-discipline-has-reentry-plan-{key_suffix}"] = False
        _render_discipline_gate_explanation(gate_result, discipline_context)
        st.error("当前交易未通过纪律门禁，不能用回补计划合理化违规卖出。")
        return gate_result
    st.session_state[f"trade-discipline-hard-block-{key_suffix}"] = False

    reentry_values = _render_reentry_plan_editor(
        symbol,
        trade_price=trade_price,
        sell_reason_type=SELL_REASON_OPTIONS[reason_label],
        decision_mood=decision_mood,
        editing_entry=editing_entry,
        key_suffix=key_suffix,
    )
    has_reentry_plan = _has_reentry_plan_values(reentry_values)
    st.session_state[f"trade-discipline-has-reentry-plan-{key_suffix}"] = has_reentry_plan

    result = evaluate_trading_discipline(
        symbol=symbol,
        positionClass=position_class or "C",
        corePositionPct=core_pct,
        tradingPositionPct=trading_pct,
        unrealizedGainPct=None,
        plannedAction=action_type,
        plannedSellPct=_parse_optional_float(planned_sell_pct),
        sellReasonType=SELL_REASON_OPTIONS[reason_label],
        thesisBroken=thesis_broken,
        positionOverLimit=position_over_limit,
        hasReentryPlan=has_reentry_plan,
        actualSellPct=actual_sell_pct,
        decisionMood=decision_mood or _entry_value(editing_entry, "decision_mood"),
    )
    _render_discipline_gate_explanation(result, discipline_context)
    return result


def _discipline_gate_context(
    *,
    position_class: str,
    current_quantity: object,
    trade_quantity: object,
    planned_sell_pct: object,
    actual_sell_pct: object,
    core_pct: object,
) -> dict:
    current_qty = _number(current_quantity) or 0.0
    sell_qty = _number(trade_quantity) or 0.0
    planned_pct = _ratio_value(planned_sell_pct) or 0.0
    actual_pct = _number(actual_sell_pct)
    if actual_pct is None and current_qty > 0:
        actual_pct = sell_qty / current_qty
    actual_pct = actual_pct or 0.0
    core_ratio = _number(core_pct)
    if core_ratio is None:
        core_ratio = POSITION_CLASS_DEFAULTS.get(str(position_class or "").upper(), (0.0, 1.0))[0] or 0.0
    core_min_qty = math.ceil(current_qty * core_ratio) if current_qty > 0 else 0
    tradable_qty = max(0.0, current_qty - core_min_qty)
    after_sell_qty = max(0.0, current_qty - sell_qty)
    remaining_tradable_qty = max(0.0, after_sell_qty - core_min_qty)
    breach_qty = max(0.0, core_min_qty - after_sell_qty)
    return {
        "positionClass": str(position_class or "").upper() or "未分类",
        "currentQty": current_qty,
        "sellQty": sell_qty,
        "plannedSellPct": planned_pct,
        "actualSellPct": actual_pct,
        "plannedActualDiffPct": abs(actual_pct - planned_pct),
        "coreRatioMin": core_ratio,
        "coreMinQty": float(core_min_qty),
        "tradableQty": tradable_qty,
        "afterSellQty": after_sell_qty,
        "remainingTradableQty": remaining_tradable_qty,
        "breachesCore": breach_qty > 1e-9,
        "breachQty": breach_qty,
    }


def _discipline_gate_conclusion(result) -> str:
    blockers = {str(item) for item in (getattr(result, "blockers", []) or [])}
    hard_blockers = blockers - FIX_REQUIRED_BLOCKERS
    if hard_blockers:
        return "BLOCK"
    if blockers:
        return "FIX_REQUIRED"
    if str(getattr(result, "disciplineStatus", "") or "") == "warning" or getattr(result, "warnings", []):
        return "WARN"
    return "PASS"


def _render_discipline_gate_explanation(result, context: dict) -> None:
    conclusion = _discipline_gate_conclusion(result)
    tone = {
        "PASS": "pass",
        "WARN": "warn",
        "FIX_REQUIRED": "fix",
        "BLOCK": "block",
    }.get(conclusion, "warn")
    max_allowed_pct = float(getattr(result, "maxAllowedSellPct", 0) or 0)
    max_allowed_qty = math.floor(context["currentQty"] * max_allowed_pct) if context["currentQty"] > 0 else 0
    reasons = _discipline_gate_reasons(result, context, max_allowed_qty)
    actions = _discipline_gate_actions(result, context, max_allowed_qty)
    metric_rows = [
        ("当前持股", _quantity_text(context["currentQty"])),
        ("本次卖出", _quantity_text(context["sellQty"])),
        ("计划卖出", _pct_point_text(context["plannedSellPct"])),
        ("实际卖出", _pct_point_text(context["actualSellPct"])),
        ("差异", _pct_point_text(context["plannedActualDiffPct"], suffix="pct")),
        ("卖出等级", str(getattr(result, "sellLevel", "") or "N/A")),
        ("等级上限", _pct_point_text(max_allowed_pct)),
        ("最多可卖", _quantity_text(max_allowed_qty)),
    ]
    split_rows = [
        ("股票分类", _position_class_label(context["positionClass"]) if context["positionClass"] in {"A", "B", "C"} else "未分类"),
        ("核心仓最低", _pct_point_text(context["coreRatioMin"])),
        ("核心仓底线", _quantity_text(context["coreMinQty"])),
        ("可交易仓", _quantity_text(context["tradableQty"])),
        ("卖出后持股", _quantity_text(context["afterSellQty"])),
        ("剩余交易仓", _quantity_text(context["remainingTradableQty"])),
        ("是否打穿", "是" if context["breachesCore"] else "否"),
        ("打穿数量", _quantity_text(context["breachQty"])),
    ]
    reason_html = "".join(f"<li>{escape(item)}</li>" for item in reasons)
    action_html = "".join(f"<li>{escape(item)}</li>" for item in actions)
    metric_html = _discipline_gate_metric_html(metric_rows)
    split_html = _discipline_gate_metric_html(split_rows)
    st.markdown(
        f"""
        <section class="trade-gate-card {escape(tone)}">
          <div class="trade-gate-head">
            <strong>门禁结论：{escape(conclusion)}</strong>
            <span>{escape(_discipline_gate_summary(conclusion))}</span>
          </div>
          <div class="trade-gate-body">
            <div><b>阻止原因</b><ul>{reason_html}</ul></div>
            <div><b>可修正动作</b><ul>{action_html}</ul></div>
          </div>
          <div class="trade-gate-subtitle">卖出比例核对</div>
          <div class="trade-gate-grid">{metric_html}</div>
          <div class="trade-gate-subtitle">核心仓 / 交易仓拆分</div>
          <div class="trade-gate-grid">{split_html}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _discipline_gate_metric_html(rows: list[tuple[str, str]]) -> str:
    return "".join(
        "<div>"
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        "</div>"
        for label, value in rows
    )


def _discipline_gate_summary(conclusion: str) -> str:
    return {
        "PASS": "可以保存并同步，但仍按计划执行。",
        "WARN": "可以同步前继续复核，避免情绪驱动。",
        "FIX_REQUIRED": "先修正比例、数量或回补计划；暂不允许同步。",
        "BLOCK": "硬性拦截；只能保存为违规记录，不能同步持仓。",
    }.get(conclusion, "需要复核。")


def _discipline_gate_reasons(result, context: dict, max_allowed_qty: int) -> list[str]:
    reasons: list[str] = []
    if context["plannedActualDiffPct"] > 0.02:
        reasons.append(
            f"你计划卖 {_pct_point_text(context['plannedSellPct'])}，但实际卖出 {_pct_point_text(context['actualSellPct'])}。"
        )
    if context["breachesCore"]:
        reasons.append(
            f"卖出后只剩 {_quantity_text(context['afterSellQty'])} 股，低于核心仓底线 {_quantity_text(context['coreMinQty'])} 股。"
        )
    else:
        reasons.append("本次卖出仍在交易仓内，未打穿核心仓。")
    sell_level = str(getattr(result, "sellLevel", "") or "N/A")
    if max_allowed_qty > 0:
        reasons.append(f"若卖出超过 {max_allowed_qty} 股，将超过 {sell_level} 卖出上限。")
    for blocker in getattr(result, "blockers", []) or []:
        text = _discipline_message_text(blocker)
        if text not in reasons:
            reasons.append(text)
    for warning in (getattr(result, "warnings", []) or [])[:2]:
        text = _discipline_message_text(warning)
        if text not in reasons:
            reasons.append(text)
    return reasons or ["当前交易纪律检查通过。"]


def _discipline_gate_actions(result, context: dict, max_allowed_qty: int) -> list[str]:
    actions: list[str] = []
    if context["plannedActualDiffPct"] > 0.02:
        planned_qty = math.floor(context["currentQty"] * context["plannedSellPct"]) if context["currentQty"] > 0 else 0
        actions.append(
            f"把计划卖出比例改为 {_pct_point_text(context['actualSellPct'])}，或把卖出数量改到约 {planned_qty} 股。"
        )
    if max_allowed_qty > 0 and context["sellQty"] > max_allowed_qty:
        actions.append(f"把本次卖出数量降到不超过 {max_allowed_qty} 股。")
    if context["breachesCore"]:
        actions.append(f"至少保留 {_quantity_text(context['coreMinQty'])} 股 A 类核心仓。")
    if "reentry_plan_required_before_trim_or_sell" in {str(item) for item in (getattr(result, "blockers", []) or [])}:
        actions.append("补全回踩买回价、不跌反涨买回价和时间止损。")
    if not actions:
        actions.append("按计划保存；同步前再次确认不是宏观恐慌或焦虑驱动。")
    return actions


def _ratio_value(value: object) -> float | None:
    number = _parse_optional_float(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 1 else number


def _pct_point_text(value: object, *, suffix: str = "%") -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    if suffix == "pct":
        return f"{number * 100:.1f}pct"
    return f"{number * 100:.1f}%"


def _render_reentry_plan_editor(
    symbol: str,
    *,
    trade_price: object = None,
    sell_reason_type: str = "",
    decision_mood: str | None = None,
    editing_entry: dict | None = None,
    key_suffix: str = "new",
) -> dict:
    emotional_sell = str(sell_reason_type or "") in {"macro", "macro_fear", "anxiety", "panic_sell"} or str(
        decision_mood or ""
    ) in SELL_EMOTIONAL_MOODS
    tone = " alert" if emotional_sell else ""
    st.markdown(
        (
            f'<section class="trade-reentry-shell{tone}">'
            '<div class="trade-reentry-head"><strong>回补计划</strong>'
            '<span>卖出前先写清楚怎么追回，避免卖飞后临场拍脑袋。</span></div>'
            "</section>"
        ),
        unsafe_allow_html=True,
    )
    keys = _reentry_keys(key_suffix)
    if st.button("使用系统建议生成回补计划", key=keys["generate"], width="stretch"):
        suggestion = _build_reentry_plan_suggestion(symbol, trade_price)
        for field, value in suggestion.items():
            if field in keys:
                st.session_state[keys[field]] = value

    price_cols = st.columns([1, 1, 0.72, 0.88, 0.88], gap="small")
    pullback_price = price_cols[0].text_input(
        "回踩买回价",
        value=_entry_number_text(editing_entry, "reentry_pullback_price"),
        key=keys["pullback"],
    )
    breakout_price = price_cols[1].text_input(
        "不跌反涨买回价",
        value=_entry_number_text(editing_entry, "reentry_breakout_price"),
        key=keys["breakout"],
    )
    time_stop_days = price_cols[2].text_input(
        "时间止损天数",
        value=_entry_int_text(editing_entry, "reentry_time_stop_days") or "5",
        key=keys["time_stop"],
    )
    pullback_pct = price_cols[3].text_input(
        "回踩买回比例 %",
        value=_entry_percent_text(editing_entry, "reentry_buy_back_pct_on_pullback", "50"),
        key=keys["pullback_pct"],
    )
    breakout_pct = price_cols[4].text_input(
        "反涨买回比例 %",
        value=_entry_percent_text(editing_entry, "reentry_buy_back_pct_on_breakout", "30"),
        key=keys["breakout_pct"],
    )
    thesis_invalidation = st.text_input(
        "不回补条件 / 投资逻辑破坏条件",
        value=_entry_value(editing_entry, "reentry_thesis_invalidation"),
        key=keys["invalidation"],
    )
    plan_text = st.text_area(
        "完整回补计划摘要",
        value=_entry_value(editing_entry, "reentry_plan_text"),
        height=64,
        key=keys["plan_text"],
    )
    values = {
        "reentryPullbackPrice": pullback_price,
        "reentryBreakoutPrice": breakout_price,
        "reentryTimeStopDays": time_stop_days,
        "reentryBuyBackPctOnPullback": pullback_pct,
        "reentryBuyBackPctOnBreakout": breakout_pct,
        "reentryThesisInvalidation": thesis_invalidation,
        "reentryPlanText": plan_text,
    }
    if emotional_sell and not _has_reentry_plan_values(values):
        st.warning("宏观或情绪驱动卖出前，必须先写清楚回补计划；否则纪律检查会继续提醒。")
    return values


def _reentry_keys(key_suffix: str) -> dict[str, str]:
    prefix = f"trade-reentry-{key_suffix}"
    return {
        "generate": f"{prefix}-generate",
        "pullback": f"{prefix}-pullback",
        "breakout": f"{prefix}-breakout",
        "time_stop": f"{prefix}-time-stop",
        "pullback_pct": f"{prefix}-pullback-pct",
        "breakout_pct": f"{prefix}-breakout-pct",
        "invalidation": f"{prefix}-invalidation",
        "plan_text": f"{prefix}-plan-text",
    }


def _reentry_plan_form_values(key_suffix: str) -> dict:
    keys = _reentry_keys(key_suffix)
    return {
        "reentryPullbackPrice": st.session_state.get(keys["pullback"]),
        "reentryBreakoutPrice": st.session_state.get(keys["breakout"]),
        "reentryTimeStopDays": st.session_state.get(keys["time_stop"]),
        "reentryBuyBackPctOnPullback": st.session_state.get(keys["pullback_pct"]),
        "reentryBuyBackPctOnBreakout": st.session_state.get(keys["breakout_pct"]),
        "reentryThesisInvalidation": st.session_state.get(keys["invalidation"]) or "",
        "reentryPlanText": st.session_state.get(keys["plan_text"]) or "",
    }


def _has_reentry_plan_values(values: dict) -> bool:
    return bool(
        str(values.get("reentryPlanText") or "").strip()
        or _parse_optional_float(values.get("reentryPullbackPrice")) is not None
        or _parse_optional_float(values.get("reentryBreakoutPrice")) is not None
    )


def _build_reentry_plan_suggestion(symbol: str, trade_price: object = None) -> dict[str, str]:
    sell_price = _parse_optional_float(trade_price)
    pullback = None
    breakout = sell_price
    try:
        cache = CacheReadModel()
        snapshot = cache.get_quote_payload(symbol) or {}
        history = cache.get_price_history(symbol)
        technicals = latest_technical_snapshot(add_technical_indicators(history)) if not history.empty else {}
        current_price = _first_number(sell_price, technicals.get("price"), snapshot.get("current_price"), cache.get_current_price(symbol))
        if current_price is not None:
            stock_data = {**snapshot, **technicals, "price_history": history, "price": current_price}
            score = calculate_total_score(snapshot, technicals)
            zone = generate_buy_zone(str(symbol).upper(), stock_data, score, score.scoring_model)
            combined = getattr(zone, "combinedEntry", None) or {}
            technical = getattr(zone, "technicalEntry", None) or {}
            pullback = _first_number(
                combined.get("technicalPullbackPrice"),
                combined.get("valuationEntryPrice"),
                _first_support_level_price(technical.get("supportLevels")),
                getattr(zone, "nextTriggerPrice", None),
                getattr(zone, "trancheBuyHigh", None),
            )
            breakout = _first_number(
                sell_price,
                getattr(zone, "noChaseAbove", None),
                technical.get("technicalReviewPrice"),
                current_price,
            )
    except Exception:
        pass
    if pullback is None and sell_price is not None:
        pullback = sell_price * 0.95
    if breakout is None and sell_price is not None:
        breakout = sell_price
    time_stop = "5"
    pullback_pct = "50"
    breakout_pct = "30"
    pullback_text = _money_text(pullback)
    breakout_text = _money_text(breakout)
    plan_text = (
        f"回踩到 {pullback_text} 买回 {pullback_pct}%；"
        f"若不跌反涨站回 {breakout_text}，买回 {breakout_pct}%；"
        f"若 {time_stop} 个交易日未跌破预期位置，买回 {breakout_pct}%；"
        "若投资逻辑破坏则不回补。"
    )
    return {
        "pullback": "" if pullback is None else f"{pullback:.2f}",
        "breakout": "" if breakout is None else f"{breakout:.2f}",
        "time_stop": time_stop,
        "pullback_pct": pullback_pct,
        "breakout_pct": breakout_pct,
        "invalidation": "核心投资假设被证伪，或长期逻辑不再成立",
        "plan_text": plan_text,
    }


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _first_support_level_price(levels: object) -> float | None:
    if not isinstance(levels, list):
        return None
    for level in levels:
        if isinstance(level, dict):
            number = _first_number(level.get("price"), level.get("level"), level.get("value"))
            if number is not None:
                return number
        else:
            number = _number(level)
            if number is not None:
                return number
    return None


def _trade_discipline_form_values(action_type: str, key_suffix: str = "new") -> dict:
    if action_type not in SELL_DISCIPLINE_ACTIONS:
        return {}
    reason_label = st.session_state.get(f"trade-discipline-sell-reason-{key_suffix}")
    position_class = _position_class_from_state(f"trade-discipline-position-class-{key_suffix}")
    reentry_values = _reentry_plan_form_values(key_suffix)
    hard_blocked = bool(st.session_state.get(f"trade-discipline-hard-block-{key_suffix}"))
    if hard_blocked:
        reentry_values = {
            "reentryPullbackPrice": "",
            "reentryBreakoutPrice": "",
            "reentryTimeStopDays": "",
            "reentryBuyBackPctOnPullback": "",
            "reentryBuyBackPctOnBreakout": "",
            "reentryThesisInvalidation": "",
            "reentryPlanText": "",
        }
    return {
        "positionClass": position_class,
        "corePositionMinPct": st.session_state.get(f"trade-discipline-core-min-{key_suffix}"),
        "tradingPositionMaxPct": st.session_state.get(f"trade-discipline-trading-max-{key_suffix}"),
        "plannedSellPct": _parse_optional_float(st.session_state.get(f"trade-discipline-planned-sell-pct-{key_suffix}")),
        "actualSellPct": st.session_state.get(f"trade-discipline-actual-sell-pct-{key_suffix}"),
        "currentPositionQuantity": st.session_state.get(f"trade-discipline-current-quantity-{key_suffix}"),
        "sellReasonType": SELL_REASON_OPTIONS.get(str(reason_label or ""), str(reason_label or "")),
        "thesisBroken": bool(st.session_state.get(f"trade-discipline-thesis-broken-{key_suffix}")),
        "positionOverLimit": bool(st.session_state.get(f"trade-discipline-position-over-limit-{key_suffix}")),
        "hasReentryPlan": False if hard_blocked else _has_reentry_plan_values(reentry_values),
        **reentry_values,
    }


def _buy_classification_form_values(action_type: str, key_suffix: str = "new") -> dict:
    if action_type not in CLASSIFICATION_ACTIONS:
        return {}
    position_class = _position_class_from_state(f"trade-class-position-{key_suffix}")
    key_part = position_class or "none"
    return {
        "positionClass": position_class,
        "corePositionMinPct": _parse_optional_float(st.session_state.get(f"trade-class-core-min-{key_suffix}-{key_part}")),
        "tradingPositionMaxPct": _parse_optional_float(st.session_state.get(f"trade-class-trading-max-{key_suffix}-{key_part}")),
        "classificationNote": st.session_state.get(f"trade-class-note-{key_suffix}") or "",
    }


def _render_trading_discipline_result(result) -> None:
    status = str(result.disciplineStatus or "")
    tone = _discipline_status_tone(status)
    metrics = [
        ("纪律状态", DISCIPLINE_STATUS_LABELS.get(status, status or "N/A")),
        ("卖出等级", str(result.sellLevel or "N/A")),
        ("上限比例", format_percent(float(result.maxAllowedSellPct or 0), already_percent=False)),
        ("允许卖核心仓", _yes_no(result.canSellCore)),
        ("需要回补计划", _yes_no(result.requiresReentryPlan)),
    ]
    metric_html = "".join(
        f'<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
        for label, value in metrics
    )
    blocker_html = _discipline_messages_html("blockers", result.blockers, is_blocker=True)
    warning_html = _discipline_messages_html("warnings", result.warnings, is_blocker=False)
    st.markdown(
        f"""
        <section class="trade-discipline-card {escape(tone)}">
          <div class="trade-discipline-head">
            <strong>{escape(DISCIPLINE_STATUS_LABELS.get(status, status or "N/A"))}</strong>
            <span>{escape(str(result.reminderText or ""))}</span>
          </div>
          <div class="trade-discipline-grid">{metric_html}</div>
          {blocker_html}
          {warning_html}
        </section>
        """,
        unsafe_allow_html=True,
    )
    if result.blockers:
        st.error("纪律不建议执行该卖出。可以保存记录，但请先复核核心仓、卖出比例和回补计划。")


def _discipline_messages_html(title: str, items: list[object], *, is_blocker: bool) -> str:
    if not items:
        return ""
    class_name = "blockers" if is_blocker else "warnings"
    label = "阻断提醒" if is_blocker else "复核提醒"
    rows = "".join(f"<li>{escape(_discipline_message_text(item))}</li>" for item in items)
    return f'<div class="trade-discipline-messages {class_name}"><b>{escape(label)}</b><ul>{rows}</ul></div>'


def _discipline_message_text(item: object) -> str:
    text = str(item or "").strip()
    return DISCIPLINE_BLOCKER_LABELS.get(text, text or "N/A")


def _discipline_status_tone(status: str) -> str:
    return {
        "allowed": "ok",
        "warning": "warning",
        "blocked": "blocked",
        "hold": "neutral",
    }.get(status, "neutral")


def _yes_no(value: object) -> str:
    return "是" if bool(value) else "否"


def _parse_optional_float(value: object) -> float | None:
    text = str(value or "").strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _actual_sell_pct(quantity: object, current_quantity: object) -> float | None:
    sell_quantity = _number(quantity)
    current = _number(current_quantity)
    if sell_quantity is None or current is None or current <= 0:
        return None
    return sell_quantity / current


def _load_stock_discipline_profile(symbol: str) -> dict:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return {}
    try:
        return StockPlanStore().get_plan(ticker)
    except Exception:
        return {}


def _profile_position_class(stock_plan: dict | None) -> str:
    value = str((stock_plan or {}).get("position_class") or "").strip().upper()
    return value if value in {"A", "B", "C"} else ""


def _default_position_class(editing_entry: dict | None, stock_plan: dict | None) -> str:
    entry_class = str((editing_entry or {}).get("position_class") or "").strip().upper()
    if entry_class in {"A", "B", "C"}:
        return entry_class
    return _profile_position_class(stock_plan)


def _position_class_label(position_class: str) -> str:
    value = str(position_class or "").strip().upper()
    for label, option_value in POSITION_CLASS_OPTIONS.items():
        if option_value == value:
            return label
    return "未分类"


def _position_class_from_state(key: str) -> str:
    value = st.session_state.get(key)
    text = str(value or "").strip().upper()
    if text in {"A", "B", "C"}:
        return text
    return POSITION_CLASS_OPTIONS.get(str(value or ""), "")


def _classification_ratio_defaults(
    position_class: str,
    editing_entry: dict | None,
    stock_plan: dict | None,
) -> tuple[float | None, float | None]:
    core_default, trading_default = POSITION_CLASS_DEFAULTS.get(str(position_class or "").upper(), (None, None))
    core = _number((editing_entry or {}).get("core_position_min_pct"))
    trading = _number((editing_entry or {}).get("trading_position_max_pct"))
    if core is None:
        core = _number((stock_plan or {}).get("core_position_min_pct"))
    if trading is None:
        trading = _number((stock_plan or {}).get("trading_position_max_pct"))
    return (core if core is not None else core_default, trading if trading is not None else trading_default)


def _ratio_percent_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return ""
    percent = number * 100 if number <= 1 else number
    return f"{percent:g}"


def _classification_summary(position_class: str) -> str:
    value = str(position_class or "").strip().upper()
    if value == "A":
        return "本次买入将该股票设为 A 类核心股，核心仓最低保留 60%。"
    if value == "B":
        return "本次买入将该股票设为 B 类赔率股，不设核心仓，按交易仓管理。"
    if value == "C":
        return "本次买入将该股票设为 C 类交易股，不设核心仓，快进快出。"
    return "本次买入不设置股票纪律分类。"


def _sync_stock_classification_profile(symbol: str, values: dict) -> None:
    if str(values.get("action_type") or "").strip().lower() not in CLASSIFICATION_ACTIONS:
        return
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return
    position_class = str(values.get("positionClass") or values.get("position_class") or "").strip().upper()
    if position_class not in {"A", "B", "C"}:
        position_class = ""
    store = StockPlanStore()
    plan = store.get_plan(ticker)
    store.save_plan(
        ticker,
        {
            **plan,
            "position_class": position_class,
            "core_position_min_pct": values.get("corePositionMinPct", values.get("core_position_min_pct")),
            "trading_position_max_pct": values.get("tradingPositionMaxPct", values.get("trading_position_max_pct")),
            "classification_note": values.get("classificationNote", values.get("classification_note", "")),
        },
    )


def _save_entry(store: TradeJournalStore, symbol: str, values: dict) -> None:
    if not str(symbol or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请填写股票代码。")
        st.rerun()
    if not str(values.get("quantity") or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请填写数量。")
        st.rerun()
    if not str(values.get("price") or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请填写价格。")
        st.rerun()
    if not str(values.get("decision_mood") or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请选择交易心理标签。")
        st.rerun()
    try:
        saved = store.save_entry(symbol, values)
        _sync_stock_classification_profile(saved["symbol"], values)
        sync_notice = _apply_portfolio_sync_if_requested(saved, values)
    except ValueError as exc:
        st.session_state["trade_journal_notice"] = ("error", _friendly_error(str(exc)))
        st.rerun()
    st.session_state["trade_journal_notice"] = sync_notice or ("success", f"{saved['symbol']} 交易记录已保存。")
    st.rerun()


def _update_entry(store: TradeJournalStore, entry_id: int, symbol: str, values: dict) -> None:
    if not str(symbol or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请填写股票代码。")
        st.rerun()
    if not str(values.get("quantity") or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请填写数量。")
        st.rerun()
    if not str(values.get("price") or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请填写价格。")
        st.rerun()
    if not str(values.get("decision_mood") or "").strip():
        st.session_state["trade_journal_notice"] = ("error", "请选择交易心理标签。")
        st.rerun()
    try:
        saved = store.update_entry(entry_id, symbol, values)
        _sync_stock_classification_profile(saved["symbol"], values)
        sync_notice = _apply_portfolio_sync_if_requested(saved, values)
    except ValueError as exc:
        st.session_state["trade_journal_notice"] = ("error", _friendly_error(str(exc)))
        st.rerun()
    _clear_trade_edit_query()
    st.session_state["trade_journal_notice"] = sync_notice or ("success", f"{saved['symbol']} 交易记录已更新。")
    st.rerun()


def _apply_portfolio_sync_if_requested(saved: dict, values: dict) -> tuple[str, str] | None:
    if not values.get("syncToPortfolio"):
        return None
    if str(saved.get("action_type") or "") in SELL_DISCIPLINE_ACTIONS and str(saved.get("discipline_status") or "") == "blocked":
        return ("error", f"{saved['symbol']} 已保存为违规交易记录；纪律门禁 BLOCK，未同步到组合持仓。")
    result = apply_trade_to_portfolio(int(saved.get("id") or 0))
    status = str(result.get("status") or "")
    if status == "success":
        return ("success", f"{saved['symbol']} 交易记录已保存，组合持仓已同步。")
    if status == "already_synced":
        return ("error", f"{saved['symbol']} 交易记录已保存，但该交易已经同步过，未重复作用到持仓。")
    return ("error", f"{saved['symbol']} 交易记录已保存，但持仓同步失败：{result.get('error') or '未知错误'}")


def _render_notice() -> None:
    notice = st.session_state.pop("trade_journal_notice", None)
    if not notice:
        return
    level, message = notice
    if level == "success":
        st.success(message)
    else:
        st.error(message)


def _render_weekly_discipline_summary() -> None:
    try:
        summary = build_trading_discipline_summary()
    except Exception:
        st.markdown(
            '<section class="weekly-discipline-strip neutral"><div><strong>本周交易纪律</strong><span>暂时无法读取交易纪律统计。</span></div></section>',
            unsafe_allow_html=True,
        )
        return
    level = str(summary.get("overTradingLevel") or "normal")
    headline = {
        "normal": "纪律正常",
        "caution": "本周操作偏多，注意是否焦虑驱动",
        "danger": "交易纪律风险高，建议暂停非必要操作",
    }.get(level, "纪律正常")
    metrics = [
        ("本周交易", summary.get("totalTradesThisWeek", 0)),
        ("sell / trim", summary.get("sellTrimCountThisWeek", 0)),
        ("A 类卖出", summary.get("aClassSellCountThisWeek", 0)),
        ("宏观卖出", summary.get("macroSellCountThisWeek", 0)),
        ("无回补计划", summary.get("noReentryPlanSellCount", 0)),
        ("NOW 式风险", summary.get("nowStyleRiskCount", 0)),
        ("blocker", summary.get("disciplineBlockerCount", 0)),
        ("warning", summary.get("disciplineWarningCount", 0)),
        ("FOMO", summary.get("fomoTradeCount", 0)),
        ("焦虑/恐慌", summary.get("anxietyPanicTradeCount", 0)),
        ("报复交易", summary.get("revengeTradeCount", 0)),
        ("深思/计划", summary.get("reasonedPlanTradeCount", 0)),
    ]
    metric_html = "".join(
        f"<div><span>{escape(label)}</span><b>{escape(str(value))}</b></div>"
        for label, value in metrics
    )
    warnings = [str(item) for item in (summary.get("warnings") or []) if str(item).strip()]
    warning_html = "".join(f"<li>{escape(item)}</li>" for item in warnings[:3])
    warning_block = f"<ul>{warning_html}</ul>" if warning_html else "<em>暂无纪律风险提醒。</em>"
    period = f"{summary.get('periodStart') or ''} - {summary.get('periodEnd') or ''}".strip(" -")
    st.markdown(
        f"""
        <section class="weekly-discipline-strip {escape(_weekly_discipline_tone(level))}">
          <div class="weekly-discipline-head">
            <div>
              <span>本周交易纪律</span>
              <strong>{escape(headline)}</strong>
              <em>{escape(period)}</em>
            </div>
            <b>{escape(_over_trading_level_text(level))}</b>
          </div>
          <div class="weekly-discipline-grid">{metric_html}</div>
          <div class="weekly-discipline-reminder">
            <strong>{escape(str(summary.get("reminderText") or ""))}</strong>
            {warning_block}
          </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _weekly_discipline_tone(level: str) -> str:
    return {
        "normal": "normal",
        "caution": "caution",
        "danger": "danger",
    }.get(str(level or ""), "normal")


def _over_trading_level_text(level: str) -> str:
    return {
        "normal": "正常",
        "caution": "注意",
        "danger": "危险",
    }.get(str(level or ""), "正常")


def _requested_symbol_filter() -> str:
    return str(st.query_params.get("symbol") or "").strip().upper()


def _load_entries(store: TradeJournalStore, symbols: list[str]) -> list[dict]:
    filter_cols = st.columns([1, 3.4])
    requested_symbol = _requested_symbol_filter()
    if requested_symbol in symbols:
        st.session_state["trade-journal-symbol-filter"] = requested_symbol
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

    headers = ["日期", "股票", "操作", "纪律", "数量 / 价格", "期权参数", "关联信号", "备注", "操作"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    row_html = "".join(_entry_row_html(entry) for entry in entries)
    st.markdown(
        (
            '<div id="trade-journal-list"></div>'
            '<div class="trade-journal-table-wrap trade-terminal-table-wrap">'
            '<table class="trade-journal-table trade-terminal-table">'
            "<colgroup>"
            '<col style="width:10%"><col style="width:8%"><col style="width:8%"><col style="width:9%">'
            '<col style="width:11%"><col style="width:10%"><col style="width:9%"><col style="width:auto"><col style="width:136px">'
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


def _render_entry_detail(store: TradeJournalStore) -> None:
    st.markdown('<div id="trade-entry-detail"></div>', unsafe_allow_html=True)
    entry_id = _query_int("viewTrade")
    if entry_id is None:
        return
    entry = store.get_entry(entry_id)
    if not entry:
        _clear_trade_detail_query()
        st.session_state["trade_journal_notice"] = ("error", "交易记录不存在或已删除。")
        st.rerun()

    head_cols = st.columns([4.4, 0.8])
    head_cols[0].markdown(
        (
            '<div class="trade-entry-detail-head">'
            "<span>交易记录详情</span>"
            f"<strong>{escape(_entry_delete_summary(entry))}</strong>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if head_cols[1].button("关闭", key=f"trade-entry-detail-close-{entry_id}", width="stretch"):
        _clear_trade_detail_query()
        st.rerun()

    st.markdown(_entry_detail_html(entry), unsafe_allow_html=True)


def _render_sell_fly_review() -> None:
    st.markdown('<div class="trade-workbench-section replay">卖飞复盘</div>', unsafe_allow_html=True)
    render_section_title("卖飞复盘", "只读检测 sell / trim 后 5d / 10d / 20d 的卖后涨幅，不写入数据库。")
    rows = _sell_fly_rows(build_sell_fly_review_results())
    if not rows:
        st.markdown(
            (
                '<div class="trade-journal-empty signal-empty">'
                "<strong>暂无可复盘的卖出记录或价格序列不足</strong>"
                "<span>需要已有 sell / trim 交易记录、卖出价格，以及卖出后的本地 price_history。</span>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )
        return
    st.caption("疑似卖飞：卖出后 10 日内最高上涨超过阈值。纪律违规：卖出行为与系统允许比例或卖出等级不匹配。")
    st.markdown(_sell_fly_table_html(rows), unsafe_allow_html=True)


def _sell_fly_rows(results: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str, float | None, float | None], dict] = {}
    for item in results:
        key = (
            str(item.get("symbol") or ""),
            str(item.get("tradeDate") or ""),
            str(item.get("actionType") or ""),
            _number(item.get("sellPrice")),
            _number(item.get("quantity")),
        )
        row = grouped.setdefault(
            key,
            {
                "symbol": item.get("symbol"),
                "tradeDate": item.get("tradeDate"),
                "actionType": item.get("actionType"),
                "sellPrice": item.get("sellPrice"),
                "quantity": item.get("quantity"),
                "suspectedSellFly": False,
                "violatedDiscipline": False,
                "reason": "",
                "horizons": {},
            },
        )
        horizon = str(item.get("horizon") or "")
        row["horizons"][horizon] = item
        row["suspectedSellFly"] = bool(row["suspectedSellFly"] or item.get("suspectedSellFly"))
        row["violatedDiscipline"] = bool(row["violatedDiscipline"] or item.get("violatedDiscipline"))
        if item.get("reason") and not str(item.get("reason")).startswith("missing"):
            row["reason"] = str(item.get("reason"))
        elif not row["reason"]:
            row["reason"] = str(item.get("reason") or "")
    return [row for row in grouped.values() if _sell_fly_row_has_data(row)]


def _sell_fly_row_has_data(row: dict) -> bool:
    horizons = row.get("horizons") or {}
    return any((item or {}).get("maxReturnAfterSellPct") is not None for item in horizons.values())


def _sell_fly_table_html(rows: list[dict]) -> str:
    headers = [
        "股票",
        "卖出日期",
        "类型",
        "卖出价格",
        "5d 最大 / 期末",
        "10d 最大 / 期末",
        "20d 最大 / 期末",
        "疑似卖飞",
        "纪律违规",
        "违规原因",
    ]
    body = "".join(_sell_fly_row_html(row) for row in rows[:30])
    return (
        '<div class="trade-journal-table-wrap trade-terminal-table-wrap sell-fly">'
        '<table class="trade-journal-table trade-terminal-table sell-fly">'
        "<colgroup>"
        '<col style="width:8%"><col style="width:10%"><col style="width:8%"><col style="width:10%">'
        '<col style="width:12%"><col style="width:12%"><col style="width:12%"><col style="width:9%">'
        '<col style="width:9%"><col style="width:auto">'
        "</colgroup>"
        f"<thead><tr>{''.join(f'<th>{escape(label)}</th>' for label in headers)}</tr></thead>"
        f"<tbody>{body}</tbody>"
        "</table>"
        "</div>"
    )


def _sell_fly_row_html(row: dict) -> str:
    horizons = row.get("horizons") or {}
    return (
        "<tr>"
        f'<td class="symbol">{escape(_text(row.get("symbol")))}</td>'
        f"<td>{escape(_text(row.get('tradeDate')))}</td>"
        f"<td>{escape(_text(row.get('actionType')))}</td>"
        f"<td>{escape(_money_text(row.get('sellPrice')))}</td>"
        f"<td>{_sell_fly_horizon_cell(horizons.get('5d'))}</td>"
        f"<td>{_sell_fly_horizon_cell(horizons.get('10d'))}</td>"
        f"<td>{_sell_fly_horizon_cell(horizons.get('20d'))}</td>"
        f"<td>{_yes_no_pill(row.get('suspectedSellFly'), positive_tone='blocked')}</td>"
        f"<td>{_yes_no_pill(row.get('violatedDiscipline'), positive_tone='blocked')}</td>"
        f"<td>{escape(_sell_fly_reason_text(row))}</td>"
        "</tr>"
    )


def _sell_fly_horizon_cell(item: dict | None) -> str:
    if not item or item.get("maxReturnAfterSellPct") is None:
        return '<span class="sell-fly-muted">缺历史</span>'
    max_return = format_percent(_number(item.get("maxReturnAfterSellPct")) or 0)
    end_return = format_percent(_number(item.get("endReturnPct")) or 0)
    return (
        '<div class="trade-journal-cell">'
        f"<b>{escape(max_return)}</b>"
        f"<span>{escape('期末 ' + end_return)}</span>"
        "</div>"
    )


def _sell_fly_reason_text(row: dict) -> str:
    if row.get("violatedDiscipline"):
        return "纪律快照含 blocker，且卖出后股价上涨。"
    if row.get("suspectedSellFly"):
        return "卖出后 10 日内最高涨幅超过 8%。"
    reason = str(row.get("reason") or "")
    if reason.startswith("missing"):
        return "价格序列不足。"
    return "未触发 10 日疑似卖飞。"


def _yes_no_pill(value: object, *, positive_tone: str = "ok") -> str:
    truthy = bool(value)
    tone = positive_tone if truthy else "neutral"
    label = "是" if truthy else "否"
    return f'<span class="trade-discipline-pill {escape(tone)}">{escape(label)}</span>'


def _entry_detail_html(entry: dict) -> str:
    base_rows = [
        ("日期", _text(entry.get("trade_date"))),
        ("股票", _text(entry.get("symbol"))),
        ("操作", ACTION_LABELS.get(str(entry.get("action_type") or ""), "未识别")),
        ("交易心理", _decision_mood_text(entry.get("decision_mood"))),
        ("数量", _quantity_text(entry.get("quantity"))),
        ("价格", _money_text(entry.get("price"))),
        ("关联信号", _snapshot_text(entry.get("decision_snapshot_id"))),
    ]
    base_html = _detail_grid_html(base_rows)
    discipline_html = _entry_discipline_snapshot_html(entry)
    notes = escape(_text(entry.get("notes")))
    return (
        '<section class="trade-entry-detail-card">'
        '<h4>基础信息</h4>'
        f"{base_html}"
        f"{_decision_mood_warning_html(entry)}"
        '<h4>交易纪律快照</h4>'
        f"{discipline_html}"
        '<h4>备注</h4>'
        f'<p class="trade-entry-detail-note">{notes}</p>'
        "</section>"
    )


def _entry_discipline_snapshot_html(entry: dict) -> str:
    action = str(entry.get("action_type") or "")
    if action in CLASSIFICATION_ACTIONS:
        return _classification_snapshot_html(entry)
    if action not in SELL_DISCIPLINE_ACTIONS:
        return '<div class="trade-entry-discipline-empty">无卖出纪律检查。</div>'
    if not entry.get("discipline_status"):
        return '<div class="trade-entry-discipline-empty">这条历史卖出 / 减仓记录未保存纪律快照。</div>'

    rows = [
        ("纪律状态", _discipline_status_text(entry.get("discipline_status"))),
        ("股票分类", _text(entry.get("position_class"))),
        ("卖出等级", _text(entry.get("sell_level"))),
        ("计划卖出", _discipline_percent(entry.get("planned_sell_pct"))),
        ("等级上限", _discipline_percent(entry.get("max_allowed_sell_pct"))),
        ("卖出原因", _sell_reason_text(entry.get("sell_reason_type"))),
        ("已有回补计划", _yes_no(entry.get("has_reentry_plan"))),
    ]
    rows.append(("实际卖出", _discipline_percent(entry.get("actual_sell_pct"))))
    if str(entry.get("discipline_status") or "").strip().lower() == "blocked":
        rows.append(("同步限制", "纪律门禁 BLOCK，禁止同步到组合持仓"))
    reentry_html = _entry_reentry_plan_html(entry)
    blocker_html = _discipline_detail_messages_html("阻断提醒", entry.get("blockers") or [], is_blocker=True)
    warning_html = _discipline_detail_messages_html("复核提醒", entry.get("warnings") or [], is_blocker=False)
    reminder = escape(_text(entry.get("reminder_text")))
    return (
        f"{_detail_grid_html(rows)}"
        f"{reentry_html}"
        f"{blocker_html}"
        f"{warning_html}"
        f'<div class="trade-entry-reminder">{reminder}</div>'
    )


def _entry_reentry_plan_html(entry: dict) -> str:
    has_plan = _entry_bool(entry, "has_reentry_plan")
    has_content = bool(
        _text(entry.get("reentry_plan_text"))
        or _text(entry.get("reentry_thesis_invalidation"))
        or _number(entry.get("reentry_pullback_price")) is not None
        or _number(entry.get("reentry_breakout_price")) is not None
    )
    if not has_plan and not has_content:
        return '<div class="trade-entry-discipline-empty">未记录具体回补计划。</div>'
    rows = [
        ("回踩买回", _reentry_price_pct_text(entry.get("reentry_pullback_price"), entry.get("reentry_buy_back_pct_on_pullback"))),
        ("不跌反涨买回", _reentry_price_pct_text(entry.get("reentry_breakout_price"), entry.get("reentry_buy_back_pct_on_breakout"))),
        ("时间止损", _reentry_time_stop_text(entry.get("reentry_time_stop_days"), entry.get("reentry_buy_back_pct_on_breakout"))),
        ("不回补条件", _text(entry.get("reentry_thesis_invalidation"))),
    ]
    summary = escape(_text(entry.get("reentry_plan_text")))
    return (
        '<div class="trade-entry-reentry-plan">'
        '<b>回补计划</b>'
        f"{_detail_grid_html(rows)}"
        f'<p>{summary}</p>'
        "</div>"
    )


def _reentry_price_pct_text(price: object, pct: object) -> str:
    price_text = _money_text(price)
    pct_text = _discipline_percent(pct)
    if price_text == BLANK_TEXT and pct_text == BLANK_TEXT:
        return BLANK_TEXT
    if price_text == BLANK_TEXT:
        return pct_text
    if pct_text == BLANK_TEXT:
        return price_text
    return f"{price_text} / {pct_text}"


def _reentry_time_stop_text(days: object, pct: object) -> str:
    number = _number(days)
    if number is None:
        return BLANK_TEXT
    pct_text = _discipline_percent(pct)
    suffix = "" if pct_text == BLANK_TEXT else f"后买回 {pct_text}"
    return f"{int(number)} 个交易日{suffix}"


def _classification_snapshot_html(entry: dict) -> str:
    position_class = str(entry.get("position_class") or "").strip().upper()
    if position_class not in {"A", "B", "C"}:
        return '<div class="trade-entry-discipline-empty">本次买入未设置股票纪律分类。</div>'
    rows = [
        ("股票分类", _position_class_label(position_class)),
        ("核心仓最低", _discipline_percent(entry.get("core_position_min_pct"))),
        ("交易仓上限", _discipline_percent(entry.get("trading_position_max_pct"))),
        ("分类备注", _text(entry.get("classification_note"))),
    ]
    return (
        f"{_detail_grid_html(rows)}"
        f'<div class="trade-entry-reminder">{escape(POSITION_CLASS_COPY.get(position_class, ""))}</div>'
    )


def _detail_grid_html(rows: list[tuple[str, str]]) -> str:
    return (
        '<div class="trade-entry-detail-grid">'
        + "".join(
            "<div>"
            f"<span>{escape(label)}</span>"
            f"<strong>{escape(value)}</strong>"
            "</div>"
            for label, value in rows
        )
        + "</div>"
    )


def _discipline_detail_messages_html(title: str, items: list[object], *, is_blocker: bool) -> str:
    if not items:
        return ""
    class_name = "blockers" if is_blocker else "warnings"
    rows = "".join(f"<li>{escape(_discipline_message_text(item))}</li>" for item in items)
    return f'<div class="trade-entry-detail-messages {class_name}"><b>{escape(title)}</b><ul>{rows}</ul></div>'


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
        f"<td>{_discipline_snapshot_badge(entry)}</td>"
        f"<td>{_cell_html(_quantity_text(entry.get('quantity')), _money_text(entry.get('price')))}</td>"
        f"<td>{_option_text(entry)}</td>"
        f"<td>{escape(_snapshot_text(entry.get('decision_snapshot_id')))}</td>"
        f'<td class="notes">{escape(_text(entry.get("notes")))}</td>'
        f'<td class="trade-entry-actions"><span class="zhx-action-group trade-entry-action-group">{_entry_actions_html(entry)}</span></td>'
        "</tr>"
    )


def _entry_actions_html(entry: dict) -> str:
    return (
        f"{_stock_detail_action_html(entry)}"
        '<span class="trade-entry-more-wrap">'
        '<button class="trade-entry-more-button" type="button">更多</button>'
        f'<span class="trade-entry-more-menu">{_entry_edit_action_html(entry)}{_entry_detail_action_html(entry)}{_entry_delete_action_html(entry)}</span>'
        "</span>"
    )


def _stock_detail_action_html(entry: dict) -> str:
    symbol = str(entry.get("symbol") or "").strip().upper()
    if not symbol:
        return BLANK_TEXT
    return (
        f'<a class="trade-entry-detail-link" href="?page=detail&symbol={escape(symbol, quote=True)}" '
        'target="_self" title="查看个股研究详情">详情</a>'
    )


def _entry_detail_action_html(entry: dict) -> str:
    entry_id = int(entry.get("id") or 0)
    if entry_id <= 0:
        return BLANK_TEXT
    return (
        f'<a class="trade-entry-record-link" href="?page=trade-journal&viewTrade={entry_id}#trade-entry-detail" '
        'target="_self" title="查看这条交易记录快照">记录</a>'
    )


def _entry_edit_action_html(entry: dict) -> str:
    entry_id = int(entry.get("id") or 0)
    if entry_id <= 0:
        return BLANK_TEXT
    return (
        f'<a class="trade-entry-edit-link" href="?page=trade-journal&editTrade={entry_id}#trade-journal-editor" '
        'target="_self" title="编辑这条交易记录">编辑</a>'
    )


def _entry_delete_action_html(entry: dict) -> str:
    entry_id = int(entry.get("id") or 0)
    if entry_id <= 0:
        return BLANK_TEXT
    return (
        f'<a class="trade-entry-delete-link" href="?page=trade-journal&deleteTrade={entry_id}#trade-journal-list" '
        'target="_self" title="删除这条交易记录">删除</a>'
    )


def _discipline_snapshot_badge(entry: dict) -> str:
    action = str(entry.get("action_type") or "")
    if action in CLASSIFICATION_ACTIONS:
        position_class = str(entry.get("position_class") or "").strip().upper()
        if position_class in {"A", "B", "C"}:
            return f'<span class="trade-discipline-pill ok">{escape(position_class + " 类")}</span>'
        return '<span class="trade-discipline-pill neutral">未分类</span>'
    if action not in SELL_DISCIPLINE_ACTIONS:
        return '<span class="trade-discipline-pill neutral">无</span>'
    status = str(entry.get("discipline_status") or "").strip().lower()
    if not status:
        return '<span class="trade-discipline-pill neutral">未保存</span>'
    tone = _discipline_status_tone(status)
    label = DISCIPLINE_STATUS_COMPACT_LABELS.get(status, DISCIPLINE_STATUS_LABELS.get(status, status))
    return f'<span class="trade-discipline-pill {escape(tone)}">{escape(label)}</span>'


def _discipline_status_text(value: object) -> str:
    status = str(value or "").strip().lower()
    return DISCIPLINE_STATUS_COMPACT_LABELS.get(status, DISCIPLINE_STATUS_LABELS.get(status, status or BLANK_TEXT))


def _sell_reason_text(value: object) -> str:
    reason = str(value or "").strip().lower()
    return SELL_REASON_LABELS.get(reason, reason or BLANK_TEXT)


def _decision_mood_text(value: object) -> str:
    mood = str(value or "").strip()
    return DECISION_MOOD_LABELS.get(mood, "未记录")


def _decision_mood_warning_html(entry: dict) -> str:
    action = str(entry.get("action_type") or "").lower()
    mood = str(entry.get("decision_mood") or "").strip()
    if action in SELL_DISCIPLINE_ACTIONS and mood in SELL_EMOTIONAL_MOODS:
        text = "该卖出可能由情绪驱动，请确认投资逻辑是否真的破坏。"
    elif action in {"buy", "add"} and mood in BUY_EMOTIONAL_MOODS:
        text = "该买入可能是追涨或卖飞后追回，不应替代原计划。"
    else:
        return ""
    return f'<div class="trade-entry-mood-warning"><b>WARN</b><span>{escape(text)}</span></div>'


def _editor_key(field: str, entry_id: int | None) -> str:
    return f"trade-journal-edit-{entry_id}-{field}" if entry_id is not None else f"trade-journal-{field}"


def _entry_value(entry: dict | None, key: str) -> str:
    if not entry:
        return ""
    value = entry.get(key)
    return "" if value is None else str(value)


def _entry_number_text(entry: dict | None, key: str) -> str:
    number = _number((entry or {}).get(key))
    if number is None:
        return ""
    return f"{number:g}"


def _entry_int_text(entry: dict | None, key: str) -> str:
    number = _number((entry or {}).get(key))
    if number is None:
        return ""
    return str(int(number))


def _entry_percent_text(entry: dict | None, key: str, default: str) -> str:
    number = _number((entry or {}).get(key))
    if number is None:
        return default
    return f"{number * 100:g}"


def _entry_bool(entry: dict | None, key: str) -> bool:
    value = (entry or {}).get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _entry_date(entry: dict | None) -> date:
    parsed = _parse_iso_date((entry or {}).get("trade_date"))
    return parsed or date.today()


def _action_label_for_entry(entry: dict | None) -> str:
    action = str((entry or {}).get("action_type") or "")
    label = ACTION_LABELS.get(action)
    return label if label in ACTION_OPTIONS else list(ACTION_OPTIONS)[0]


def _decision_mood_label_for_entry(entry: dict | None) -> str:
    mood = str((entry or {}).get("decision_mood") or "")
    label = DECISION_MOOD_LABELS.get(mood)
    return label if label in DECISION_MOOD_OPTIONS else list(DECISION_MOOD_OPTIONS)[0]


def _sell_reason_label_for_entry(entry: dict | None) -> str:
    reason = str((entry or {}).get("sell_reason_type") or "")
    label = SELL_REASON_LABELS.get(reason)
    return label if label in SELL_REASON_OPTIONS else list(SELL_REASON_OPTIONS)[0]


def _discipline_percent(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return format_percent(number, already_percent=False)


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


def _clear_trade_detail_query() -> None:
    if "viewTrade" in st.query_params:
        st.query_params.pop("viewTrade")


def _clear_trade_edit_query() -> None:
    if "editTrade" in st.query_params:
        st.query_params.pop("editTrade")


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
    if "decision_mood is invalid" in message:
        return "请选择有效的交易心理标签。"
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
        .weekly-discipline-strip {
            margin: 0.65rem 0 0.82rem;
            padding: 0.62rem 0.72rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-left: 3px solid #4f9d78;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.82);
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.025);
        }
        .weekly-discipline-strip.caution {
            border-left-color: #c59a32;
            background: linear-gradient(90deg, rgba(197, 154, 50, 0.055), rgba(255, 255, 255, 0.82) 42%);
        }
        .weekly-discipline-strip.danger {
            border-left-color: #b85c50;
            background: linear-gradient(90deg, rgba(184, 92, 80, 0.06), rgba(255, 255, 255, 0.82) 42%);
        }
        .weekly-discipline-head {
            display: flex;
            align-items: start;
            justify-content: space-between;
            gap: 0.7rem;
            margin-bottom: 0.48rem;
        }
        .weekly-discipline-head span,
        .weekly-discipline-grid span {
            display: block;
            color: #64748b;
            font-size: 0.68rem;
            font-weight: 760;
            line-height: 1.2;
        }
        .weekly-discipline-head strong {
            display: block;
            margin-top: 0.12rem;
            color: #0f172a;
            font-size: 0.92rem;
            font-weight: 840;
            line-height: 1.25;
        }
        .weekly-discipline-head em {
            display: block;
            margin-top: 0.1rem;
            color: #94a3b8;
            font-size: 0.66rem;
            font-style: normal;
            font-weight: 650;
        }
        .weekly-discipline-head > b {
            display: inline-flex;
            align-items: center;
            min-height: 24px;
            padding: 0 0.55rem;
            border-radius: 999px;
            background: rgba(15, 23, 42, 0.045);
            color: #334155;
            font-size: 0.7rem;
            font-weight: 820;
            white-space: nowrap;
        }
        .weekly-discipline-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(86px, 1fr));
            border: 1px solid rgba(15, 23, 42, 0.055);
            border-radius: 7px;
            overflow: hidden;
            background: rgba(248, 250, 252, 0.72);
        }
        .weekly-discipline-grid div {
            padding: 0.42rem 0.5rem;
            border-right: 1px solid rgba(15, 23, 42, 0.055);
        }
        .weekly-discipline-grid div:last-child {
            border-right: 0;
        }
        .weekly-discipline-grid b {
            display: block;
            margin-top: 0.12rem;
            color: #0f172a;
            font-size: 0.96rem;
            font-weight: 860;
            line-height: 1.1;
        }
        .weekly-discipline-reminder {
            margin-top: 0.48rem;
            color: #475569;
            font-size: 0.74rem;
            line-height: 1.38;
        }
        .weekly-discipline-reminder > strong {
            display: block;
            color: #334155;
            font-size: 0.76rem;
            font-weight: 760;
        }
        .weekly-discipline-reminder ul {
            margin: 0.24rem 0 0;
            padding-left: 1rem;
        }
        .weekly-discipline-reminder em {
            color: #7b8798;
            font-style: normal;
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
            overflow: visible;
            box-shadow: none;
        }
        .trade-journal-table-wrap.signal {
            margin-top: 0.35rem;
        }
        .trade-journal-table {
            width: 100%;
            min-width: 980px;
            border-collapse: collapse;
            table-layout: fixed;
            font-size: 12px;
        }
        .trade-journal-table.signal {
            min-width: 620px;
        }
        .trade-journal-table.sell-fly {
            min-width: 1080px;
        }
        .trade-journal-table th {
            height: 28px;
            padding: 0 10px;
            border-bottom: 1px solid var(--trade-terminal-line, rgba(15, 23, 42, 0.055));
            background: var(--trade-terminal-head, #F8FAFC);
            color: #64748B;
            font-size: 11px;
            font-weight: 650;
            text-align: left;
        }
        .trade-journal-table td {
            height: 46px;
            padding: 0 10px;
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
        .sell-fly-muted {
            color: #94a3b8;
            font-size: 11px;
        }
        .trade-entry-actions {
            text-align: center;
            width: 136px;
            overflow: visible;
        }
        .trade-entry-action-group {
            min-width: 104px;
            padding: 0;
            border: 0;
            background: transparent;
            margin: 0 auto;
            display: inline-flex;
            justify-content: center;
            align-items: center;
            gap: 0.24rem;
            position: relative;
        }
        .trade-entry-actions::after {
            content: "";
            display: inline-flex;
            vertical-align: middle;
        }
        .trade-entry-delete-link,
        .trade-entry-delete-link:visited,
        .trade-entry-detail-link,
        .trade-entry-detail-link:visited,
        .trade-entry-edit-link,
        .trade-entry-edit-link:visited,
        .trade-entry-record-link,
        .trade-entry-record-link:visited,
        .trade-entry-more-button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            height: 26px;
            min-width: 40px;
            padding: 0 0.28rem;
            border: 1px solid transparent;
            border-radius: 4px;
            background: transparent;
            color: #52657F;
            font-size: 12px;
            font-weight: 650;
            text-decoration: none !important;
            white-space: nowrap;
            cursor: pointer;
        }
        .trade-entry-delete-link:hover,
        .trade-entry-detail-link:hover,
        .trade-entry-edit-link:hover,
        .trade-entry-record-link:hover,
        .trade-entry-more-button:hover {
            border-color: rgba(15, 23, 42, 0.08);
            background: #FFFFFF;
            color: #0F172A;
            text-decoration: none !important;
        }
        .trade-entry-detail-link,
        .trade-entry-detail-link:visited,
        .trade-entry-edit-link,
        .trade-entry-edit-link:visited,
        .trade-entry-record-link,
        .trade-entry-record-link:visited {
            color: #2563eb;
        }
        .trade-entry-more-wrap {
            position: relative;
            display: inline-flex;
        }
        .trade-entry-more-menu {
            position: absolute;
            right: 0;
            top: calc(100% + 4px);
            z-index: 30;
            display: none;
            min-width: 96px;
            padding: 0.2rem;
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 6px;
            background: #fff;
            box-shadow: 0 8px 18px rgba(15, 23, 42, 0.10);
        }
        .trade-entry-more-wrap:hover .trade-entry-more-menu,
        .trade-entry-more-wrap:focus-within .trade-entry-more-menu {
            display: grid;
            gap: 0.12rem;
        }
        .trade-entry-more-menu a,
        .trade-entry-more-menu a:visited {
            justify-content: flex-start;
            height: 24px;
            min-width: 0;
            width: 100%;
            padding: 0 0.46rem;
            border-radius: 4px;
            color: #52657F !important;
        }
        .trade-entry-more-menu a:hover {
            color: #0F172A !important;
        }
        .trade-discipline-pill {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 42px;
            height: 20px;
            padding: 0 0.45rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 999px;
            background: #f8fafc;
            color: #64748b;
            font-size: 11px;
            font-weight: 720;
            white-space: nowrap;
        }
        .trade-discipline-pill.ok {
            border-color: rgba(79, 157, 120, 0.18);
            background: rgba(79, 157, 120, 0.08);
            color: #276749;
        }
        .trade-discipline-pill.warning {
            border-color: rgba(181, 106, 50, 0.18);
            background: rgba(181, 106, 50, 0.08);
            color: #8A4B00;
        }
        .trade-discipline-pill.blocked {
            border-color: rgba(185, 28, 28, 0.18);
            background: rgba(254, 226, 226, 0.72);
            color: #991b1b;
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
            gap: 0.08rem;
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
        .trade-entry-detail-head {
            margin: 0.75rem 0 0.35rem;
            padding: 0.58rem 0.68rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.82);
        }
        .trade-entry-detail-head span {
            display: block;
            color: #64748b;
            font-size: 0.68rem;
            font-weight: 760;
        }
        .trade-entry-detail-head strong {
            display: block;
            margin-top: 0.1rem;
            color: #0f172a;
            font-size: 0.88rem;
            font-weight: 840;
        }
        .trade-entry-detail-card {
            margin: 0.35rem 0 0.9rem;
            padding: 0.65rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: #fff;
        }
        .trade-entry-detail-card h4 {
            margin: 0.1rem 0 0.42rem;
            color: #0f172a;
            font-size: 0.82rem;
            font-weight: 840;
        }
        .trade-entry-detail-card h4:not(:first-child) {
            margin-top: 0.72rem;
        }
        .trade-entry-detail-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.45rem;
        }
        .trade-entry-detail-grid div {
            min-height: 48px;
            padding: 0.42rem 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 6px;
            background: rgba(248, 250, 252, 0.72);
        }
        .trade-entry-detail-grid span {
            display: block;
            color: #64748b;
            font-size: 0.66rem;
            font-weight: 760;
        }
        .trade-entry-detail-grid strong {
            display: block;
            margin-top: 0.12rem;
            color: #0f172a;
            font-size: 0.8rem;
            font-weight: 820;
            overflow-wrap: anywhere;
        }
        .trade-entry-discipline-empty,
        .trade-entry-reminder,
        .trade-entry-detail-note {
            margin: 0;
            padding: 0.48rem 0.58rem;
            border: 1px dashed rgba(15, 23, 42, 0.12);
            border-radius: 6px;
            background: rgba(248, 250, 252, 0.74);
            color: #64748b;
            font-size: 0.76rem;
        }
        .trade-entry-reminder {
            margin-top: 0.45rem;
            border-style: solid;
            background: rgba(239, 246, 255, 0.62);
            color: #334155;
        }
        .trade-entry-reentry-plan {
            margin-top: 0.48rem;
            padding: 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.66);
        }
        .trade-entry-reentry-plan > b {
            display: block;
            margin-bottom: 0.4rem;
            color: #0F172A;
            font-size: 0.76rem;
            font-weight: 820;
        }
        .trade-entry-reentry-plan p {
            margin: 0.42rem 0 0;
            color: #475569;
            font-size: 0.74rem;
            line-height: 1.45;
        }
        .trade-entry-mood-warning {
            display: flex;
            align-items: center;
            gap: 0.45rem;
            margin-top: 0.5rem;
            padding: 0.48rem 0.58rem;
            border: 1px solid rgba(185, 28, 28, 0.14);
            border-radius: 6px;
            background: rgba(254, 226, 226, 0.52);
            color: #991b1b;
            font-size: 0.76rem;
        }
        .trade-entry-mood-warning b {
            font-size: 0.68rem;
            letter-spacing: 0.04em;
        }
        .trade-entry-detail-messages {
            margin-top: 0.45rem;
            padding: 0.48rem 0.58rem;
            border-radius: 6px;
            font-size: 0.76rem;
        }
        .trade-entry-detail-messages.blockers {
            border: 1px solid rgba(185, 28, 28, 0.15);
            background: rgba(254, 226, 226, 0.48);
            color: #991b1b;
        }
        .trade-entry-detail-messages.warnings {
            border: 1px solid rgba(181, 106, 50, 0.15);
            background: rgba(255, 251, 235, 0.62);
            color: #8A4B00;
        }
        .trade-entry-detail-messages ul {
            margin: 0.25rem 0 0;
            padding-left: 1rem;
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
        .trade-discipline-title {
            margin: 0.64rem 0 0.35rem;
            color: #0F172A;
            font-size: 0.76rem;
            font-weight: 760;
            letter-spacing: 0;
        }
        .trade-reentry-state {
            min-height: 38px;
            display: flex;
            align-items: center;
            color: #64748B;
            font-size: 0.66rem;
            font-weight: 700;
        }
        .trade-reentry-shell {
            margin: 0.56rem 0 0.42rem;
            padding: 0.52rem 0.64rem;
            border: 1px solid rgba(15, 23, 42, 0.075);
            border-left: 3px solid rgba(79, 157, 120, 0.58);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.72);
        }
        .trade-reentry-shell.alert {
            border-left-color: rgba(181, 106, 50, 0.78);
            background: rgba(255, 251, 235, 0.58);
        }
        .trade-reentry-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.85rem;
        }
        .trade-reentry-head strong {
            color: #0F172A;
            font-size: 0.78rem;
            font-weight: 820;
        }
        .trade-reentry-head span {
            color: #64748B;
            font-size: 0.68rem;
            text-align: right;
        }
        .trade-gate-card {
            margin: 0.52rem 0 0.66rem;
            padding: 0.68rem 0.76rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-left: 4px solid #4F9D78;
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
        }
        .trade-gate-card.warn {
            border-left-color: #C59A32;
            background: linear-gradient(180deg, rgba(255, 251, 235, 0.86), rgba(255, 255, 255, 0.92));
        }
        .trade-gate-card.fix {
            border-left-color: #B56A32;
            background: linear-gradient(180deg, rgba(255, 247, 237, 0.9), rgba(255, 255, 255, 0.92));
        }
        .trade-gate-card.block {
            border-left-color: #B91C1C;
            background: linear-gradient(180deg, rgba(255, 245, 245, 0.94), rgba(255, 255, 255, 0.92));
        }
        .trade-gate-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.8rem;
            margin-bottom: 0.52rem;
        }
        .trade-gate-head strong {
            color: #0F172A;
            font-size: 0.86rem;
            font-weight: 860;
        }
        .trade-gate-head span {
            color: #64748B;
            font-size: 0.7rem;
            text-align: right;
        }
        .trade-gate-body {
            display: grid;
            grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
            gap: 0.55rem;
            margin-bottom: 0.58rem;
        }
        .trade-gate-body > div {
            padding: 0.48rem 0.56rem;
            border: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 7px;
            background: rgba(255, 255, 255, 0.68);
        }
        .trade-gate-body b,
        .trade-gate-subtitle {
            color: #0F172A;
            font-size: 0.72rem;
            font-weight: 820;
        }
        .trade-gate-body ul {
            margin: 0.3rem 0 0;
            padding-left: 1rem;
            color: #475569;
            font-size: 0.7rem;
            line-height: 1.48;
        }
        .trade-gate-subtitle {
            margin: 0.48rem 0 0.28rem;
        }
        .trade-gate-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            border: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 7px;
            overflow: hidden;
        }
        .trade-gate-grid div {
            display: grid;
            gap: 0.08rem;
            min-width: 0;
            padding: 0.4rem 0.48rem;
            border-right: 1px solid rgba(15, 23, 42, 0.055);
            border-bottom: 1px solid rgba(15, 23, 42, 0.045);
            background: rgba(255, 255, 255, 0.62);
        }
        .trade-gate-grid span {
            color: #64748B;
            font-size: 0.64rem;
            white-space: nowrap;
        }
        .trade-gate-grid strong {
            color: #0F172A;
            font-size: 0.76rem;
            font-weight: 820;
            white-space: nowrap;
        }
        .trade-classification-summary {
            display: grid;
            grid-template-columns: minmax(220px, 1.2fr) minmax(220px, 1fr) minmax(220px, 1fr);
            gap: 0.55rem;
            align-items: center;
            margin: 0.45rem 0 0.68rem;
            padding: 0.56rem 0.68rem;
            border: 1px solid rgba(15, 23, 42, 0.075);
            border-radius: 8px;
            background: linear-gradient(180deg, rgba(248, 250, 252, 0.9), rgba(255, 255, 255, 0.86));
        }
        .trade-classification-summary strong {
            color: #0F172A;
            font-size: 0.78rem;
            font-weight: 820;
        }
        .trade-classification-summary span,
        .trade-classification-summary em {
            color: #64748B;
            font-size: 0.7rem;
            font-style: normal;
        }
        .trade-portfolio-sync-card {
            margin: 0.45rem 0 0.68rem;
            padding: 0.62rem 0.72rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.88);
        }
        .trade-portfolio-sync-card.warning {
            border-color: rgba(164, 48, 63, 0.18);
            background: rgba(255, 245, 245, 0.88);
        }
        .trade-portfolio-sync-card > div:first-child {
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            margin-bottom: 0.48rem;
        }
        .trade-portfolio-sync-card b {
            color: #0F172A;
            font-size: 0.76rem;
        }
        .trade-portfolio-sync-card span {
            color: #64748B;
            font-size: 0.68rem;
            text-align: right;
        }
        .trade-portfolio-sync-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.35rem;
        }
        .trade-portfolio-sync-grid div {
            display: grid;
            gap: 0.06rem;
            padding: 0.35rem 0.42rem;
            border: 1px solid rgba(15, 23, 42, 0.055);
            border-radius: 6px;
            background: rgba(255, 255, 255, 0.72);
        }
        .trade-portfolio-sync-grid span,
        .trade-portfolio-sync-grid strong {
            text-align: left;
            white-space: nowrap;
        }
        .trade-discipline-card {
            margin: 0.45rem 0 0.68rem;
            padding: 0.72rem 0.82rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
        }
        .trade-discipline-card.warning {
            border-color: rgba(181, 106, 50, 0.18);
            background: linear-gradient(180deg, rgba(255, 251, 235, 0.92), rgba(255, 255, 255, 0.86));
        }
        .trade-discipline-card.blocked {
            border-color: rgba(164, 48, 63, 0.2);
            background: linear-gradient(180deg, rgba(255, 245, 245, 0.94), rgba(255, 255, 255, 0.88));
        }
        .trade-discipline-card.ok {
            border-color: rgba(79, 157, 120, 0.18);
            background: linear-gradient(180deg, rgba(244, 250, 246, 0.94), rgba(255, 255, 255, 0.88));
        }
        .trade-discipline-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.8rem;
            margin-bottom: 0.5rem;
        }
        .trade-discipline-head strong {
            color: #0F172A;
            font-size: 0.82rem;
            font-weight: 820;
            white-space: nowrap;
        }
        .trade-discipline-head span {
            color: #64748B;
            font-size: 0.72rem;
            text-align: right;
        }
        .trade-discipline-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            border: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 6px;
            overflow: hidden;
        }
        .trade-discipline-grid div {
            display: grid;
            gap: 0.08rem;
            min-width: 0;
            padding: 0.42rem 0.5rem;
            border-right: 1px solid rgba(15, 23, 42, 0.055);
            background: rgba(255, 255, 255, 0.62);
        }
        .trade-discipline-grid div:last-child {
            border-right: 0;
        }
        .trade-discipline-grid span {
            color: #64748B;
            font-size: 0.66rem;
            white-space: nowrap;
        }
        .trade-discipline-grid strong {
            color: #0F172A;
            font-size: 0.75rem;
            font-weight: 780;
            white-space: nowrap;
        }
        .trade-discipline-messages {
            margin-top: 0.5rem;
            color: #52657F;
            font-size: 0.72rem;
        }
        .trade-discipline-messages b {
            display: block;
            margin-bottom: 0.18rem;
            color: #334155;
            font-size: 0.68rem;
        }
        .trade-discipline-messages ul {
            margin: 0;
            padding-left: 1rem;
        }
        .trade-discipline-messages.blockers {
            color: #8A1F1F;
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
