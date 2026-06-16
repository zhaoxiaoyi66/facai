from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

import streamlit as st

from data.ai_stock_radar import build_cached_ai_stock_radar_report
from data.decision_log import (
    DecisionErrorTagStore,
    DecisionLogStore,
    DecisionOutcomeStore,
    TradeJournalStore,
    build_decision_signal_stats,
    refresh_decision_outcomes,
)
from data.discipline_review import DISCIPLINE_TAG_LABELS, DisciplineReviewStore, label_for_tag
from data.entry_display import format_buy_zone, format_zone_status
from data.macro_regime import load_macro_regime, macro_regime_trade_hint_text
from data.trade_gate import buy_gate_entry_fields, evaluate_buy_gate
from data.portfolio_trade_sync import (
    POSITION_AFFECTING_ACTIONS,
    apply_trade_to_portfolio,
    get_trade_portfolio_sync_status,
    preview_trade_values_portfolio_effect,
)
from data.portfolio import PortfolioPositionStore
from data.portfolio_roles import (
    ROLE_OBSERVATION,
    portfolio_role_core_tactical_split,
    portfolio_role_label,
    portfolio_role_target_weight,
)
from data.portfolio_view_model import build_portfolio_view_model
from data.sell_fly_review import build_sell_fly_review_results
from data.sell_review import evaluate_sell_review_flags, format_sell_review_label
from data.stock_plan import StockPlanStore
from data.trade_performance import EVENT_EXIT_REASONS, EVENT_PLAN_KEYWORDS, summarize_trade_performance
from data.trade_activity import (
    activity_level_label,
    build_daily_trade_activity,
    build_monthly_trade_calendar,
)
from data.trade_intent import TradeIntentStore
from data.trade_safety_gate import has_concrete_reentry_plan
from data.trading_discipline import evaluate_trading_discipline
from data.trading_discipline_stats import build_trading_discipline_summary
from formatting import format_currency, format_percent
from ui.theme import render_page_header, render_section_title
from ui.trade_intent import intent_record_html, render_trade_intent_dialog


ACTION_OPTIONS = {
    "买入": "buy",
    "卖出": "sell",
    "加仓": "add",
    "减仓": "trim",
    "放弃操作": "skip",
}
ACTION_LABELS = {value: label for label, value in ACTION_OPTIONS.items()}
SELL_ENTRY_ACTION_OPTIONS = {
    "减仓": "trim",
    "清仓": "sell",
}
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
    "A": "长期核心，不建议因宏观恐慌清仓。",
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
    "未选择": "",
    "止盈": "take_profit",
    "止损": "stop_loss",
    "清仓": "full_exit",
    "减仓": "trim_position",
    "换仓": "rotation",
    "风险控制": "risk_control",
    "恐慌卖出": "panic_sell",
    "其他": "other",
    "宏观风险": "macro",
    "技术破位 / 过热": "technical",
    "估值过高": "valuation",
    "仓位过重": "position_size",
    "目标价触发": "target_price",
    "财报催化结束": "earnings_catalyst_done",
    "事件交易结束": "event_trade_done",
    "催化失败": "catalyst_failed",
    "财报后无反应": "no_post_earnings_reaction",
    "按计划退出": "planned_exit",
    "降级为观察": "downgrade_watch",
    "买入逻辑不清": "unclear_thesis",
    "风险控制": "risk_control",
    "投资假设破裂": "thesis_broken",
}
SELL_CONTEXT_TYPE_OPTIONS = {
    "请选择": "",
    "估值压缩 / 风险溢价上升": "valuation_compression",
    "流动性冲击 / 市场恐慌": "liquidity_shock",
    "基本面改写": "fundamental_change",
    "仓位超限": "position_risk",
    "计划内减仓": "planned_reduction",
    "情绪性卖出": "emotional_sell",
    "其他": "other",
}
SELL_CONTEXT_TYPE_LABELS = {value: label for label, value in SELL_CONTEXT_TYPE_OPTIONS.items()}
FUNDAMENTAL_CHANGE_OPTIONS = {
    "收入增速恶化": "revenue_growth_deterioration",
    "利润率恶化": "margin_deterioration",
    "需求路径变化": "demand_path_change",
    "融资条件变化": "financing_condition_change",
    "监管环境变化": "regulatory_change",
    "竞争格局变化": "competitive_landscape_change",
    "管理层执行问题": "management_execution_issue",
    "财务质量恶化": "financial_quality_deterioration",
    "指引下修": "guidance_cut",
    "其他": "other",
}
FUNDAMENTAL_CHANGE_LABELS = {value: label for label, value in FUNDAMENTAL_CHANGE_OPTIONS.items()}
SELL_REASON_TAG_OPTIONS = {
    "估值": "valuation",
    "风险控制": "risk_control",
    "仓位管理": "position_management",
    "流动性 / 市场环境": "liquidity_market",
    "thesis 变化": "thesis_change",
    "纪律止盈": "discipline_take_profit",
    "纪律止损": "discipline_stop_loss",
    "其他": "other",
}
SELL_REASON_TAG_LABELS = {value: label for label, value in SELL_REASON_TAG_OPTIONS.items()}
DECISION_MOOD_OPTIONS = {
    "请选择": "",
    "平静 / 无明显情绪": "NEUTRAL",
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
DECISION_MOOD_OPTIONS["抄底冲动"] = "bottom_fishing_impulse"
DECISION_MOOD_LABELS = {value: label for label, value in DECISION_MOOD_OPTIONS.items() if value}
SELL_EMOTIONAL_MOODS = {"anxiety", "macro_fear", "panic_sell", "regret_chase"}
BUY_EMOTIONAL_MOODS = {"fomo", "anxiety", "bottom_fishing_impulse", "revenge_trade", "regret_chase"}
FIX_REQUIRED_BLOCKERS = {"planned_actual_sell_pct_mismatch", "reentry_plan_required_before_trim_or_sell"}
DISCIPLINE_STATUS_LABELS = {
    "allowed": "风险较低",
    "warning": "需要复核",
    "blocked": "纪律不建议执行",
    "hold": "无需卖出",
}
DISCIPLINE_STATUS_COMPACT_LABELS = {
    "allowed": "通过",
    "warning": "警告",
    "blocked": "高风险",
    "hold": "无",
}
DISCIPLINE_BLOCKER_LABELS = {
    "now_style_error_risk": "NOW 式错误风险：A 类核心股在投资逻辑未破坏时，不应因宏观恐慌或情绪压力卖出核心仓。若你不愿右侧追回，就不能全卖低位买到的好公司。",
    "a_class_core_clear_requires_thesis_break": "A 类核心仓不能在投资逻辑未破裂时清仓。",
    "a_class_core_sale_blocked_while_gain_0_to_25_pct": "A 类持仓在 0-25% 浮盈区间不建议卖核心仓。",
    "sell_level_does_not_allow_core_sale": "当前卖出等级不允许动核心仓。",
    "macro_risk_cannot_trigger_single_name_exit": "宏观风险不能单独触发个股清仓。",
    "reentry_plan_required_before_trim_or_sell": "减仓 / 卖出前需要明确回补计划。",
    "actual_sell_pct_exceeds_sell_level_limit": "实际卖出比例超过当前纪律等级上限。",
    "planned_sell_pct_exceeds_sell_level_limit": "计划卖出比例超过当前纪律等级上限。",
    "b_class_position_size_requires_actual_overlimit": "B 类不能把“仓位过重”当作默认放行理由；需要确认实际仓位已经超出 B 类上限。",
    "b_class_low_sell_requires_downgrade_or_thesis": "B 类低于目标价或买区内卖出，需要选择降级为观察、买入逻辑不清、thesis 失效或风险控制，并写清逻辑变化。",
}
DISCIPLINE_BLOCKER_LABELS.update(
    {
        "planned_actual_sell_pct_mismatch": "计划卖出比例与实际卖出数量不一致。",
        "a_class_macro_or_emotional_sell_exceeds_20_pct": "A 类核心股在宏观或情绪压力下，默认最多只能卖出 20%。",
        "a_class_core_floor_breached": "该操作会打穿 A 类核心仓底仓保护。",
        "planned_sell_pct_breaches_core_floor": "按计划卖出比例测算会打穿 A 类核心仓底仓保护。",
    }
)
FINAL_ACTION_LABELS = {
    "add": "加仓",
    "buy": "买入",
    "wait": "等待",
    "review": "复核",
    "blocked": "风险复核",
    "可小仓分批": "可小仓分批",
    "可正常分批": "可正常分批",
    "只观察": "只观察",
    "等回踩": "等回踩",
    "禁止追高": "追高风险",
    "待复核，暂不新增": "待复核",
    "unknown": "未标记",
}
LANE_LABELS = {
    "actionable": "可执行",
    "blocked": "追高风险",
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
    symbols = store.list_symbols()
    entries = _load_entries(store, symbols)
    real_entries = _executed_trade_entries(entries)
    historical_entries = _historical_non_trade_entries(entries, real_entries)
    performance_summary = _load_trade_performance_summary(store)
    _render_summary(real_entries, performance_summary, legacy_count=len(historical_entries))

    sell_tab, calendar_tab, entries_tab, stats_tab, history_tab = st.tabs(["卖出 / 减仓", "交易日历", "真实交易流水", "战绩统计", "历史非成交记录"])
    with sell_tab:
        _render_editor(store)
    with calendar_tab:
        _render_trade_activity_calendar(real_entries)
    with entries_tab:
        _render_entry_delete_confirmation(store)
        _render_entry_detail(store)
        _render_entries(symbols, real_entries)
    with stats_tab:
        _render_trade_performance_stats(store, symbols)
        with st.expander("卖飞复盘", expanded=False):
            _render_sell_fly_review()
        with st.expander("系统信号复盘", expanded=False):
            _render_signal_replay(decision_store, outcome_store, error_tag_store)
    with history_tab:
        _render_historical_non_trade_records(historical_entries)


def _render_trade_activity_calendar(entries: list[dict]) -> None:
    st.markdown("### 交易频率日历")
    if not entries:
        st.info("暂无真实交易记录，交易日历会在有成交流水后显示。")
        calendar = build_monthly_trade_calendar(_hkt_now().year, _hkt_now().month, [])
        st.markdown(_trade_activity_calendar_html(calendar), unsafe_allow_html=True)
        return
    default_month = _latest_trade_month(entries)
    year_month = st.session_state.get("trade_activity_calendar_month") or default_month
    year, month = (int(part) for part in str(year_month).split("-", 1))
    nav_cols = st.columns([1, 2, 1])
    if nav_cols[0].button("上个月", key="trade-activity-prev-month", width="stretch"):
        st.session_state["trade_activity_calendar_month"] = _shift_month(year, month, -1)
        st.rerun()
    nav_cols[1].markdown(f"<div class='trade-activity-month-title'>{year} 年 {month:02d} 月</div>", unsafe_allow_html=True)
    if nav_cols[2].button("下个月", key="trade-activity-next-month", width="stretch"):
        st.session_state["trade_activity_calendar_month"] = _shift_month(year, month, 1)
        st.rerun()
    calendar = build_monthly_trade_calendar(year, month, entries)
    summary = calendar["summary"]
    metrics = st.columns(5)
    metrics[0].metric("本月交易日数", int(summary["trade_day_count"]))
    metrics[1].metric("本月交易决策数", int(summary["monthly_trade_decision_count"]))
    metrics[2].metric("高频交易日数", int(summary["high_frequency_day_count"]))
    metrics[3].metric("最大单日决策数", int(summary["max_daily_decision_count"]))
    metrics[4].metric("平均每日决策数", summary["avg_daily_decision_count"])
    st.markdown(_trade_activity_calendar_html(calendar), unsafe_allow_html=True)
    selected_date = _selected_trade_activity_date(calendar)
    _render_trade_activity_day_detail(build_daily_trade_activity(selected_date, entries))


def _trade_activity_calendar_html(calendar: dict) -> str:
    days = {str(day["date_hkt"]): day for day in calendar.get("days", [])}
    year = int(calendar.get("year") or 1970)
    month = int(calendar.get("month") or 1)
    weeks = __import__("calendar").monthcalendar(year, month)
    weekday_header = "".join(f"<th>{label}</th>" for label in ["一", "二", "三", "四", "五", "六", "日"])
    rows = []
    for week in weeks:
        cells = []
        for day_number in week:
            if not day_number:
                cells.append('<td class="trade-activity-day empty"></td>')
                continue
            key = f"{year:04d}-{month:02d}-{day_number:02d}"
            day = days.get(key, {})
            level = str(day.get("advisory_level") or "LOW").lower()
            decision_count = int(day.get("trade_decision_count") or 0)
            record_count = int(day.get("trade_record_count") or 0)
            buy_count = int(day.get("buy_count") or 0)
            sell_count = int(day.get("sell_count") or 0)
            href = f"?page=trade-journal&tradeActivityDate={key}#trade-activity-detail"
            cells.append(
                '<td class="trade-activity-day">'
                f'<a class="trade-activity-cell level-{escape(level)}" href="{escape(href, quote=True)}" target="_self">'
                f"<b>{day_number}</b>"
                f"<span>{decision_count} 决策 / {record_count} 记录</span>"
                f"<em>买 {buy_count}｜卖 {sell_count}</em>"
                f"<strong>{escape(str(day.get('advisory_level') or 'LOW'))}</strong>"
                "</a></td>"
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<div class="trade-activity-calendar-wrap">'
        '<table class="trade-activity-calendar">'
        f"<thead><tr>{weekday_header}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></div>"
    )


def _render_trade_activity_day_detail(activity: dict) -> None:
    st.markdown('<div id="trade-activity-detail"></div>', unsafe_allow_html=True)
    st.markdown(f"#### {activity['date_hkt']} 当天详情")
    cols = st.columns(8)
    cols[0].metric("成交记录", int(activity["trade_record_count"]))
    cols[1].metric("交易决策", int(activity["trade_decision_count"]))
    cols[2].metric("涉及股票", int(activity["unique_ticker_count"]))
    cols[3].metric("买入", int(activity["buy_count"]))
    cols[4].metric("卖出", int(activity["sell_count"]))
    cols[5].metric("清仓", int(activity["liquidation_count"]))
    cols[6].metric("反向交易", int(activity["reverse_trade_count"]))
    cols[7].metric("深夜交易", int(activity["late_night_trade_count"]))
    st.warning(str(activity["advisory_text"])) if activity["advisory_level"] in {"HIGH", "CRITICAL"} else st.info(str(activity["advisory_text"]))
    if activity.get("advisory_reasons"):
        st.caption(" / ".join(str(item) for item in activity["advisory_reasons"]))
    st.markdown(_trade_activity_day_table_html(activity.get("trades") or []), unsafe_allow_html=True)


def _trade_activity_day_table_html(trades: list[dict]) -> str:
    if not trades:
        return '<div class="trade-journal-empty"><strong>当天无交易</strong><span>没有需要复盘的操作频率。</span></div>'
    headers = ["时间", "ticker", "side", "quantity", "price", "notional", "reason", "advisory", "note"]
    rows = []
    for trade in trades:
        side = "buy" if str(trade.get("action_type") or "").lower() in {"buy", "add"} else "sell"
        quantity = _number(trade.get("quantity")) or 0
        price = _number(trade.get("price")) or 0
        notional = quantity * price
        rows.append(
            "<tr>"
            f"<td>{escape(_activity_time_text(trade))}</td>"
            f"<td class='symbol'>{escape(str(trade.get('symbol') or ''))}</td>"
            f"<td>{escape(side)}</td>"
            f"<td>{_quantity_text(quantity)}</td>"
            f"<td>{format_currency(price)}</td>"
            f"<td>{format_currency(notional)}</td>"
            f"<td>{escape(_sell_reason_text(trade.get('sell_reason_type')))}</td>"
            f"<td>{escape(str(trade.get('daily_trade_advisory_level') or trade.get('advisory_level') or trade.get('sell_warning_level') or ''))}</td>"
            f"<td>{escape(str(trade.get('notes') or ''))}</td>"
            "</tr>"
        )
    return (
        '<div class="trade-journal-table-wrap trade-terminal-table-wrap">'
        '<table class="trade-journal-table trade-terminal-table">'
        f"<thead><tr>{''.join(f'<th>{escape(label)}</th>' for label in headers)}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _selected_trade_activity_date(calendar: dict) -> str:
    query_date = str(st.query_params.get("tradeActivityDate") or "").strip()
    days = [str(day["date_hkt"]) for day in calendar.get("days", [])]
    if query_date in days:
        return query_date
    trade_days = [str(day["date_hkt"]) for day in calendar.get("days", []) if int(day.get("trade_record_count") or 0) > 0]
    return trade_days[-1] if trade_days else days[-1]


def _latest_trade_month(entries: list[dict]) -> str:
    latest = max(str(entry.get("trade_date") or "")[:10] for entry in entries if str(entry.get("trade_date") or "").strip())
    return latest[:7]


def _shift_month(year: int, month: int, delta: int) -> str:
    total = year * 12 + (month - 1) + delta
    new_year, zero_month = divmod(total, 12)
    return f"{new_year:04d}-{zero_month + 1:02d}"


def _activity_time_text(entry: dict) -> str:
    text = str(entry.get("created_at") or entry.get("trade_date") or "")
    if "T" in text:
        return text.split("T", 1)[1][:5]
    if " " in text:
        return text.split(" ", 1)[1][:5]
    return "日内"


def _hkt_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _pending_daily_trade_activity(
    store: TradeJournalStore,
    *,
    symbol: str,
    action_type: str,
    trade_date: str,
    quantity: object,
    price: object,
    editing_entry: dict | None,
    decision_mood: str,
) -> dict:
    entries = store.list_entries()
    if editing_entry:
        edit_id = int(editing_entry.get("id") or 0)
        entries = [entry for entry in entries if int(entry.get("id") or 0) != edit_id]
    pending = {
        "symbol": symbol,
        "action_type": action_type,
        "trade_date": trade_date,
        "quantity": quantity,
        "price": price,
        "decision_mood": decision_mood,
        "created_at": _hkt_now().isoformat(),
    }
    return build_daily_trade_activity(trade_date, [*entries, pending])


def _render_daily_trade_activity_advisory(activity: dict, *, key_suffix: str) -> bool:
    level = str(activity.get("advisory_level") or "LOW").upper()
    if level in {"HIGH", "CRITICAL"}:
        st.warning(
            f"今日交易较多：已记录 {int(activity.get('trade_record_count') or 0)} 笔，"
            "建议确认是否属于计划内操作。"
        )
    return False


def _render_editor(store: TradeJournalStore) -> None:
    _render_pending_sell_intent_dialog(store)
    editing_id = _query_int("editTrade")
    editing_entry = store.get_entry(editing_id) if editing_id is not None else None
    if editing_id is not None and editing_entry is None:
        _clear_trade_edit_query()
        st.session_state["trade_journal_notice"] = ("error", "交易记录不存在或已删除。")
        st.rerun()
    editor_open = bool(st.session_state.get("trade_journal_editor_open", False)) or editing_entry is not None
    title = "编辑交易记录" if editing_entry else "卖出 / 减仓记录"
    st.markdown('<div id="trade-journal-editor"></div>', unsafe_allow_html=True)
    with st.expander(title, expanded=editor_open):
        st.session_state["trade_journal_editor_open"] = False
        active_positions = _active_sell_positions()
        if editing_entry is None:
            st.caption("买入/加仓请前往组合持仓页操作；这里只记录减仓、清仓，并显示卖出风险提示。")
            _render_macro_regime_sell_hint()
            if not active_positions:
                st.info("当前没有可卖出的 active 持仓。")
                return
        st.markdown("### 成交信息")
        top_cols = st.columns([1.1, 1.2, 1])
        selected_position = None
        if editing_entry is None:
            position_options = [str(row.get("symbol") or "").strip().upper() for row in active_positions]
            label_by_symbol = {str(row.get("symbol") or "").strip().upper(): _sell_position_option_label(row) for row in active_positions}
            symbol = top_cols[0].selectbox(
                "持仓",
                position_options,
                format_func=lambda value: label_by_symbol.get(str(value), str(value)),
                key=_editor_key("symbol", editing_id),
            ).strip().upper()
            selected_position = next((row for row in active_positions if str(row.get("symbol") or "").strip().upper() == symbol), None)
            action_options = list(SELL_ENTRY_ACTION_OPTIONS)
            action_label = top_cols[1].selectbox("操作类型", action_options, key=_editor_key("action-stock", editing_id))
        else:
            symbol = str((editing_entry or {}).get("symbol") or "").strip().upper()
            top_cols[0].text_input("股票代码", value=symbol, disabled=True, key=_editor_key("symbol", editing_id))
            action_default = _action_label_for_entry(editing_entry)
            top_cols[1].text_input("操作类型", value=action_default, disabled=True, key=_editor_key("action-stock", editing_id))
            action_label = action_default
        trade_date = top_cols[2].date_input("日期", value=_entry_date(editing_entry), key=_editor_key("date", editing_id))
        action_type = SELL_ENTRY_ACTION_OPTIONS.get(action_label, ACTION_OPTIONS.get(action_label, "trim"))

        stock_plan = _stock_plan_with_position_tier(_load_stock_discipline_profile(symbol), selected_position)

        if editing_entry is None and selected_position:
            _render_sell_quantity_shortcuts(selected_position, key_suffix=str(editing_id or "new"))
        trade_cols = st.columns([1, 1])
        quantity_default = _entry_number_text(editing_entry, "quantity")
        if editing_entry is None and action_type == "sell":
            selected_quantity = _number((selected_position or {}).get("quantity"))
            quantity_default = "" if selected_quantity is None else f"{selected_quantity:g}"
        quantity = trade_cols[0].text_input("卖出数量", value=quantity_default, key=_editor_key("quantity", editing_id))
        price = trade_cols[1].text_input("卖出均价", value=_entry_number_text(editing_entry, "price"), key=_editor_key("price", editing_id))
        decision_mood = str((editing_entry or {}).get("decision_mood") or "NEUTRAL").strip()
        notes = st.text_area(
            "成交备注（可选）",
            value=_entry_value(editing_entry, "notes"),
            height=72,
            key=_editor_key("notes", editing_id),
            placeholder="只记录执行层信息，例如分批成交、夜盘流动性差、实际成交偏差。",
        )
        with st.expander("复盘信息，可选", expanded=False):
            decision_snapshot_id = st.text_input(
                "关联信号 ID（可选）",
                value=_entry_int_text(editing_entry, "decision_snapshot_id"),
                key=_editor_key("snapshot-id", editing_id),
            )
        sell_reference_context = _sell_reference_context(symbol, selected_position) if selected_position else {}

        portfolio_preview = _portfolio_sync_preview(symbol, action_type, quantity, price)
        effective_sell_quantity = _effective_sell_available_quantity(
            action_type,
            portfolio_preview.get("currentQuantity"),
            editing_entry=editing_entry,
            symbol=symbol,
        )
        portfolio_preview = _sell_edit_portfolio_preview(
            portfolio_preview,
            action_type,
            quantity,
            effective_sell_quantity,
            editing_entry=editing_entry,
            symbol=symbol,
        )
        discipline_result = None
        radar_gate_result = None
        after_quantity = _number(portfolio_preview.get("afterQuantity"))
        st.markdown("### 系统摘要")
        if editing_entry is None and selected_position:
            _render_sell_reference_card(symbol, selected_position, context=sell_reference_context, after_quantity=after_quantity)

        if action_type in CLASSIFICATION_ACTIONS:
            radar_gate_result = _render_radar_buy_gate(
                symbol,
                action_type,
                decision_mood=decision_mood,
                portfolio_preview=portfolio_preview,
                buy_reason=notes,
                key_suffix=str(editing_id or "new"),
            )
            _render_buy_classification_editor(symbol, editing_entry=editing_entry, stock_plan=stock_plan, key_suffix=str(editing_id or "new"))

        st.markdown("### 提交按钮")
        submit_qty = _number(quantity)
        submit_price = _number(price)
        daily_activity = _pending_daily_trade_activity(
            store,
            symbol=symbol,
            action_type=action_type,
            trade_date=trade_date.isoformat(),
            quantity=quantity,
            price=price,
            editing_entry=editing_entry,
            decision_mood=decision_mood,
        )
        user_confirmed_daily_advisory = _render_daily_trade_activity_advisory(
            daily_activity,
            key_suffix=str(editing_id or "new"),
        )

        button_label = "保存修改" if editing_entry else ("确认卖出并入账" if action_type == "sell" else "确认减仓并入账")
        if st.button(button_label, key=_editor_key("save", editing_id), width="stretch"):
            identity_error = _sell_edit_identity_error(editing_entry, symbol, action_type)
            if identity_error:
                st.session_state["trade_journal_notice"] = ("error", identity_error)
                st.rerun()
            quantity_error = _sell_quantity_validation_error(
                action_type,
                quantity,
                portfolio_preview.get("currentQuantity"),
                editing_entry=editing_entry,
                symbol=symbol,
            )
            if quantity_error:
                st.session_state["trade_journal_notice"] = ("error", quantity_error)
                st.rerun()
            entry_values = {
                "trade_date": trade_date.isoformat(),
                "action_type": action_type,
                "quantity": quantity,
                "price": price,
                "decision_mood": decision_mood,
                "decision_snapshot_id": decision_snapshot_id,
                "notes": notes,
                "userConfirmedDailyTradeAdvisory": user_confirmed_daily_advisory,
            }
            if selected_position:
                entry_values["targetSellPrice"] = selected_position.get("plannedSellPrice")
                entry_values.update(_pre_trade_snapshot_values(selected_position))
            entry_values.update(buy_gate_entry_fields(radar_gate_result if action_type in CLASSIFICATION_ACTIONS else None, action_type=action_type))
            entry_values.update(_trade_discipline_form_values(action_type, key_suffix=str(editing_id or "new")))
            entry_values.update(_buy_classification_form_values(action_type, key_suffix=str(editing_id or "new")))
            structured_sell_error = _structured_sell_reason_validation_error(action_type, entry_values)
            if structured_sell_error:
                st.session_state["trade_journal_notice"] = ("error", structured_sell_error)
                st.rerun()
            if selected_position and action_type in SELL_DISCIPLINE_ACTIONS:
                entry_values.update(
                    _sell_context_snapshot_values(
                        symbol=symbol,
                        action_type=action_type,
                        trade_date=trade_date.isoformat(),
                        entry_values=entry_values,
                        position_row=selected_position,
                        sell_reference=sell_reference_context,
                    )
                )
            if editing_entry:
                _update_entry(store, int(editing_id or 0), symbol, entry_values)
            else:
                _queue_sell_intent(symbol, action_type, entry_values)
        if editing_entry and st.button("取消编辑", key=_editor_key("cancel", editing_id), width="stretch"):
            _clear_trade_edit_query()
            st.rerun()


def _render_radar_buy_gate(
    symbol: str,
    action_type: str,
    *,
    decision_mood: str,
    portfolio_preview: dict,
    buy_reason: str,
    key_suffix: str,
):
    if action_type not in CLASSIFICATION_ACTIONS:
        return None
    st.markdown('<div class="trade-discipline-title">买入前 Radar 提示</div>', unsafe_allow_html=True)
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        st.markdown(
            """
            <section class="trade-radar-gate warning">
              <div class="trade-radar-gate-head"><b>等待股票代码</b><span>填写 ticker 后读取单票 RadarReport，不会批量刷新。</span></div>
            </section>
            """,
            unsafe_allow_html=True,
        )
        return None
    bucket_label = st.selectbox(
        "本次买入用途",
        ["交易仓", "核心仓"],
        key=f"trade-radar-position-bucket-{key_suffix}",
    )
    position_bucket = "core" if bucket_label == "核心仓" else "trade"
    report = build_cached_ai_stock_radar_report(ticker)
    result = evaluate_buy_gate(
        report,
        action_type=action_type,
        position_bucket=position_bucket,
        planned_after_position_pct=portfolio_preview.get("afterPositionPct"),
        decision_mood=decision_mood,
        observation_only=False,
        buy_reason=buy_reason,
    )
    _render_radar_buy_gate_card(report.to_dict(), result.to_dict(), bucket_label=bucket_label)
    return result


def _render_radar_buy_gate_card(report: dict, result: dict, *, bucket_label: str) -> None:
    decision = str(report.get("decision") or "DATA_MISSING")
    warning_level = str(result.get("warning_level") or result.get("warningLevel") or "").strip().lower()
    if warning_level not in {"info", "warning", "danger"}:
        warning_level = "info" if not result.get("advisory_warnings") else "warning"
    tone = warning_level
    status = {"info": "正常提醒", "warning": "建议复核", "danger": "高风险提醒"}.get(warning_level, "建议复核")
    reasons = [
        str(item)
        for item in (
            result.get("advisory_warnings")
            or result.get("radarAdvisoryWarnings")
            or result.get("reasons")
            or report.get("block_reasons")
            or []
        )
        if str(item).strip()
    ]
    required = [str(item) for item in (result.get("required_actions") or []) if str(item).strip()]
    reason_html = "".join(f"<li>{escape(item)}</li>" for item in reasons) or "<li>暂无额外风险提示</li>"
    required_html = "".join(f"<li>{escape(item)}</li>" for item in required)
    if required_html:
        required_html = f"<div class=\"trade-radar-requirements\"><b>可选补充</b><ul>{required_html}</ul></div>"
    stale_text = "价格数据可能过期" if bool(report.get("is_stale")) else "价格数据正常"
    rows = [
        ("当前结论", decision),
        ("当前价格", _money_text(report.get("current_price"))),
        ("击球区", _radar_zone_text(report.get("buy_zone"))),
        ("观察区", _radar_zone_text(report.get("watch_zone"))),
        ("追高区", _radar_zone_text(report.get("chase_zone"))),
        ("综合评分", _score_text(report.get("final_score"))),
        ("估值评分", _score_text(report.get("valuation_score"))),
        ("核心仓上限", _pct_value_text(report.get("core_max_pct"))),
        ("交易仓上限", _pct_value_text(report.get("trade_max_pct"))),
        ("系统参考", _pct_value_text(report.get("allowed_add_pct"))),
        ("本次用途", bucket_label),
        ("数据状态", f"{report.get('data_status') or 'MISSING'} / {stale_text}"),
    ]
    metrics_html = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(str(value))}</strong></div>"
        for label, value in rows
    )
    st.markdown(
        f"""
        <section class="trade-radar-gate {escape(tone)}">
          <div class="trade-radar-gate-head">
            <b>Radar 提示：{escape(status)}</b>
            <span>价格提醒不是买入信号；你可以手动继续，偏离建议会记录为人工 override。</span>
          </div>
          <div class="trade-radar-gate-grid">{metrics_html}</div>
          <div class="trade-radar-reasons"><b>提示原因</b><ul>{reason_html}</ul></div>
          {required_html}
        </section>
        """,
        unsafe_allow_html=True,
    )


def _radar_zone_text(zone: object) -> str:
    text = format_buy_zone(zone)
    return BLANK_TEXT if text == "N/A" else text


def _score_text(value: object) -> str:
    number = _number(value)
    return BLANK_TEXT if number is None else f"{number:.0f}"


def _pct_value_text(value: object) -> str:
    number = _number(value)
    return BLANK_TEXT if number is None else f"{number:.1f}%"


def _active_sell_positions() -> list[dict]:
    try:
        rows = list((build_portfolio_view_model().get("rows") or []))
        raw_positions = {
            str(row.get("symbol") or "").strip().upper(): row
            for row in PortfolioPositionStore().list_active_positions()
        }
    except Exception:
        return []
    active_rows: list[dict] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol or (_number(row.get("quantity")) or 0) <= 0:
            continue
        raw = raw_positions.get(symbol) or {}
        enriched = dict(row)
        enriched["createdAt"] = raw.get("created_at") or row.get("createdAt")
        active_rows.append(enriched)
    return active_rows


def _sell_position_option_label(row: dict) -> str:
    symbol = str(row.get("symbol") or "").strip().upper()
    quantity = _quantity_text(row.get("quantity"))
    average_cost = _money_text(row.get("averageCost"))
    tier = str(row.get("positionTier") or "").strip().upper()
    tier_text = f"{tier}类" if tier in {"A", "B", "C"} else "需设置等级"
    target = _money_text(row.get("plannedSellPrice"))
    return f"{symbol}｜持有 {quantity}｜均价 {average_cost}｜{tier_text}｜目标卖出价 {target}"


def _render_sell_quantity_shortcuts(position_row: dict, *, key_suffix: str) -> None:
    current_quantity = _number(position_row.get("quantity"))
    if current_quantity is None or current_quantity <= 0:
        return
    st.caption("快捷数量会写入卖出数量；卖出比例仍由系统按当前持仓自动计算。")
    cols = st.columns(4)
    for index, (label, ratio) in enumerate((("10%", 0.10), ("25%", 0.25), ("50%", 0.50), ("100% 清仓", 1.00))):
        quantity = current_quantity if ratio >= 1 else current_quantity * ratio
        if cols[index].button(label, key=f"trade-sell-qty-shortcut-{key_suffix}-{int(ratio * 100)}", width="stretch"):
            st.session_state[_editor_key("quantity", None)] = f"{quantity:g}"


def _stock_plan_with_position_tier(stock_plan: dict, position_row: dict | None) -> dict:
    if not position_row:
        return stock_plan
    tier = str(position_row.get("positionTier") or "").strip().upper()
    if tier not in {"A", "B", "C"}:
        return stock_plan
    core, trading = POSITION_CLASS_DEFAULTS.get(tier, (None, None))
    merged = dict(stock_plan or {})
    merged["position_class"] = tier
    if core is not None:
        merged["core_position_min_pct"] = core
    if trading is not None:
        merged["trading_position_max_pct"] = trading
    return merged


def _pre_trade_snapshot_values(position_row: dict) -> dict:
    quantity = _number(position_row.get("quantity"))
    avg_cost = _number(position_row.get("averageCost"))
    total_cost = _number(position_row.get("costBasis"))
    if total_cost is None and quantity is not None and avg_cost is not None:
        total_cost = quantity * avg_cost
    tier = str(position_row.get("positionTier") or "").strip().upper()
    role = position_row.get("holdingRole") or position_row.get("role") or ROLE_OBSERVATION
    return {
        "preTradeQuantity": quantity,
        "preTradeAvgCost": avg_cost,
        "preTradeTotalCost": total_cost,
        "preTradePositionTier": tier if tier in {"A", "B", "C"} else "",
        "tradeRole": role,
        "roleLabel": portfolio_role_label(role),
        "roleTargetWeight": portfolio_role_target_weight(role),
        "coreTacticalSplit": portfolio_role_core_tactical_split(role),
        "preTradeTargetSellPrice": _number(position_row.get("plannedSellPrice")),
        "preTradeUnrealizedPnl": _number(position_row.get("unrealizedPnl")),
        "costBasisSource": "position_snapshot" if avg_cost is not None else "",
    }


def _sell_reference_context(symbol: str, position_row: dict) -> dict:
    try:
        report = build_cached_ai_stock_radar_report(symbol).to_dict()
    except Exception:
        report = {}
    current_price = _first_number(position_row.get("currentPrice"), report.get("current_price"))
    average_cost = _number(position_row.get("averageCost"))
    unrealized_pnl = _number(position_row.get("unrealizedPnl"))
    unrealized_pnl_pct = _number(position_row.get("unrealizedPnlPct"))
    target_sell = _number(position_row.get("plannedSellPrice"))
    distance_to_target = None
    if current_price is not None and target_sell is not None and target_sell > 0:
        distance_to_target = (current_price - target_sell) / target_sell * 100
    zone_status = str(report.get("price_position") or report.get("zone_status") or "ZONE_MISSING")
    data_status = str(report.get("data_status") or "missing")
    buy_zone_text = _radar_zone_text(report.get("buy_zone"))
    holding_days = _holding_days(position_row.get("createdAt") or position_row.get("created_at"))
    position_tier = str(position_row.get("positionTier") or position_row.get("position_tier") or "").strip().upper()
    missing_fields = []
    if not report:
        missing_fields.append("radar_report")
    if not report.get("buy_zone"):
        missing_fields.append("buy_zone")
    if zone_status == "ZONE_MISSING":
        missing_fields.append("zone_status")
    if current_price is None:
        missing_fields.append("current_price")
    return {
        "currentPrice": current_price,
        "averageCost": average_cost,
        "unrealizedPnl": unrealized_pnl,
        "unrealizedPnlPct": unrealized_pnl_pct,
        "holdingDays": holding_days,
        "positionTier": position_tier if position_tier in {"A", "B", "C"} else "",
        "targetSellPrice": target_sell,
        "distanceToTarget": distance_to_target,
        "zoneStatus": zone_status,
        "pricePosition": zone_status,
        "buyZone": report.get("buy_zone") if isinstance(report.get("buy_zone"), dict) else None,
        "buyZoneText": buy_zone_text,
        "radarDecision": report.get("decision"),
        "dataStatus": data_status,
        "isStale": bool(report.get("is_stale")),
        "missingSnapshotFields": missing_fields,
        "belowTargetSellPrice": bool(current_price is not None and target_sell is not None and target_sell > 0 and current_price < target_sell),
        "inBuyZoneOrBelow": zone_status.strip().upper() in {"IN_BUY_ZONE", "BELOW_BUY_ZONE"},
    }


def _sell_context_snapshot_values(
    *,
    symbol: str,
    action_type: str,
    trade_date: str,
    entry_values: dict,
    position_row: dict,
    sell_reference: dict,
) -> dict:
    quantity_before = _number(entry_values.get("preTradeQuantity"))
    sell_quantity = _number(entry_values.get("quantity"))
    sell_price = _number(entry_values.get("price"))
    target_sell = _first_number(entry_values.get("preTradeTargetSellPrice"), sell_reference.get("targetSellPrice"))
    holding_days = _number(sell_reference.get("holdingDays"))
    sell_pct = sell_quantity / quantity_before if quantity_before and sell_quantity is not None else None
    missing_fields = list(sell_reference.get("missingSnapshotFields") or [])
    field_checks = {
        "quantity_before": quantity_before,
        "average_cost": entry_values.get("preTradeAvgCost"),
        "position_tier": entry_values.get("preTradePositionTier"),
        "target_sell_price": target_sell,
        "sell_quantity": sell_quantity,
        "sell_price": sell_price,
        "holding_days_reference": holding_days,
    }
    for field, value in field_checks.items():
        if value in (None, "") and field not in missing_fields:
            missing_fields.append(field)
    snapshot = {
        "ticker": str(symbol or "").strip().upper(),
        "quantity_before": quantity_before,
        "average_cost": _number(entry_values.get("preTradeAvgCost")),
        "total_cost": _number(entry_values.get("preTradeTotalCost")),
        "position_tier": entry_values.get("preTradePositionTier"),
        "position_class": entry_values.get("positionClass"),
        "position_created_at": position_row.get("createdAt") or position_row.get("created_at"),
        "holding_days_reference": holding_days,
        "target_sell_price": target_sell,
        "action": action_type,
        "sell_quantity": sell_quantity,
        "sell_price": sell_price,
        "sell_pct": sell_pct,
        "sell_reason": entry_values.get("sellReasonType"),
        "sell_context_type": entry_values.get("sellContextType"),
        "sell_reason_tags": entry_values.get("sellReasonTags") or [],
        "fundamental_change_type": entry_values.get("fundamentalChangeType") or [],
        "valuation_compression_reason": entry_values.get("valuationCompressionReason"),
        "liquidity_shock_reason": entry_values.get("liquidityShockReason"),
        "position_risk_reason": entry_values.get("positionRiskReason"),
        "sell_thesis_note": entry_values.get("sellThesisNote"),
        "mood": entry_values.get("decision_mood"),
        "replenishment_plan": entry_values.get("reentryPlanText"),
        "created_at": entry_values.get("created_at"),
        "trade_date": trade_date,
        "radar_decision": sell_reference.get("radarDecision"),
        "buy_zone": sell_reference.get("buyZone"),
        "zone_status": sell_reference.get("zoneStatus"),
        "price_position": sell_reference.get("pricePosition"),
        "current_price": sell_reference.get("currentPrice"),
        "distance_to_target_sell_price": sell_reference.get("distanceToTarget"),
        "data_status": sell_reference.get("dataStatus"),
        "is_stale": bool(sell_reference.get("isStale")),
        "below_target_at_sell": bool(
            sell_price is not None and target_sell is not None and target_sell > 0 and sell_price < target_sell
        ),
        "in_or_below_buy_zone_at_sell": bool(sell_reference.get("inBuyZoneOrBelow")),
        "missing_snapshot_fields": sorted(set(str(item) for item in missing_fields if str(item).strip())),
        "snapshot_created_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"sellContextSnapshot": snapshot}


def _render_sell_reference_card(symbol: str, position_row: dict, *, context: dict | None = None, after_quantity: object = None) -> None:
    context = context or _sell_reference_context(symbol, position_row)
    current_price = _number(context.get("currentPrice"))
    average_cost = _number(context.get("averageCost"))
    unrealized_pnl = _number(context.get("unrealizedPnl"))
    unrealized_pnl_pct = _number(context.get("unrealizedPnlPct"))
    holding_days = _number(context.get("holdingDays"))
    target_sell = _number(context.get("targetSellPrice"))
    distance_to_target = _number(context.get("distanceToTarget"))
    zone_status = str(context.get("zoneStatus") or "ZONE_MISSING")
    buy_zone_text = str(context.get("buyZoneText") or BLANK_TEXT)
    reference_rows = [
        ("当前价", _money_text(current_price)),
        ("成本价", _money_text(average_cost)),
        ("浮盈亏", _pnl_text(unrealized_pnl, unrealized_pnl_pct)),
        ("持仓天数", _position_holding_days_text(holding_days)),
        ("目标卖出价", _money_text(target_sell)),
        ("距目标", _percent_or_dash(distance_to_target)),
        ("卖出后剩余持仓", _quantity_text(after_quantity)),
        ("回补参考", buy_zone_text),
    ]
    hint = _sell_reference_hint(zone_status, current_price, target_sell)
    metrics_html = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in reference_rows
    )
    st.markdown(
        f"""
        <section class="trade-sell-reference-card">
          <div><b>系统摘要</b><span>{escape(hint)}</span></div>
          <div class="trade-portfolio-sync-grid">{metrics_html}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_macro_regime_sell_hint() -> None:
    try:
        snapshot = load_macro_regime()
    except Exception:
        return
    st.info(macro_regime_trade_hint_text(snapshot, context="sell"))


def _sell_reference_alerts(context: dict) -> list[str]:
    position_tier = str(context.get("positionTier") or "").strip().upper()
    if position_tier != "A":
        return []
    alerts: list[str] = []
    if context.get("belowTargetSellPrice"):
        alerts.append("A 类核心仓：当前低于目标价，卖出前先确认目标逻辑是否失效。")
    if context.get("inBuyZoneOrBelow"):
        alerts.append("A 类核心仓：仍在买区或低于买区，卖出必须证明基本面恶化、仓位超限或计划触发。")
    holding_days = _number(context.get("holdingDays"))
    if holding_days is not None and holding_days < 30:
        alerts.append("A 类核心仓：持仓天数偏短，注意 NOW 式卖飞风险。")
    alerts.append("A 类核心仓：下方必须写清具体回补计划，否则卖出纪律会提示高风险并要求复核。")
    return alerts


def _zone_status_text(value: object) -> str:
    text = format_zone_status(value)
    return BLANK_TEXT if text == "N/A" else text


def _sell_reference_hint(zone_status: str, current_price: float | None, target_sell: float | None) -> str:
    status = str(zone_status or "").strip().upper()
    if status in {"IN_BUY_ZONE", "BELOW_BUY_ZONE"}:
        return "当前并非自然卖出区；卖出需要证明基本面恶化、仓位超限或交易计划触发。"
    if current_price is not None and target_sell is not None and target_sell > 0 and current_price < target_sell:
        return "当前价低于买入时设定的卖出目标，先复核是否真的需要卖。"
    if status == "IN_CHASE_ZONE":
        return "价格进入追高区，可复核是否按计划减交易仓，核心仓仍需纪律复核。"
    return "卖出前先看目标价、买区位置和卖出后处理预案，避免临场卖飞。"


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


def _render_portfolio_ledger_preview(
    symbol: str,
    action_type: str,
    quantity: object,
    price: object,
    *,
    preview: dict | None = None,
    discipline_blocked: bool = False,
) -> None:
    if action_type not in POSITION_AFFECTING_ACTIONS:
        return
    current_preview = preview or _portfolio_sync_preview(symbol, action_type, quantity, price)
    _render_portfolio_sync_preview(current_preview, checked=True, discipline_blocked=discipline_blocked)


def _portfolio_sync_preview(symbol: str, action_type: str, quantity: object, price: object) -> dict:
    if action_type not in POSITION_AFFECTING_ACTIONS:
        return {}
    return preview_trade_values_portfolio_effect(
        symbol,
        {"action_type": action_type, "quantity": quantity, "price": price},
    )


def _discipline_result_blocked(result: object) -> bool:
    return False


def _legacy_sell_quantity_validation_error(action_type: str, quantity: object, current_quantity: object) -> str:
    if str(action_type or "").strip().lower() not in SELL_DISCIPLINE_ACTIONS:
        return ""
    sell_quantity = _number(quantity)
    current = _number(current_quantity)
    if sell_quantity is None or sell_quantity <= 0:
        return "卖出/减仓数量必须大于 0。"
    if current is None or current <= 0:
        return "只能卖出当前组合持仓里已有的股票。"
    if sell_quantity > current:
        return f"卖出数量不能超过当前持仓：当前 {_quantity_text(current)}，本次 {_quantity_text(sell_quantity)}。"
    return ""


def _sell_edit_identity_error(editing_entry: dict | None, symbol: str, action_type: str) -> str:
    if not editing_entry:
        return ""
    original_action = str(editing_entry.get("action_type") or "").strip().lower()
    if original_action not in SELL_DISCIPLINE_ACTIONS:
        return ""
    original_symbol = str(editing_entry.get("symbol") or "").strip().upper()
    next_symbol = str(symbol or "").strip().upper()
    next_action = str(action_type or "").strip().lower()
    if original_symbol and next_symbol and original_symbol != next_symbol:
        return "历史交易不支持直接修改 ticker/account，请删除后重建。"
    if next_action and original_action != next_action:
        return "历史交易不支持直接修改交易方向，请删除后重建。"
    return ""


def _is_same_sell_edit(editing_entry: dict | None, symbol: str | None, action_type: str) -> bool:
    if not editing_entry:
        return False
    original_action = str(editing_entry.get("action_type") or "").strip().lower()
    if original_action not in SELL_DISCIPLINE_ACTIONS:
        return False
    next_action = str(action_type or "").strip().lower()
    if original_action != next_action:
        return False
    original_symbol = str(editing_entry.get("symbol") or "").strip().upper()
    next_symbol = str(symbol or "").strip().upper()
    return bool(original_symbol and next_symbol and original_symbol == next_symbol)


def _effective_sell_available_quantity(
    action_type: str,
    current_quantity: object,
    *,
    editing_entry: dict | None = None,
    symbol: str | None = None,
) -> float | None:
    current = _number(current_quantity)
    if _is_same_sell_edit(editing_entry, symbol, action_type):
        original_quantity = _number((editing_entry or {}).get("quantity")) or 0.0
        return float(current or 0.0) + original_quantity
    return current


def _sell_edit_portfolio_preview(
    preview: dict,
    action_type: str,
    quantity: object,
    effective_sell_quantity: object,
    *,
    editing_entry: dict | None = None,
    symbol: str | None = None,
) -> dict:
    if not _is_same_sell_edit(editing_entry, symbol, action_type):
        return preview
    restored_quantity = _number(effective_sell_quantity)
    if restored_quantity is None:
        return preview
    result = dict(preview or {})
    result["currentQuantity"] = restored_quantity
    trade_quantity = _number(quantity)
    if trade_quantity is not None:
        result["afterQuantity"] = restored_quantity - trade_quantity
    result["status"] = "edit_preview"
    result["error"] = ""
    return result


def _sell_quantity_validation_error(
    action_type: str,
    quantity: object,
    current_quantity: object,
    *,
    editing_entry: dict | None = None,
    symbol: str | None = None,
) -> str:
    if str(action_type or "").strip().lower() not in SELL_DISCIPLINE_ACTIONS:
        return ""
    sell_quantity = _number(quantity)
    current = _effective_sell_available_quantity(
        action_type,
        current_quantity,
        editing_entry=editing_entry,
        symbol=symbol,
    )
    if sell_quantity is None or sell_quantity <= 0:
        return "卖出/减仓数量必须大于 0。"
    if current is None or current <= 0:
        return "只能卖出当前组合持仓里已有的股票。"
    if sell_quantity > current:
        if _is_same_sell_edit(editing_entry, symbol, action_type):
            return "修改后的卖出数量超过还原后可用持仓，请检查数量。"
        return f"卖出数量不能超过当前持仓：当前 {_quantity_text(current)}，本次 {_quantity_text(sell_quantity)}。"
    return ""


def _render_portfolio_sync_preview(preview: dict, *, checked: bool, discipline_blocked: bool = False) -> None:
    tone = "warning" if preview.get("status") == "failed" or discipline_blocked else "ok"
    title = "成交入账预览"
    if discipline_blocked:
        title = "纪律提示需修正"
    rows = [
        ("当前持股", _quantity_text(preview.get("currentQuantity"))),
        ("当前均价", _money_text(preview.get("currentAverageCost"))),
        ("本次股数", _quantity_text(preview.get("tradeQuantity"))),
        ("成交价格", _money_text(preview.get("tradePrice"))),
        ("入账后持股", _quantity_text(preview.get("afterQuantity"))),
        ("入账后均价", _money_text(preview.get("afterAverageCost"))),
    ]
    if preview.get("afterMarketValue") is not None:
        rows.append(("入账后市值", _money_text(preview.get("afterMarketValue"))))
    if preview.get("afterPositionPct") is not None:
        rows.append(("入账后仓位", _percent_or_dash(preview.get("afterPositionPct"))))
    content = "".join(
        f"<div><span>{escape(label)}</span><strong>{escape(value)}</strong></div>"
        for label, value in rows
    )
    error = str(preview.get("error") or "").strip()
    hint = error if error else "确认成交后会同时写入交易日志并更新组合持仓。"
    if discipline_blocked:
        hint = "当前为卖出风险提醒；系统不建议时仍可继续，最终只由数量、价格、持仓等基础校验决定是否入账。"
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
    sell_reference: dict | None = None,
    key_suffix: str = "new",
) -> None:
    position_default = _default_position_class(editing_entry, stock_plan)
    position_default_label = _position_class_label(position_default)
    reason_default = _sell_reason_label_for_entry(editing_entry)
    actual_sell_pct = _actual_sell_pct(trade_quantity, current_quantity)
    st.session_state[f"trade-discipline-actual-sell-pct-{key_suffix}"] = actual_sell_pct
    st.session_state[f"trade-discipline-current-quantity-{key_suffix}"] = _number(current_quantity)
    st.session_state[f"trade-discipline-below-target-{key_suffix}"] = bool((sell_reference or {}).get("belowTargetSellPrice"))
    st.session_state[f"trade-discipline-in-buy-zone-or-below-{key_suffix}"] = bool((sell_reference or {}).get("inBuyZoneOrBelow"))
    computed_sell_pct = "" if actual_sell_pct is None else f"{actual_sell_pct * 100:.2f}"
    st.session_state[f"trade-discipline-planned-sell-pct-{key_suffix}"] = computed_sell_pct
    planned_sell_pct = computed_sell_pct

    with st.expander("复盘信息，可选", expanded=False):
        st.caption("卖出原因、回补计划和纪律细节只用于复盘；模型提醒不阻止提交，基础数量和价格校验仍会保留。")
        cols = st.columns([0.9, 1.0, 1.35, 0.9, 0.9], gap="small")
        position_label = cols[0].selectbox(
            "股票分类",
            list(POSITION_CLASS_OPTIONS),
            index=list(POSITION_CLASS_OPTIONS).index(position_default_label),
            key=f"trade-discipline-position-class-{key_suffix}",
        )
        cols[1].metric("卖出比例", _pct_point_text(actual_sell_pct))
        reason_label = cols[2].selectbox(
            "卖出原因（可选）",
            list(SELL_REASON_OPTIONS),
            index=list(SELL_REASON_OPTIONS).index(reason_default),
            key=f"trade-discipline-sell-reason-{key_suffix}",
        )
        thesis_broken = cols[3].checkbox("投资逻辑破裂", value=_entry_bool(editing_entry, "thesis_broken"), key=f"trade-discipline-thesis-broken-{key_suffix}")
        position_over_limit = cols[4].checkbox("仓位超限", value=_entry_bool(editing_entry, "position_over_limit"), key=f"trade-discipline-position-over-limit-{key_suffix}")
        position_class = POSITION_CLASS_OPTIONS.get(position_label, "")
        core_pct, trading_pct = _classification_ratio_defaults(position_class, editing_entry, stock_plan)
        st.session_state[f"trade-discipline-core-min-{key_suffix}"] = core_pct
        st.session_state[f"trade-discipline-trading-max-{key_suffix}"] = trading_pct
        _render_structured_sell_reason_editor(
            position_class=position_class,
            editing_entry=editing_entry,
            key_suffix=key_suffix,
        )
        st.markdown("#### 回补计划（可选）")
        reentry_values = _render_reentry_plan_editor(
            symbol,
            trade_price=trade_price,
            sell_reason_type=SELL_REASON_OPTIONS[reason_label],
            decision_mood=decision_mood,
            editing_entry=editing_entry,
            key_suffix=key_suffix,
        )
    position_label = st.session_state.get(f"trade-discipline-position-class-{key_suffix}", position_default_label)
    reason_label = st.session_state.get(f"trade-discipline-sell-reason-{key_suffix}", reason_default)
    position_class = POSITION_CLASS_OPTIONS.get(str(position_label or ""), "")
    thesis_broken = bool(st.session_state.get(f"trade-discipline-thesis-broken-{key_suffix}"))
    position_over_limit = bool(st.session_state.get(f"trade-discipline-position-over-limit-{key_suffix}"))
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
    st.session_state[f"trade-discipline-hard-block-{key_suffix}"] = False
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
        belowTargetSellPrice=bool((sell_reference or {}).get("belowTargetSellPrice")),
        inBuyZoneOrBelow=bool((sell_reference or {}).get("inBuyZoneOrBelow")),
    )
    _render_discipline_summary_row(
        result,
        action_type=action_type,
        sell_reason_label=reason_label,
        actual_sell_pct=actual_sell_pct,
        has_reentry_plan=has_reentry_plan,
    )
    if _discipline_gate_conclusion(result) != "PASS" and not _combined_sell_reason_note(editing_entry):
        st.caption("建议填写卖出原因，便于复盘；不填写也可以继续提交。")
    with st.expander("纪律检查详情", expanded=False):
        _render_discipline_gate_explanation(result, discipline_context)
    return result


def _render_discipline_summary_row(
    result,
    *,
    action_type: str,
    sell_reason_label: str,
    actual_sell_pct: float | None,
    has_reentry_plan: bool,
) -> None:
    conclusion = _discipline_gate_conclusion(result)
    status_text = _discipline_gate_conclusion_label(conclusion)
    action_text = "清仓" if str(action_type or "") == "sell" else "减仓"
    summary_cols = st.columns([1.1, 1, 1, 1], gap="small")
    summary_cols[0].metric("纪律检查", status_text)
    summary_cols[1].metric("卖出类型", action_text)
    summary_cols[2].metric("本次比例", _pct_point_text(actual_sell_pct))
    summary_cols[3].metric("需要回补计划", "是" if has_reentry_plan else "否")
    if conclusion != "PASS":
        st.caption("纪律检查有提醒，完整风险提示和比例明细已折叠到下方。")


def _render_structured_sell_reason_editor(
    *,
    position_class: str,
    editing_entry: dict | None = None,
    key_suffix: str = "new",
) -> None:
    context_default = _sell_context_type_label_for_entry(editing_entry)
    context_label = st.selectbox(
        "卖出原因类型",
        list(SELL_CONTEXT_TYPE_OPTIONS),
        index=list(SELL_CONTEXT_TYPE_OPTIONS).index(context_default),
        key=f"trade-sell-context-type-{key_suffix}",
        help="只用于记录和复盘，不改变卖出风险提示；真实成交入账仍由用户确认。",
    )
    context_type = SELL_CONTEXT_TYPE_OPTIONS.get(context_label, "")
    if context_type == "fundamental_change":
        selected_changes = st.multiselect(
            "基本面改写类型",
            list(FUNDAMENTAL_CHANGE_OPTIONS),
            default=_fundamental_change_labels_for_entry(editing_entry),
            key=f"trade-fundamental-change-type-{key_suffix}",
        )
        if not selected_changes:
            st.warning("选择“基本面改写”时，请至少选择一项具体改写类型；本提示只用于复盘，不改变风险提示。")
    st.multiselect(
        "原因标签（多选）",
        list(SELL_REASON_TAG_OPTIONS),
        default=_sell_reason_tag_labels_for_entry(editing_entry),
        key=f"trade-sell-reason-tags-{key_suffix}",
        help="用于筛选和复盘，可同时标记估值、风险、仓位、市场环境或 thesis 变化。",
    )
    st.text_area(
        "补充说明（可选）",
        value=_combined_sell_reason_note(editing_entry),
        height=96,
        key=f"trade-sell-thesis-note-{key_suffix}",
        placeholder="可记录本次卖出的核心原因，例如止盈、止损、换仓、风险控制、thesis 变化或回补判断。",
    )
    if str(position_class or "").upper() == "A" and context_type in {"valuation_compression", "liquidity_shock"}:
        st.warning("这可能是在流动性较差或风险溢价上升时卖出核心资产。请确认不是恐慌卖出，并填写回补计划。")
    if context_type == "emotional_sell":
        st.warning("情绪性卖出容易造成卖飞，请确认是否有明确回补计划。")


def _sell_context_type_label_for_entry(entry: dict | None) -> str:
    value = str((entry or {}).get("sell_context_type") or "").strip()
    return SELL_CONTEXT_TYPE_LABELS.get(value, "请选择")


def _fundamental_change_labels_for_entry(entry: dict | None) -> list[str]:
    raw = (entry or {}).get("fundamental_change_types")
    if raw is None:
        raw = (entry or {}).get("fundamental_change_type")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [raw] if raw.strip() else []
    elif isinstance(raw, (list, tuple, set)):
        parsed = list(raw)
    else:
        parsed = []
    return [FUNDAMENTAL_CHANGE_LABELS.get(str(item), str(item)) for item in parsed if str(item).strip()]


def _sell_reason_tag_labels_for_entry(entry: dict | None) -> list[str]:
    raw = (entry or {}).get("sell_reason_tags")
    if raw is None:
        raw = (entry or {}).get("sell_reason_tag_list")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        parsed = list(raw)
    else:
        parsed = []
    return [SELL_REASON_TAG_LABELS.get(str(item), str(item)) for item in parsed if str(item).strip()]


def _combined_sell_reason_note(entry: dict | None) -> str:
    if not entry:
        return ""
    direct = _entry_value(entry, "sell_thesis_note")
    if direct:
        return direct
    legacy_parts = [
        ("估值 / 风险", _entry_value(entry, "valuation_compression_reason")),
        ("流动性 / 市场", _entry_value(entry, "liquidity_shock_reason")),
        ("仓位", _entry_value(entry, "position_risk_reason")),
    ]
    lines = [f"{label}：{value}" for label, value in legacy_parts if value]
    return "\n".join(lines)


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
    uses_planned_fallback = False
    if actual_pct is None and current_qty > 0:
        actual_pct = sell_qty / current_qty
    elif actual_pct is None:
        actual_pct = planned_pct
        uses_planned_fallback = True
    core_ratio = _ratio_value(core_pct)
    if core_ratio is None:
        core_ratio = POSITION_CLASS_DEFAULTS.get(str(position_class or "").upper(), (0.0, 1.0))[0] or 0.0
    core_min_qty = current_qty * core_ratio if current_qty > 0 else 0.0
    tradable_qty = max(0.0, current_qty - core_min_qty)
    actual_after_qty = max(0.0, current_qty - sell_qty)
    actual_remaining_tradable_qty = max(0.0, actual_after_qty - core_min_qty)
    actual_breach_qty = max(0.0, core_min_qty - actual_after_qty)
    planned_sell_qty = round(current_qty * planned_pct) if current_qty > 0 else 0
    planned_after_qty = max(0.0, current_qty - planned_sell_qty)
    planned_remaining_tradable_qty = max(0.0, planned_after_qty - core_min_qty)
    planned_breach_qty = max(0.0, core_min_qty - planned_after_qty)
    return {
        "positionClass": str(position_class or "").upper() or "未分类",
        "currentQty": current_qty,
        "sellQty": sell_qty,
        "plannedSellPct": planned_pct,
        "plannedSellQty": float(planned_sell_qty),
        "plannedAfterQty": planned_after_qty,
        "plannedRemainingTradableQty": planned_remaining_tradable_qty,
        "plannedBreachesCore": planned_breach_qty > 1e-9,
        "plannedBreachQty": planned_breach_qty,
        "actualSellPct": actual_pct,
        "plannedActualDiffPct": abs(actual_pct - planned_pct),
        "usesPlannedFallback": uses_planned_fallback,
        "coreRatioMin": core_ratio,
        "coreMinQty": float(core_min_qty),
        "tradableQty": tradable_qty,
        "actualAfterQty": actual_after_qty,
        "actualRemainingTradableQty": actual_remaining_tradable_qty,
        "actualBreachesCore": actual_breach_qty > 1e-9,
        "actualBreachQty": actual_breach_qty,
        "afterSellQty": actual_after_qty,
        "remainingTradableQty": actual_remaining_tradable_qty,
        "breachesCore": actual_breach_qty > 1e-9 or planned_breach_qty > 1e-9,
        "breachQty": max(actual_breach_qty, planned_breach_qty),
    }


def _discipline_gate_conclusion(result) -> str:
    blockers = {str(item) for item in (getattr(result, "blockers", []) or [])}
    if blockers:
        return "WARN"
    if str(getattr(result, "disciplineStatus", "") or "") == "warning" or getattr(result, "warnings", []):
        return "WARN"
    return "PASS"


def _render_discipline_gate_explanation(result, context: dict) -> None:
    context = _normalized_core_gate_context(context)
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
        ("实际数量", _quantity_text(context["sellQty"])),
        ("实际卖出", _pct_point_text(context["actualSellPct"])),
        ("实际卖后", _quantity_text(context["actualAfterQty"])),
        ("计划比例", _pct_point_text(context["plannedSellPct"])),
        ("计划股数", _quantity_text(context["plannedSellQty"])),
        ("计划卖后", _quantity_text(context["plannedAfterQty"])),
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
        ("实际是否打穿", "是" if context["actualBreachesCore"] else "否"),
        ("实际打穿", _quantity_text(context["actualBreachQty"])),
        ("计划是否打穿", "是" if context["plannedBreachesCore"] else "否"),
        ("计划打穿", _quantity_text(context["plannedBreachQty"])),
    ]
    reason_html = "".join(f"<li>{escape(item)}</li>" for item in reasons)
    action_html = "".join(f"<li>{escape(item)}</li>" for item in actions)
    metric_html = _discipline_gate_metric_html(metric_rows)
    split_html = _discipline_gate_metric_html(split_rows)
    st.markdown(
        f"""
        <section class="trade-gate-card {escape(tone)}">
          <div class="trade-gate-head">
            <strong>风险提示：{escape(_discipline_gate_conclusion_label(conclusion))}</strong>
            <span>{escape(_discipline_gate_summary(conclusion))}</span>
          </div>
          <div class="trade-gate-body">
            <div><b>提示原因</b><ul>{reason_html}</ul></div>
            <div><b>可选复核动作</b><ul>{action_html}</ul></div>
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
        "PASS": "可以确认入账，但仍按计划执行。",
        "WARN": "入账前继续复核，避免情绪驱动。",
        "FIX_REQUIRED": "卖出前建议先修正比例、数量或回补计划；如仍继续，将记录为人工确认。",
        "BLOCK": "高风险卖出提醒；系统不建议，但你可以继续。",
    }.get(conclusion, "需要复核。")


def _discipline_gate_conclusion_label(conclusion: str) -> str:
    return {
        "PASS": "通过",
        "WARN": "需要复核",
        "FIX_REQUIRED": "卖出前复核",
        "BLOCK": "高风险提醒",
    }.get(str(conclusion or ""), "需要复核")


def _discipline_gate_reasons(result, context: dict, max_allowed_qty: int) -> list[str]:
    reasons: list[str] = []
    position_class = str(context.get("positionClass") or "").strip().upper()
    floor_label = "A 类核心仓" if position_class == "A" else "B 类持仓底线" if position_class == "B" else "持仓底线"
    if context["plannedActualDiffPct"] > 0.02:
        reasons.append(
            f"你实际填写卖出 {_quantity_text(context['sellQty'])} 股，但计划卖出比例为 {_pct_point_text(context['plannedSellPct'])}。"
        )
    if context.get("usesPlannedFallback"):
        reasons.append("暂时拿不到当前持股，实际卖出比例先按计划卖出比例估算；入账前仍会由组合持仓兜底校验。")
    if context["actualBreachesCore"]:
        reasons.append(
            f"按实际数量测算，卖后剩 {_quantity_text(context['actualAfterQty'])} 股，会低于{floor_label}约 {_quantity_text(math.ceil(context['actualBreachQty']))} 股。"
        )
    else:
        safe_text = "未打穿核心仓" if position_class == "A" else "未低于 B 类持仓底线" if position_class == "B" else "未低于持仓底线"
        reasons.append(f"按实际数量测算，卖后剩 {_quantity_text(context['actualAfterQty'])} 股，{safe_text}。")
    if context["plannedSellPct"] > 0:
        if context["plannedBreachesCore"]:
            reasons.append(
                f"按计划比例测算，约卖 {_quantity_text(context['plannedSellQty'])} 股，卖后剩 {_quantity_text(context['plannedAfterQty'])} 股，会低于{floor_label}约 {_quantity_text(math.ceil(context['plannedBreachQty']))} 股。"
            )
        else:
            safe_text = "未打穿核心仓" if position_class == "A" else "未低于 B 类持仓底线" if position_class == "B" else "未低于持仓底线"
            reasons.append(f"按计划比例测算，约卖 {_quantity_text(context['plannedSellQty'])} 股，卖后剩 {_quantity_text(context['plannedAfterQty'])} 股，{safe_text}。")
    sell_level = str(getattr(result, "sellLevel", "") or "N/A")
    max_allowed_pct = float(getattr(result, "maxAllowedSellPct", 0) or 0)
    if context["actualSellPct"] > max_allowed_pct + 1e-9:
        reasons.append(
            f"实际卖出 {_pct_point_text(context['actualSellPct'])}，超过 {sell_level} 上限 {_pct_point_text(max_allowed_pct)}。"
        )
    if context["plannedSellPct"] > max_allowed_pct + 1e-9:
        if position_class == "B":
            reasons.append(
                f"B 类本次卖出上限为 {_pct_point_text(max_allowed_pct)}。当前计划卖出 {_pct_point_text(context['plannedSellPct'])}，超过纪律上限。"
            )
        elif position_class == "A":
            reasons.append(
                f"A 类核心股在宏观风险/恐慌卖出场景下，上限为 {_pct_point_text(max_allowed_pct)}。当前计划卖出 {_pct_point_text(context['plannedSellPct'])}，超过纪律上限。"
            )
        else:
            reasons.append(
                f"当前卖出上限为 {_pct_point_text(max_allowed_pct)}。当前计划卖出 {_pct_point_text(context['plannedSellPct'])}，超过纪律上限。"
            )
    if max_allowed_qty > 0:
        reasons.append(f"若卖出超过 {max_allowed_qty} 股，将超过 {sell_level} 卖出上限。")
    for blocker in getattr(result, "blockers", []) or []:
        if str(blocker) == "a_class_core_floor_breached" and not context["actualBreachesCore"]:
            continue
        if str(blocker) == "planned_sell_pct_breaches_core_floor" and not context["plannedBreachesCore"]:
            continue
        text = _discipline_message_text(blocker)
        if text not in reasons:
            reasons.append(text)
    for warning in (getattr(result, "warnings", []) or [])[:2]:
        text = _discipline_message_text(warning)
        if text not in reasons:
            reasons.append(text)
    return reasons or ["当前交易纪律检查通过。"]


def _normalized_core_gate_context(context: dict) -> dict:
    normalized = dict(context)
    current_qty = _number(normalized.get("currentQty")) or 0.0
    sell_qty = _number(normalized.get("sellQty")) or 0.0
    planned_pct = _ratio_value(normalized.get("plannedSellPct")) or 0.0
    if _number(normalized.get("actualSellPct")) is None:
        normalized["actualSellPct"] = planned_pct
        normalized["usesPlannedFallback"] = True
    actual_after_qty = max(0.0, current_qty - sell_qty)
    core_ratio = _ratio_value(normalized.get("coreRatioMin"))
    if core_ratio is None:
        core_ratio = 0.0
    core_min_qty = current_qty * core_ratio if current_qty > 0 else 0.0
    tradable_qty = max(0.0, current_qty - core_min_qty)
    actual_remaining_tradable_qty = max(0.0, actual_after_qty - core_min_qty)
    actual_breach_qty = max(0.0, core_min_qty - actual_after_qty)
    planned_sell_qty = round(current_qty * planned_pct) if current_qty > 0 else 0
    planned_after_qty = max(0.0, current_qty - planned_sell_qty)
    planned_remaining_tradable_qty = max(0.0, planned_after_qty - core_min_qty)
    planned_breach_qty = max(0.0, core_min_qty - planned_after_qty)
    normalized.update(
        {
            "plannedSellPct": planned_pct,
            "plannedSellQty": float(planned_sell_qty),
            "plannedAfterQty": planned_after_qty,
            "plannedRemainingTradableQty": planned_remaining_tradable_qty,
            "plannedBreachesCore": planned_breach_qty > 1e-9,
            "plannedBreachQty": planned_breach_qty,
            "usesPlannedFallback": bool(normalized.get("usesPlannedFallback")),
            "coreRatioMin": core_ratio,
            "coreMinQty": core_min_qty,
            "tradableQty": tradable_qty,
            "actualAfterQty": actual_after_qty,
            "actualRemainingTradableQty": actual_remaining_tradable_qty,
            "actualBreachesCore": actual_breach_qty > 1e-9,
            "actualBreachQty": actual_breach_qty,
            "afterSellQty": actual_after_qty,
            "remainingTradableQty": actual_remaining_tradable_qty,
            "breachesCore": actual_breach_qty > 1e-9 or planned_breach_qty > 1e-9,
            "breachQty": max(actual_breach_qty, planned_breach_qty),
        }
    )
    return normalized


def _discipline_gate_actions(result, context: dict, max_allowed_qty: int) -> list[str]:
    actions: list[str] = []
    position_class = str(context.get("positionClass") or "").strip().upper()
    if context["plannedActualDiffPct"] > 0.02:
        planned_qty = int(context["plannedSellQty"])
        planned_is_blocked = context["plannedBreachesCore"] or (
            max_allowed_qty > 0 and context["plannedSellQty"] > max_allowed_qty
        )
        suffix = f"；但 {_pct_point_text(context['plannedSellPct'])} 计划会触发纪律复核。" if planned_is_blocked else "。"
        actions.append(
            f"请将计划卖出比例改为 {_pct_point_text(context['actualSellPct'])}，或将数量改为与 {_pct_point_text(context['plannedSellPct'])} 一致（约 {planned_qty} 股）{suffix}"
        )
    if max_allowed_qty > 0 and context["sellQty"] > max_allowed_qty:
        actions.append(f"把本次卖出数量降到不超过 {max_allowed_qty} 股。")
    if max_allowed_qty > 0 and context["plannedSellQty"] > max_allowed_qty:
        actions.append(f"把计划卖出比例降到不超过 {_pct_point_text(max_allowed_qty / context['currentQty'] if context['currentQty'] else 0)}。")
    if context["actualBreachesCore"] or context["plannedBreachesCore"]:
        if position_class == "B":
            actions.append("如果确实要卖到该比例，请改选降级为观察、买入逻辑不清、thesis 失效或风险控制，并写清后续复核计划。")
        elif position_class == "A":
            actions.append(f"至少保留 {_quantity_text(context['coreMinQty'])} 股 A 类核心仓。")
        else:
            actions.append(f"至少保留 {_quantity_text(context['coreMinQty'])} 股持仓底线。")
    if "reentry_plan_required_before_trim_or_sell" in {str(item) for item in (getattr(result, "blockers", []) or [])}:
        actions.append("补全回踩买回价、不跌反涨买回价和时间止损。")
    if not actions:
        actions.append("按计划入账前再次确认不是宏观恐慌或焦虑驱动。")
    return actions


def _ratio_value(value: object) -> float | None:
    number = _parse_optional_float(value)
    if number is None:
        return None
    return number / 100 if abs(number) > 1 else number


def _pct_point_text(value: object, *, suffix: str = "%") -> str:
    number = _ratio_value(value)
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
    st.caption("按需填写回补条件；基础卖出记录只需要完成上方执行与理由。")
    keys = _reentry_keys(key_suffix)
    if st.button("使用系统建议生成回补计划", key=keys["generate"], width="stretch"):
        suggestion = _build_reentry_plan_suggestion(symbol, trade_price)
        for field, value in suggestion.items():
            if field in keys:
                st.session_state[keys[field]] = value

    price_cols = st.columns([1, 1, 0.72, 0.88], gap="small")
    pullback_price = price_cols[0].text_input(
        "目标买回价",
        value=_entry_number_text(editing_entry, "reentry_pullback_price"),
        key=keys["pullback"],
    )
    breakout_price = price_cols[1].text_input(
        "不涨回补价（可选）",
        value=_entry_number_text(editing_entry, "reentry_breakout_price"),
        key=keys["breakout"],
    )
    time_stop_days = price_cols[2].text_input(
        "时间上限天数",
        value=_entry_int_text(editing_entry, "reentry_time_stop_days") or "5",
        key=keys["time_stop"],
    )
    pullback_pct = price_cols[3].text_input(
        "回补比例 %",
        value=_entry_percent_text(editing_entry, "reentry_buy_back_pct_on_pullback", "50"),
        key=keys["pullback_pct"],
    )
    st.session_state[keys["breakout_pct"]] = pullback_pct
    breakout_pct = pullback_pct
    plan_text = st.text_area(
        "回补计划说明",
        value=_entry_value(editing_entry, "reentry_plan_text"),
        height=64,
        key=keys["plan_text"],
    )
    thesis_invalidation = st.text_input(
        "不回补条件",
        value=_entry_value(editing_entry, "reentry_thesis_invalidation"),
        key=keys["invalidation"],
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
    return has_concrete_reentry_plan(values)


def _build_reentry_plan_suggestion(symbol: str, trade_price: object = None) -> dict[str, str]:
    sell_price = _parse_optional_float(trade_price)
    pullback = None
    breakout = sell_price
    try:
        report = build_cached_ai_stock_radar_report(symbol).to_dict()
        current_price = _first_number(sell_price, report.get("current_price"), report.get("price"))
        buy_zone_context = report.get("buy_zone_context") if isinstance(report.get("buy_zone_context"), dict) else {}
        pullback, breakout = _reentry_levels_from_buy_zone_context(
            buy_zone_context,
            sell_price=sell_price,
            current_price=current_price,
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


def _reentry_levels_from_buy_zone_context(
    context: dict | None,
    *,
    sell_price: object = None,
    current_price: object = None,
) -> tuple[float | None, float | None]:
    if not isinstance(context, dict):
        return None, _first_number(sell_price, current_price)
    pullback = _first_number(
        context.get("pullback_zone_high"),
        context.get("support_zone_high"),
        context.get("pullback_zone_low"),
        context.get("support_zone_low"),
    )
    breakout = _first_number(
        context.get("confirmation_price"),
        sell_price,
        context.get("chase_price"),
        current_price,
    )
    return pullback, breakout


def _first_number(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _trade_discipline_form_values(action_type: str, key_suffix: str = "new") -> dict:
    if action_type not in SELL_DISCIPLINE_ACTIONS:
        return {}
    reason_label = st.session_state.get(f"trade-discipline-sell-reason-{key_suffix}")
    position_class = _position_class_from_state(f"trade-discipline-position-class-{key_suffix}")
    reentry_values = _reentry_plan_form_values(key_suffix)
    hard_blocked = False
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
        "belowTargetSellPrice": bool(st.session_state.get(f"trade-discipline-below-target-{key_suffix}")),
        "inBuyZoneOrBelow": bool(st.session_state.get(f"trade-discipline-in-buy-zone-or-below-{key_suffix}")),
        "userConfirmedSellWarning": True,
        **reentry_values,
        **_structured_sell_reason_form_values(key_suffix),
    }


def _structured_sell_reason_form_values(key_suffix: str = "new") -> dict:
    context_label = st.session_state.get(f"trade-sell-context-type-{key_suffix}")
    fundamental_labels = st.session_state.get(f"trade-fundamental-change-type-{key_suffix}") or []
    reason_tag_labels = st.session_state.get(f"trade-sell-reason-tags-{key_suffix}") or []
    return {
        "sellContextType": SELL_CONTEXT_TYPE_OPTIONS.get(str(context_label or ""), str(context_label or "")),
        "fundamentalChangeType": [
            FUNDAMENTAL_CHANGE_OPTIONS.get(str(label), str(label))
            for label in fundamental_labels
            if str(label).strip()
        ],
        "sellReasonTags": [
            SELL_REASON_TAG_OPTIONS.get(str(label), str(label))
            for label in reason_tag_labels
            if str(label).strip()
        ],
        "valuationCompressionReason": "",
        "liquidityShockReason": "",
        "positionRiskReason": "",
        "sellThesisNote": st.session_state.get(f"trade-sell-thesis-note-{key_suffix}") or "",
    }


def _structured_sell_reason_validation_error(action_type: str, values: dict) -> str:
    if str(action_type or "").strip().lower() not in SELL_DISCIPLINE_ACTIONS:
        return ""
    return ""


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
        ("核心仓卖出提示", "可作为参考" if result.canSellCore else "系统不建议动核心仓"),
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
    label = "高风险提醒" if is_blocker else "复核提醒"
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
    core = _ratio_value(core) if core is not None else core_default
    trading = _ratio_value(trading) if trading is not None else trading_default
    return (core, trading)


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
            "core_position_min_pct": _ratio_value(values.get("corePositionMinPct", values.get("core_position_min_pct"))),
            "trading_position_max_pct": _ratio_value(values.get("tradingPositionMaxPct", values.get("trading_position_max_pct"))),
            "classification_note": values.get("classificationNote", values.get("classification_note", "")),
        },
    )


def _queue_sell_intent(symbol: str, action_type: str, values: dict) -> None:
    st.session_state["trade_journal_pending_trade_intent"] = {
        "symbol": str(symbol or "").strip().upper(),
        "action_type": str(action_type or "").strip().lower(),
        "action_label": "清仓" if str(action_type or "").strip().lower() == "sell" else "减仓",
        "values": values,
    }
    st.rerun()


def _sell_portfolio_role_context(symbol: str, values: dict) -> dict[str, object]:
    explicit_role = values.get("tradeRole") or values.get("trade_role")
    role = explicit_role or ROLE_OBSERVATION
    clean_symbol = str(symbol or "").strip().upper()
    if clean_symbol and not explicit_role:
        try:
            rows = build_portfolio_view_model().get("rows") or []
        except Exception:
            rows = []
        current_row = next(
            (row for row in rows if str(row.get("symbol") or "").strip().upper() == clean_symbol),
            {},
        )
        role = current_row.get("holdingRole") or current_row.get("role") or ROLE_OBSERVATION
    return {"current_role": role or ROLE_OBSERVATION}


def _render_pending_sell_intent_dialog(store: TradeJournalStore) -> None:
    pending = st.session_state.get("trade_journal_pending_trade_intent")
    if not isinstance(pending, dict):
        return

    def confirm(intent: dict[str, str]) -> None:
        st.session_state.pop("trade_journal_pending_trade_intent", None)
        values = dict(pending.get("values") or {})
        role = intent.get("portfolio_role") or intent.get("trade_role")
        if role:
            values["tradeRole"] = role
            values["roleLabel"] = intent.get("role_label") or portfolio_role_label(role)
        values["pre_trade_intent"] = intent
        _save_entry(store, str(pending.get("symbol") or ""), values)

    def cancel() -> None:
        st.session_state.pop("trade_journal_pending_trade_intent", None)
        st.rerun()

    render_trade_intent_dialog(
        side="sell",
        ticker=str(pending.get("symbol") or ""),
        action_label=str(pending.get("action_label") or "卖出 / 减仓"),
        key_prefix="trade-journal-sell-intent",
        on_confirm=confirm,
        on_cancel=cancel,
        portfolio_role_context=_sell_portfolio_role_context(
            str(pending.get("symbol") or ""),
            dict(pending.get("values") or {}),
        ),
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
    try:
        saved = store.save_entry(symbol, values)
        _sync_stock_classification_profile(saved["symbol"], values)
        ledger_notice = _apply_portfolio_ledger_or_remove(store, saved)
    except ValueError as exc:
        st.session_state["trade_journal_notice"] = ("error", _friendly_error(str(exc)))
        st.rerun()
    st.session_state["trade_journal_notice"] = ledger_notice
    if ledger_notice[0] == "success" and values.get("pre_trade_intent"):
        TradeIntentStore(store.path).save_intent(
            int(saved.get("id") or 0),
            saved["symbol"],
            saved["action_type"],
            values["pre_trade_intent"],
            source="trade_journal",
            snapshots={
                "position_quantity": values.get("preTradeQuantity") or values.get("pre_trade_quantity"),
                "position_weight": values.get("preTradePositionPct") or values.get("pre_trade_position_pct"),
                "buy_zone_context": values.get("sellContextSnapshot") or values.get("sell_context_snapshot") or {},
            },
        )
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
    try:
        saved = store.update_entry(entry_id, symbol, values)
        _sync_stock_classification_profile(saved["symbol"], values)
    except ValueError as exc:
        st.session_state["trade_journal_notice"] = ("error", _friendly_error(str(exc)))
        st.rerun()
    _clear_trade_edit_query()
    st.session_state["trade_journal_notice"] = ("success", f"{saved['symbol']} 交易记录已更新。")
    st.rerun()


def _apply_portfolio_ledger_or_remove(store: TradeJournalStore, saved: dict) -> tuple[str, str]:
    action = str(saved.get("action_type") or "").strip().lower()
    if action not in POSITION_AFFECTING_ACTIONS:
        store.delete_entry(int(saved.get("id") or 0))
        return ("error", "交易日志只记录真实 buy/add/sell/trim，本次未入账。")
    result = apply_trade_to_portfolio(int(saved.get("id") or 0))
    status = str(result.get("status") or "")
    if status == "success":
        return ("success", f"{saved['symbol']} 成交已入账。")
    if status == "already_synced":
        return ("error", f"{saved['symbol']} 已入账过，未重复作用到持仓。")
    store.delete_entry(int(saved.get("id") or 0))
    return ("error", f"{saved['symbol']} 入账失败：{result.get('error') or '未知错误'}。交易日志未保存。")


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
    level = _weekly_effective_discipline_level(summary)
    headline = {
        "normal": "纪律正常",
        "caution": "本周操作偏多，注意是否焦虑驱动",
        "danger": "交易纪律风险高，建议暂停非必要操作",
        "stop": "本周停止主动卖出，只允许复核和计划",
    }.get(level, "纪律正常")
    metrics = [
        ("本周交易", summary.get("totalTradesThisWeek", 0)),
        ("sell / trim", summary.get("sellTrimCountThisWeek", 0)),
        ("A 类卖出", summary.get("aClassSellCountThisWeek", 0)),
        ("宏观卖出", summary.get("macroSellCountThisWeek", 0)),
        ("无回补计划", summary.get("noReentryPlanSellCount", 0)),
        ("回补待处理", summary.get("reentryObligationCount", 0)),
        ("回补触发", summary.get("reentryObligationTriggeredCount", 0)),
        ("回补逾期", summary.get("reentryObligationOverdueCount", 0)),
        ("NOW 式风险", summary.get("nowStyleRiskCount", 0)),
        ("纪律高风险提醒", summary.get("disciplineBlockerCount", 0)),
        ("纪律提醒", summary.get("disciplineWarningCount", 0)),
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
        "stop": "danger",
    }.get(str(level or ""), "normal")


def _over_trading_level_text(level: str) -> str:
    return {
        "normal": "正常",
        "caution": "注意",
        "danger": "危险",
        "stop": "停止",
    }.get(str(level or ""), "正常")


def _weekly_effective_discipline_level(summary: dict[str, object]) -> str:
    over_trading_level = str(summary.get("overTradingLevel") or "normal")
    score_level = str(summary.get("disciplineLevel") or "normal")
    return max([over_trading_level, score_level], key=_weekly_discipline_level_rank)


def _weekly_discipline_level_rank(level: str) -> int:
    return {"normal": 0, "caution": 1, "danger": 2, "stop": 3}.get(str(level or ""), 0)


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
        entries = store.list_entries()
    else:
        entries = store.list_entries(selected)
    return _attach_trade_intent_reviews(entries, store.path)


def _attach_trade_intent_reviews(entries: list[dict], path: Path) -> list[dict]:
    if not entries:
        return entries
    try:
        reviews = TradeIntentStore(path).list_intents()
    except Exception:  # pragma: no cover - journal list should remain readable if intent table is unavailable
        reviews = []
    by_trade_id = {
        int(review.get("trade_id") or review.get("trade_entry_id") or 0): review
        for review in reviews
        if int(review.get("trade_id") or review.get("trade_entry_id") or 0) > 0
    }
    result: list[dict] = []
    for entry in entries:
        copied = dict(entry)
        entry_id = int(copied.get("id") or 0)
        if entry_id in by_trade_id:
            copied["trade_intent_review"] = by_trade_id[entry_id]
        result.append(copied)
    return result


def _load_trade_performance_summary(store: TradeJournalStore) -> dict:
    try:
        result = summarize_trade_performance(path=store.path, filters={})
    except Exception:  # pragma: no cover - summary strip should not block journal rendering
        return {}
    return result.get("summary") or {}


def _executed_trade_entries(entries: list[dict]) -> list[dict]:
    result: list[dict] = []
    seen_ids: set[int] = set()
    for entry in entries:
        entry_id = int(entry.get("id") or 0)
        if entry_id in seen_ids:
            continue
        if _is_executed_trade_entry(entry):
            result.append(entry)
            seen_ids.add(entry_id)
    return result


def _historical_non_trade_entries(entries: list[dict], executed_entries: list[dict]) -> list[dict]:
    executed_ids = {int(entry.get("id") or 0) for entry in executed_entries}
    result: list[dict] = []
    seen_ids: set[int] = set(executed_ids)
    for entry in entries:
        entry_id = int(entry.get("id") or 0)
        if entry_id in seen_ids:
            continue
        result.append(entry)
        seen_ids.add(entry_id)
    return result


def _is_executed_trade_entry(entry: dict) -> bool:
    action = str(entry.get("action_type") or "").strip().lower()
    if action not in {"buy", "add", "sell", "trim"}:
        return False
    if bool(entry.get("radar_observation_only")):
        return False
    if action not in SELL_DISCIPLINE_ACTIONS and str(entry.get("discipline_status") or "").strip().lower() == "blocked":
        return False
    entry_id = int(entry.get("id") or 0)
    if entry_id <= 0:
        return False
    status = get_trade_portfolio_sync_status(entry_id)
    return str(status.get("syncStatus") or "") == "synced"


def _render_summary(entries: list[dict], performance_summary: dict | None = None, *, legacy_count: int = 0) -> None:
    summary = performance_summary or {}
    items = [
        ("已实现盈亏", _money_text(summary.get("total_realized_pnl")), "真实盈亏"),
        ("胜率", _percent_or_dash(summary.get("win_rate")), "已完成卖出"),
        ("平均持仓天数", _days_text(summary.get("average_holding_days")), "FIFO 统计"),
        ("疑似卖飞次数", str(summary.get("suspected_sell_fly_count") or 0), "复盘提示"),
        ("历史非成交", str(legacy_count), "旧系统记录"),
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


def _render_trade_performance_stats(store: TradeJournalStore, symbols: list[str]) -> None:
    with st.expander("完整战绩统计", expanded=False):
        st.caption("只统计已入账的真实 buy/add/sell/trim；仅观察记录不计入已实现盈亏。卖出风险提醒不会自动排除真实成交。")
        filters = _trade_performance_filters(symbols)
        try:
            result = summarize_trade_performance(path=store.path, filters=filters)
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            st.warning(f"战绩统计暂时无法读取：{_friendly_error(str(exc))}")
            return
        summary = result.get("summary") or {}
        rows = result.get("realized_trades") or []
        _render_trade_performance_cards(summary)
        if not rows:
            st.info("暂无已完成交易。只有已入账的卖出 / 减仓才会进入真实战绩。")
            return
        _render_trade_performance_table(rows)
        with st.expander("匹配 lot 明细", expanded=False):
            _render_trade_performance_lots(rows)
        with st.expander("分组统计", expanded=False):
            _render_trade_performance_groups(result.get("groups") or {})


def _trade_performance_filters(symbols: list[str]) -> dict:
    cols = st.columns([1.0, 1.0, 0.8, 1.0, 1.0])
    period = cols[0].selectbox("时间范围", ["全部", "近30天", "近90天", "今年"], key="trade-performance-period")
    ticker = cols[1].selectbox("Ticker", ["全部", *symbols], key="trade-performance-ticker")
    tier = cols[2].selectbox("A/B/C", ["全部", "A", "B", "C"], key="trade-performance-tier")
    outcome_label = cols[3].selectbox("盈亏", ["全部", "只看盈利", "只看亏损"], key="trade-performance-outcome")
    issue_only = cols[4].checkbox("只看疑似纪律问题", key="trade-performance-discipline-only")
    today = date.today()
    date_from = None
    if period == "近30天":
        date_from = today - timedelta(days=30)
    elif period == "近90天":
        date_from = today - timedelta(days=90)
    elif period == "今年":
        date_from = date(today.year, 1, 1)
    outcome = {"只看盈利": "profit", "只看亏损": "loss"}.get(outcome_label, "")
    return {
        "date_from": date_from,
        "ticker": "" if ticker == "全部" else ticker,
        "position_tier": "" if tier == "全部" else tier,
        "outcome": outcome,
        "discipline_issue_only": issue_only,
    }


def _render_trade_performance_cards(summary: dict) -> None:
    items = [
        ("已实现盈亏", _money_text(summary.get("total_realized_pnl")), "REALIZED"),
        ("已实现盈亏率", _percent_or_dash(summary.get("realized_pnl_pct")), "RETURN"),
        ("可计算卖出", str(summary.get("completed_sell_count") or 0), "SELLS"),
        ("缺成本卖出", str(summary.get("missing_cost_count") or 0), "MISSING"),
        ("缺成本数量", _quantity_text(summary.get("missing_cost_quantity")), "SHARES"),
        ("缺成本金额", _money_text(summary.get("missing_cost_amount")), "VALUE"),
        ("胜率", _percent_or_dash(summary.get("win_rate")), "WIN RATE"),
        ("平均盈利", _money_text(summary.get("average_winner")), "AVG WIN"),
        ("平均亏损", _money_text(summary.get("average_loser")), "AVG LOSS"),
        ("最大盈利", _money_text(summary.get("max_winner")), "BEST"),
        ("最大亏损", _money_text(summary.get("max_loser")), "WORST"),
        ("平均持仓", _days_text(summary.get("average_holding_days")), "AVG DAYS"),
        ("中位持仓", _days_text(summary.get("median_holding_days")), "MEDIAN DAYS"),
        ("疑似卖飞", str(summary.get("suspected_sell_fly_count") or 0), "REVIEW"),
        ("A类疑似卖飞", str(summary.get("a_class_suspected_sell_fly_count") or 0), "A REVIEW"),
        ("情绪型卖出", str(summary.get("emotional_sell_count") or 0), "MOOD"),
        ("买区内卖出", str(summary.get("buy_zone_sell_count") or 0), "ZONE"),
        ("低于目标卖出", str(summary.get("below_target_sell_count") or 0), "TARGET"),
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
    st.markdown(f'<div class="trade-journal-summary performance">{html}</div>', unsafe_allow_html=True)


def _render_trade_performance_table(rows: list[dict]) -> None:
    headers = [
        "日期",
        "股票",
        "操作",
        "数量",
        "卖出价",
        "盈亏",
        "持仓",
        "等级",
        "卖出原因",
        "状态",
        "详情",
    ]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body = "".join(_trade_performance_row_html(row) for row in rows[:80])
    st.markdown(
        (
            '<div class="trade-journal-table-wrap performance">'
            '<table class="trade-journal-table performance">'
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if len(rows) > 80:
        st.caption(f"仅显示前 80 条已实现交易；当前过滤后共 {len(rows)} 条。")


def _trade_performance_row_html(row: dict) -> str:
    pnl = _number(row.get("realized_pnl"))
    tone = "gain" if pnl is not None and pnl >= 0 else "loss"
    status_html = _performance_status_badges(row)
    detail_html = _trade_performance_detail_html(row)
    detail_toggle = (
        '<details class="trade-row-detail-toggle">'
        '<summary>查看详情</summary>'
        f"{detail_html}"
        "</details>"
    )
    return (
        "<tr>"
        f"<td>{escape(_text(row.get('sell_date')))}</td>"
        f'<td class="symbol">{escape(_text(row.get("ticker")))}</td>'
        f"<td>{escape(_performance_action_text(row.get('action_type')))}</td>"
        f"<td>{escape(_quantity_text(row.get('sell_quantity')))}</td>"
        f"<td>{escape(_money_text(row.get('sell_price')))}</td>"
        f'<td class="{tone}">{_performance_pnl_cell(row)}</td>'
        f"<td>{_performance_holding_cell(row)}</td>"
        f"<td>{escape(_position_tier_text(row.get('position_tier')))}</td>"
        f"<td>{escape(_sell_reason_text(row.get('sell_reason_type')))}</td>"
        f'<td class="performance-status">{status_html}</td>'
        f"<td>{detail_toggle}</td>"
        "</tr>"
    )


def _trade_performance_detail_html(row: dict) -> str:
    below_target = "是" if row.get("below_target_sell_price") else "否"
    flags = row.get("discipline_flags") or []
    flag_text = "；".join(str(item) for item in flags) if flags else "无"
    review = row.get("sell_review") if isinstance(row.get("sell_review"), dict) else {}
    review_text = format_sell_review_label(review or flags)
    sell_fly_text = "是" if (review.get("suspected_sell_fly") or row.get("suspected_sell_fly_risk")) else "否"
    missing_review_fields = "、".join(str(item) for item in (review.get("data_missing_fields") or row.get("sell_review_missing_fields") or [])) or "无"
    reentry = _text(row.get("reentry_plan_text"))
    note = _text(row.get("notes"))
    action = "补录成本" if row.get("cost_basis_missing") else "继续复盘"
    left_rows = [
        ("买入均价", _buy_avg_cost_text(row)),
        ("卖出价", _money_text(row.get("sell_price"))),
        ("目标价", _money_text(row.get("target_sell_price"))),
        ("是否低于目标", below_target),
        ("买入心情", _mood_text(row.get("buy_mood"))),
        ("卖出心情", _mood_text(row.get("sell_mood"))),
        ("卖出原因", _sell_reason_text(row.get("sell_reason_type"))),
        ("卖出原因类型", _sell_context_type_text(row.get("sell_context_type"))),
        ("备注", note),
    ]
    right_rows = [
        ("成本来源", _cost_basis_source_text(row.get("cost_basis_source"))),
        ("成本状态", _cost_basis_status_text(row.get("cost_basis_status"))),
        ("是否计入统计", "是" if row.get("included_in_performance") else "否"),
        ("持仓天数说明", _holding_days_text(row)),
        ("事件交易", _event_trade_note_text(row)),
        ("纪律问题", flag_text),
        ("卖出复盘", review_text),
        ("疑似卖飞风险", sell_fly_text),
        ("复盘数据缺口", missing_review_fields),
        ("回补计划", reentry),
        ("建议动作", action),
    ]
    return (
        '<div class="performance-detail-grid">'
        f'<div><b>交易信息</b>{_performance_detail_list(left_rows)}</div>'
        f'<div><b>统计与纪律状态</b>{_performance_detail_list(right_rows)}</div>'
        "</div>"
    )


def _performance_detail_list(rows: list[tuple[str, str]]) -> str:
    return "".join(
        "<p>"
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        "</p>"
        for label, value in rows
    )


def _performance_pnl_cell(row: dict) -> str:
    if row.get("cost_basis_missing"):
        return '<strong>未计算</strong><small>缺成本</small>'
    return (
        f"<strong>{escape(_money_text(row.get('realized_pnl')))}</strong>"
        f"<small>{escape(_percent_or_dash(row.get('realized_pnl_pct')))}</small>"
    )


def _performance_holding_cell(row: dict) -> str:
    if row.get("cost_basis_missing") or (
        str(row.get("cost_basis_source") or "") == "position_snapshot" and row.get("holding_days") is None
    ):
        return '<strong>—</strong><small>缺日期</small>'
    return f"<strong>{escape(_days_text(row.get('holding_days')))}</strong>"


def _performance_status_badges(row: dict) -> str:
    badges: list[tuple[str, str]] = []
    if row.get("included_in_performance"):
        badges.append(("ok", "已计入"))
    else:
        badges.append(("muted", "未计入"))
    if row.get("cost_basis_missing"):
        badges.extend([("warn", "缺成本"), ("warn", "需补录")])
    event_status = str(row.get("event_trade_status") or "")
    if event_status == "planned_exit":
        badges.extend([("ok", "计划内退出"), ("muted", "事件交易结束")])
    elif event_status == "needs_review":
        badges.append(("review", "需复盘"))
    has_core_review = any("核心仓卖出需复盘" in str(item) for item in (row.get("discipline_flags") or []))
    if has_core_review:
        badges.append(("review", "核心仓需复盘"))
    review = row.get("sell_review") if isinstance(row.get("sell_review"), dict) else {}
    if review.get("suspected_sell_fly") or row.get("suspected_sell_fly_risk"):
        badges.append(("review", "疑似卖飞"))
    elif row.get("discipline_flags") and not has_core_review:
        badges.append(("review", "纪律问题"))
    if not row.get("cost_basis_missing") and not row.get("discipline_flags") and not event_status:
        badges.append(("muted", "已匹配"))
    return "".join(
        f'<span class="performance-badge {escape(tone)}">{escape(label)}</span>'
        for tone, label in badges
    )


def _buy_avg_cost_text(row: dict) -> str:
    if row.get("cost_basis_missing"):
        return "缺成本"
    return _money_text(row.get("buy_avg_price"))


def _realized_pnl_text(row: dict) -> str:
    if row.get("cost_basis_missing"):
        return "未计算"
    return _money_text(row.get("realized_pnl"))


def _realized_pct_text(row: dict) -> str:
    if row.get("cost_basis_missing"):
        return "未计算"
    return _percent_or_dash(row.get("realized_pnl_pct"))


def _holding_days_text(row: dict) -> str:
    if row.get("cost_basis_missing"):
        return "缺买入日期"
    if str(row.get("cost_basis_source") or "") == "position_snapshot" and row.get("holding_days") is None:
        return "缺买入日期"
    return _days_text(row.get("holding_days"))


def _event_trade_note_text(row: dict) -> str:
    note = str(row.get("event_trade_note") or "").strip()
    return note or "无"


def _cost_basis_source_text(value: object) -> str:
    return {
        "fifo": "FIFO buy/add lot",
        "position_snapshot": "持仓快照",
        "manual_cost_basis": "手动成本基准",
        "mixed": "混合来源",
        "missing": "缺 buy/add lot",
    }.get(str(value or ""), "缺 buy/add lot")


def _cost_basis_status_text(value: object) -> str:
    return {
        "matched_fifo": "逐笔匹配",
        "position_snapshot": "快照估算",
        "manual_cost_basis": "手动补录",
        "mixed": "混合计算",
        "missing": "需补录成本",
    }.get(str(value or ""), "需补录成本")


def _position_tier_text(value: object) -> str:
    tier = str(value or "").strip().upper()
    if tier in {"A", "B", "C"}:
        return f"{tier}类"
    return "等级缺失"


def _render_trade_performance_lots(rows: list[dict]) -> None:
    for row in rows[:30]:
        lots = row.get("matched_lots") or []
        title = f"{row.get('ticker')} {row.get('sell_date')} / {_quantity_text(row.get('matched_quantity'))} 股"
        st.markdown(f"**{title}**")
        if not lots:
            st.caption("缺买入成本或未匹配到 buy lot。")
            continue
        lot_lines = [
            (
                f"- {lot.get('buy_date')} 买入 {_quantity_text(lot.get('matched_quantity'))} 股，"
                f"{_money_text(lot.get('buy_price'))} -> {_money_text(lot.get('sell_price'))}，"
                f"盈亏 {_money_text(lot.get('realized_pnl'))}，持仓 {_days_text(lot.get('holding_days'))}"
            )
            for lot in lots
        ]
        st.markdown("\n".join(lot_lines))


def _render_trade_performance_groups(groups: dict) -> None:
    labels = {
        "ticker": "按 ticker",
        "position_tier": "按 A/B/C",
        "buy_mood": "按买入心情",
        "sell_mood": "按卖出心情",
        "sell_reason": "按卖出原因",
        "holding_bucket": "按持仓天数",
    }
    for key, title in labels.items():
        rows = groups.get(key) or []
        if not rows:
            continue
        if key == "position_tier":
            _render_position_tier_performance_group(rows)
            continue
        st.markdown(f"**{title}**")
        body = "".join(
            "<tr>"
            f"<td>{escape(_group_label(key, row.get('key')))}</td>"
            f"<td>{escape(str(row.get('count') or 0))}</td>"
            f"<td>{escape(_money_text(row.get('realized_pnl')))}</td>"
            f"<td>{escape(_percent_or_dash(row.get('win_rate')))}</td>"
            f"<td>{escape(_days_text(row.get('average_holding_days')))}</td>"
            "</tr>"
            for row in rows
        )
        st.markdown(
            (
                '<div class="trade-journal-table-wrap performance group">'
                '<table class="trade-journal-table performance group">'
                "<thead><tr><th>分组</th><th>次数</th><th>盈亏</th><th>胜率</th><th>平均持仓</th></tr></thead>"
                f"<tbody>{body}</tbody>"
                "</table></div>"
            ),
            unsafe_allow_html=True,
        )


def _render_position_tier_performance_group(rows: list[dict]) -> None:
    st.markdown("**按 A/B/C 分类表现**")
    body = "".join(
        "<tr>"
        f"<td>{escape(_group_label('position_tier', row.get('key')))}</td>"
        f"<td>{escape(str(row.get('count') or 0))}</td>"
        f"<td>{escape(_money_text(row.get('realized_pnl')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('realized_pnl_pct')))}</td>"
        f"<td>{escape(_percent_or_dash(row.get('win_rate')))}</td>"
        f"<td>{escape(_money_text(row.get('average_winner')))}</td>"
        f"<td>{escape(_money_text(row.get('average_loser')))}</td>"
        f"<td>{escape(_days_text(row.get('average_holding_days')))}</td>"
        f"<td>{escape(_days_text(row.get('median_holding_days')))}</td>"
        f"<td>{escape(str(row.get('discipline_issue_count') or 0))}</td>"
        "</tr>"
        for row in rows
    )
    st.markdown(
        (
            '<div class="trade-journal-table-wrap performance group tier">'
            '<table class="trade-journal-table performance group tier">'
            "<thead><tr>"
            "<th>等级</th><th>完成交易</th><th>已实现盈亏</th><th>盈亏率</th><th>胜率</th>"
            "<th>平均盈利</th><th>平均亏损</th><th>平均持仓</th><th>中位持仓</th><th>纪律问题</th>"
            "</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table></div>"
        ),
        unsafe_allow_html=True,
    )


def _group_label(group_key: str, value: object) -> str:
    if group_key == "position_tier":
        text = _text(value)
        return "等级缺失" if text in {"未记录", "-"} else text
    if group_key in {"buy_mood", "sell_mood"}:
        return _mood_text(value)
    if group_key == "sell_reason":
        return _sell_reason_text(value)
    return _text(value)


def _performance_action_text(value: object) -> str:
    return {"sell": "清仓", "trim": "减仓"}.get(str(value or ""), _text(value))


def _mood_text(value: object) -> str:
    mood = str(value or "")
    return {
        "NEUTRAL": "平静 / 无明显情绪",
        "well_reasoned": "深思熟虑",
        "plan_execution": "计划内执行",
        "fomo": "FOMO",
        "anxiety": "焦虑",
        "bottom_fishing_impulse": "抄底冲动",
        "macro_fear": "宏观恐慌",
        "revenge_trade": "复仇交易",
        "boredom_trade": "手痒交易",
        "panic_sell": "恐慌卖出",
        "regret_chase": "卖飞后追回",
        "uncertainty": "不确定",
    }.get(mood, _text(mood))


def _days_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:g} 天"


def _render_historical_non_trade_records(entries: list[dict]) -> None:
    if not entries:
        st.info("暂无历史非成交记录。")
        return
    st.caption("这里仅兼容旧系统留下的非成交记录；默认交易流水和战绩统计不会读取这些记录。")
    headers = ["日期", "股票", "类型", "数量 / 价格", "原因", "操作"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body = "".join(_historical_non_trade_row_html(entry) for entry in entries[:80])
    st.markdown(
        (
            '<div class="trade-journal-table-wrap trade-terminal-table-wrap">'
            '<table class="trade-journal-table trade-terminal-table">'
            f"<thead><tr>{header_html}</tr></thead>"
            f"<tbody>{body}</tbody>"
            "</table>"
            "</div>"
        ),
        unsafe_allow_html=True,
    )
    if len(entries) > 80:
        st.caption(f"仅显示前 80 条历史非成交记录；当前过滤后共 {len(entries)} 条。")


def _historical_non_trade_row_html(entry: dict) -> str:
    return (
        "<tr>"
        f"<td>{_cell_html(_text(entry.get('trade_date')), _created_text(entry))}</td>"
        f'<td class="symbol">{escape(_text(entry.get("symbol")))}</td>'
        f"<td>{_action_badge(entry)}</td>"
        f"<td>{_cell_html(_quantity_text(entry.get('quantity')), _money_text(entry.get('price')))}</td>"
        f"<td>{escape(_historical_non_trade_reason(entry))}</td>"
        f'<td class="trade-entry-actions"><span class="zhx-action-group trade-entry-action-group">{_entry_detail_action_html(entry)}</span></td>'
        "</tr>"
    )


def _historical_non_trade_reason(entry: dict) -> str:
    action = str(entry.get("action_type") or "").strip().lower()
    if action not in {"buy", "add", "sell", "trim"}:
        return "旧系统非成交动作"
    if bool(entry.get("radar_observation_only")):
        return "旧系统仅观察记录"
    if str(entry.get("discipline_status") or "").strip().lower() == "blocked":
        return "历史卖出风险提醒记录"
    return "旧系统未入账记录"


def _entry_action_plain_text(entry: dict) -> str:
    action = str(entry.get("action_type") or "").strip().lower()
    return {
        "buy": "买入",
        "add": "加仓",
        "sell": "卖出",
        "trim": "减仓",
    }.get(action, action or BLANK_TEXT)


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

    headers = ["日期", "股票", "操作", "数量 / 价格", "盈亏", "持仓天数", "A/B/C", "卖出原因", "复盘标签", "状态", "操作"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    row_html = "".join(_entry_row_html(entry) for entry in entries)
    st.markdown(
        (
            '<div id="trade-journal-list"></div>'
            '<div class="trade-journal-table-wrap trade-terminal-table-wrap">'
            '<table class="trade-journal-table trade-terminal-table">'
            "<colgroup>"
            '<col style="width:9%"><col style="width:7%"><col style="width:7%"><col style="width:10%">'
            '<col style="width:8%"><col style="width:8%"><col style="width:7%"><col style="width:10%">'
            '<col style="width:auto"><col style="width:8%"><col style="width:132px">'
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

    delete_block_reason = store.delete_entry_block_reason(entry_id)
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
    if delete_block_reason:
        st.warning(delete_block_reason)
    cols = st.columns([1, 1, 4.2])
    if cols[0].button("确认删除", key=f"trade-entry-delete-confirm-{entry_id}", width="stretch", disabled=bool(delete_block_reason)):
        deleted = store.delete_entry(entry_id)
        _clear_trade_delete_query()
        st.session_state["trade_journal_notice"] = (
            "success" if deleted else "error",
            "交易记录已删除。" if deleted else (delete_block_reason or "交易记录不存在或已删除。"),
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
    _render_trade_intent_record(entry, store)
    _render_trade_discipline_tag_editor(entry)


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


def _render_trade_discipline_tag_editor(entry: dict) -> None:
    entry_id = int(entry.get("id") or 0)
    if entry_id <= 0:
        return
    store = DisciplineReviewStore()
    rows = store.list_tags_for_trade(entry_id)
    current_tags = [str(row.get("tag") or "") for row in rows if str(row.get("tag") or "").strip()]
    labels = list(DISCIPLINE_TAG_LABELS.values())
    label_to_tag = {label: tag for tag, label in DISCIPLINE_TAG_LABELS.items()}
    default_labels = [DISCIPLINE_TAG_LABELS[tag] for tag in current_tags if tag in DISCIPLINE_TAG_LABELS]
    with st.expander("纪律标签（可选）", expanded=False):
        st.caption("这些标签只用于个人复盘统计，不参与买入评分，也不会阻止交易。")
        selected = st.multiselect("纪律标签", labels, default=default_labels, key=f"trade-discipline-tags-{entry_id}")
        notes = st.text_area("标签备注", value=_first_discipline_tag_note(rows), height=68, key=f"trade-discipline-tag-notes-{entry_id}")
        if st.button("保存纪律标签", key=f"trade-discipline-tags-save-{entry_id}", width="stretch"):
            store.save_trade_tags(entry_id, [label_to_tag[label] for label in selected], notes)
            st.success("纪律标签已保存。")
            st.rerun()
        if current_tags:
            st.markdown(_discipline_tag_chips_html(current_tags), unsafe_allow_html=True)


def _render_trade_intent_record(entry: dict, store: TradeJournalStore) -> None:
    entry_id = int(entry.get("id") or 0)
    intent = TradeIntentStore(store.path).get_intent_for_trade(entry_id) if entry_id > 0 else None
    st.markdown(intent_record_html(intent), unsafe_allow_html=True)


def _first_discipline_tag_note(rows: list[dict]) -> str:
    for row in rows:
        note = str(row.get("notes") or "").strip()
        if note:
            return note
    return ""


def _discipline_tag_chips_html(tags: list[str]) -> str:
    chips = "".join(f"<span>{escape(label_for_tag(tag))}</span>" for tag in tags)
    return f'<div class="discipline-tag-chip-row">{chips}</div>'


def _entry_discipline_snapshot_html(entry: dict) -> str:
    action = str(entry.get("action_type") or "")
    if action in CLASSIFICATION_ACTIONS:
        return (
            f"{_classification_snapshot_html(entry)}"
            f"{_entry_radar_gate_snapshot_html(entry)}"
            f"{_entry_volume_price_snapshot_html(entry)}"
        )
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
        ("回补计划具体", _yes_no(_entry_has_concrete_reentry_plan(entry))),
    ]
    rows.append(("实际卖出", _discipline_percent(entry.get("actual_sell_pct"))))
    if str(entry.get("discipline_status") or "").strip().lower() == "blocked":
        rows.append(("卖出提醒", "系统不建议，但历史记录可按人工确认继续处理"))
    reentry_html = _entry_reentry_plan_html(entry)
    sell_review_html = _entry_sell_review_html(entry)
    blocker_html = _discipline_detail_messages_html("高风险提醒", entry.get("blockers") or [], is_blocker=True)
    warning_html = _discipline_detail_messages_html("复核提醒", entry.get("warnings") or [], is_blocker=False)
    reminder = escape(_text(entry.get("reminder_text")))
    return (
        f"{_detail_grid_html(rows)}"
        f"{reentry_html}"
        f"{sell_review_html}"
        f"{blocker_html}"
        f"{warning_html}"
        f'<div class="trade-entry-reminder">{reminder}</div>'
    )


def _entry_sell_review_html(entry: dict) -> str:
    action = str(entry.get("action_type") or "").strip().lower()
    if action not in SELL_DISCIPLINE_ACTIONS:
        return ""
    review = evaluate_sell_review_flags(entry)
    snapshot = _entry_sell_context_snapshot(entry)
    rows = [
        ("卖出复盘标签", format_sell_review_label(review)),
        ("卖出原因类型", _sell_context_type_text(_structured_sell_context_type(entry, snapshot))),
        ("是否基本面改写", _yes_no(_structured_sell_context_type(entry, snapshot) == "fundamental_change")),
        ("基本面改写类型", _fundamental_change_text(entry, snapshot)),
        ("是否估值压缩", _yes_no(_structured_sell_context_type(entry, snapshot) == "valuation_compression")),
        ("是否流动性冲击", _yes_no(_structured_sell_context_type(entry, snapshot) == "liquidity_shock")),
        ("是否计划内减仓", _yes_no(_structured_sell_context_type(entry, snapshot) == "planned_reduction")),
        ("是否情绪性卖出", _yes_no(review.get("emotional_sell") or _structured_sell_context_type(entry, snapshot) == "emotional_sell")),
        ("低于目标价", _yes_no(review.get("below_target_sell"))),
        ("买区内/低于买区", _yes_no(review.get("sell_in_buy_zone"))),
        ("A类短持", _yes_no(review.get("a_class_short_hold"))),
        ("缺具体回补计划", _yes_no(review.get("a_class_missing_reentry"))),
        ("情绪型卖出", _yes_no(review.get("emotional_sell"))),
        ("疑似卖飞风险", _yes_no(review.get("suspected_sell_fly"))),
        ("数据缺口", "、".join(str(item) for item in (review.get("data_missing_fields") or [])) or "无"),
    ]
    if snapshot:
        rows.extend(
            [
                ("卖出时等级", _position_class_label(snapshot.get("position_tier") or snapshot.get("position_class"))),
                ("卖出时目标价", _money_text(snapshot.get("target_sell_price"))),
                ("卖出时买区", _radar_zone_text(snapshot.get("buy_zone"))),
                ("卖出时区间状态", _zone_status_text(snapshot.get("zone_status") or snapshot.get("price_position"))),
                ("卖出时持仓天数", _position_holding_days_text(snapshot.get("holding_days_reference"))),
                ("快照缺失字段", "、".join(str(item) for item in (snapshot.get("missing_snapshot_fields") or [])) or "无"),
            ]
        )
    return (
        '<div class="trade-entry-reentry-plan">'
        '<b>卖出复盘</b>'
        f"{_detail_grid_html(rows)}"
        f"{_structured_sell_note_html(entry, snapshot)}"
        "</div>"
    )


def _structured_sell_context_type(entry: dict, snapshot: dict | None = None) -> str:
    snapshot = snapshot or {}
    return str(entry.get("sell_context_type") or snapshot.get("sell_context_type") or "").strip()


def _sell_context_type_text(value: object) -> str:
    clean = str(value or "").strip()
    if not clean:
        return "未记录"
    return SELL_CONTEXT_TYPE_LABELS.get(clean, "未记录")


def _fundamental_change_text(entry: dict, snapshot: dict | None = None) -> str:
    values = _structured_fundamental_change_values(entry, snapshot)
    labels = [FUNDAMENTAL_CHANGE_LABELS.get(str(item), str(item)) for item in values if str(item).strip()]
    return "、".join(labels) if labels else "未记录"


def _structured_fundamental_change_values(entry: dict, snapshot: dict | None = None) -> list[str]:
    snapshot = snapshot or {}
    raw = (
        entry.get("fundamental_change_types")
        or entry.get("fundamental_change_type")
        or snapshot.get("fundamental_change_type")
        or snapshot.get("fundamental_change_types")
    )
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = [raw] if raw.strip() else []
    elif isinstance(raw, (list, tuple, set)):
        parsed = list(raw)
    else:
        parsed = []
    return [str(item) for item in parsed if str(item).strip()]


def _structured_sell_note_html(entry: dict, snapshot: dict | None = None) -> str:
    snapshot = snapshot or {}
    tag_labels = _sell_reason_tag_labels_for_entry(
        {
            "sell_reason_tags": entry.get("sell_reason_tags")
            or entry.get("sell_reason_tag_list")
            or snapshot.get("sell_reason_tags")
        }
    )
    notes = [("原因标签", " / ".join(tag_labels))]
    thesis_note = entry.get("sell_thesis_note") or snapshot.get("sell_thesis_note")
    notes.append(("卖出理由", thesis_note))
    if not thesis_note:
        notes.extend(
            [
                ("估值 / 风险说明", entry.get("valuation_compression_reason") or snapshot.get("valuation_compression_reason")),
                ("流动性 / 市场说明", entry.get("liquidity_shock_reason") or snapshot.get("liquidity_shock_reason")),
                ("仓位风险说明", entry.get("position_risk_reason") or snapshot.get("position_risk_reason")),
            ]
        )
    rows = [(label, _text(value)) for label, value in notes if _entry_text_value(value)]
    if not rows:
        return ""
    return _detail_grid_html(rows)


def _entry_sell_context_snapshot(entry: dict) -> dict:
    snapshot = entry.get("sell_context_snapshot")
    if isinstance(snapshot, dict):
        return snapshot
    raw = entry.get("sell_context_snapshot_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _entry_reentry_plan_html(entry: dict) -> str:
    has_plan = _entry_has_concrete_reentry_plan(entry)
    invalidation = _entry_text_value(entry.get("reentry_thesis_invalidation"))
    if not has_plan and not invalidation:
        return '<div class="trade-entry-discipline-empty">未记录具体回补计划。</div>'
    if not has_plan:
        return (
            '<div class="trade-entry-discipline-empty">'
            f"未记录具体回补计划；仅记录不回补条件：{escape(invalidation)}"
            "</div>"
        )
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


def _entry_has_concrete_reentry_plan(entry: dict) -> bool:
    return has_concrete_reentry_plan(entry)


def _entry_radar_gate_snapshot_html(entry: dict) -> str:
    decision = str(entry.get("radar_decision") or "").strip()
    if not decision:
        return '<div class="trade-entry-discipline-empty">这条买入 / 加仓记录未保存 Radar 提示快照。</div>'
    reasons = [
        str(item)
        for item in (
            entry.get("radar_advisory_warnings")
            or entry.get("radar_block_reasons")
            or []
        )
        if str(item).strip()
    ]
    rows = [
        ("Radar 结论", decision),
        ("Radar 提示", _yes_no(entry.get("radar_advisory_only"))),
        ("情绪提示", _yes_no(entry.get("mood_gate_blocked"))),
        ("仓位提示", _yes_no(entry.get("position_gate_blocked"))),
        ("仅观察记录", _yes_no(entry.get("radar_observation_only"))),
        ("检查时间", _text(entry.get("gate_checked_at"))),
    ]
    reason_html = _discipline_detail_messages_html("Radar 风险提示", reasons, is_blocker=False) if reasons else ""
    sync_note = ""
    if entry.get("radar_blocked"):
        sync_note = '<div class="trade-entry-reminder">Radar 旧字段曾标记高风险：请按当时人工决策复盘。</div>'
    return f"{_detail_grid_html(rows)}{reason_html}{sync_note}"


def _entry_volume_price_snapshot_html(entry: dict) -> str:
    if str(entry.get("action_type") or "") not in CLASSIFICATION_ACTIONS:
        return ""
    status = str(entry.get("volume_price_status") or "").strip()
    if not status:
        return '<div class="trade-entry-discipline-empty">历史日志未记录量价快照</div>'
    rows = [
        ("量价状态", status),
        ("分数", _number_text(entry.get("volume_price_score"))),
        ("量比", _ratio_text(entry.get("volume_ratio"))),
        ("量能标签", _text(entry.get("volume_regime_cn"))),
        ("区间来源", _volume_price_zone_source_text(entry.get("volume_price_zone_source"))),
        ("K线", _text(entry.get("candle_signal_cn"))),
        ("量能", _text(entry.get("volume_signal_cn"))),
        ("支撑", _text(entry.get("support_signal_cn"))),
        ("确认", _text(entry.get("confirmation_signal_cn"))),
        ("派发日", _int_text(entry.get("distribution_count_10d"))),
        ("原因", _text(entry.get("volume_price_reason_cn"))),
    ]
    return (
        '<div class="trade-entry-reentry-plan">'
        '<b>量价承接快照</b>'
        f"{_detail_grid_html(rows)}"
        "</div>"
    )


def _volume_price_zone_source_text(value: object) -> str:
    return {
        "radar": "雷达区间",
        "upstream": "雷达区间",
        "fallback": "本地回退区间",
        "missing": "缺失",
    }.get(str(value or "").strip(), _text(value))


def _entry_text_value(value: object) -> str:
    return str(value or "").strip()


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
    display = snapshot.get("buy_zone_display") if isinstance(snapshot.get("buy_zone_display"), dict) else {}
    items = [
        ("当时价格", _money_text(snapshot.get("price"))),
        ("主动作", _text(display.get("main_action_text")) if display else _final_action_label(snapshot.get("final_action"))),
        ("股票层", _text(display.get("technical_action_text")) if display else _text(snapshot.get("buy_zone_status"))),
        ("账户层", _text(display.get("account_action_text")) if display else _percent_or_dash(snapshot.get("current_add_pct"))),
        ("决策通道", _lane_label(snapshot.get("decision_lane"))),
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
        body = "<li>暂无高风险或复核原因。</li>"
    return (
        '<section class="trade-signal-drawer-card">'
        "<h4>高风险 / 复核原因</h4>"
        f"<ul>{body}</ul>"
        "</section>"
    )


def _signal_reason_label(value: object) -> str:
    text = str(value or "").strip()
    labels = {
        "buy_zone": "买区风险提示",
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
        f"<td>{escape(_entry_realized_pnl_text(entry))}</td>"
        f"<td>{escape(_entry_holding_days_text(entry))}</td>"
        f"<td>{escape(_entry_position_class_text(entry))}</td>"
        f"<td>{escape(_entry_sell_reason_summary(entry))}</td>"
        f'<td class="notes">{_entry_review_tags_html(entry)}</td>'
        f"<td>{_discipline_snapshot_badge(entry)}</td>"
        f'<td class="trade-entry-actions"><span class="zhx-action-group trade-entry-action-group">{_entry_actions_html(entry)}</span></td>'
        "</tr>"
    )


def _entry_realized_pnl_text(entry: dict) -> str:
    for key in ("realized_pnl", "realizedPnl"):
        if entry.get(key) is not None:
            return _money_text(entry.get(key))
    return BLANK_TEXT


def _entry_holding_days_text(entry: dict) -> str:
    snapshot = _entry_sell_context_snapshot(entry)
    value = entry.get("holding_days") or snapshot.get("holding_days_reference")
    return _days_text(value)


def _entry_position_class_text(entry: dict) -> str:
    snapshot = _entry_sell_context_snapshot(entry)
    value = (
        entry.get("position_class")
        or entry.get("pre_trade_position_tier")
        or snapshot.get("position_tier")
        or snapshot.get("position_class")
    )
    return _position_tier_text(value)


def _entry_sell_reason_summary(entry: dict) -> str:
    action = str(entry.get("action_type") or "").strip().lower()
    if action not in SELL_DISCIPLINE_ACTIONS:
        return BLANK_TEXT
    return _sell_reason_text(entry.get("sell_reason_type"))


def _entry_review_tags_html(entry: dict) -> str:
    action = str(entry.get("action_type") or "").strip().lower()
    chips: list[str] = []
    intent = entry.get("trade_intent_review")
    if isinstance(intent, dict):
        chips.append("有交易意图记录")
        discipline_tags = intent.get("discipline_tags")
        if isinstance(discipline_tags, list) and discipline_tags:
            chips.append("有纪律标签")
        flags = intent.get("attention_flags")
        if isinstance(flags, list) and flags:
            chips.append("有复盘关注点")
    if action in SELL_DISCIPLINE_ACTIONS:
        review = evaluate_sell_review_flags(entry)
        label = format_sell_review_label(review)
        if label and label != "无":
            chips.append(label)
    if not chips:
        return '<span class="trade-muted-cell">无</span>'
    return "".join(f'<span class="trade-review-compact">{escape(label)}</span>' for label in chips)


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
    event_status = _entry_event_trade_status(entry)
    if status != "blocked" and event_status == "planned_exit":
        return '<span class="trade-discipline-pill ok">计划内退出</span>'
    if status != "blocked" and event_status == "needs_review":
        return '<span class="trade-discipline-pill warning">需复盘</span>'
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
    return f'<div class="trade-entry-mood-warning"><b>复核</b><span>{escape(text)}</span></div>'


def _entry_event_trade_status(entry: dict) -> str:
    position_class = str(
        entry.get("position_class") or entry.get("position_tier") or entry.get("pre_trade_position_tier") or ""
    ).strip().upper()
    reason = str(entry.get("sell_reason_type") or "").strip().lower()
    if position_class != "C" or reason not in EVENT_EXIT_REASONS:
        return ""
    return "planned_exit" if _entry_has_event_exit_plan(entry) else "needs_review"


def _entry_has_event_exit_plan(entry: dict) -> bool:
    text_parts = [
        entry.get("notes"),
        entry.get("buy_reason"),
        entry.get("classification_note"),
        entry.get("reentry_plan_text"),
        entry.get("exit_plan"),
        entry.get("event_plan"),
    ]
    text = " ".join(str(item or "") for item in text_parts)
    return any(keyword in text for keyword in EVENT_PLAN_KEYWORDS)


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
    number = _ratio_value(value)
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
        "skip": "skip",
    }.get(action, "skip")
    return f'<span class="trade-action-badge {escape(tone)}">{escape(label)}</span>'


def _created_text(entry: dict) -> str:
    created = str(entry.get("created_at") or "")
    return created[:16].replace("T", " ") if created else BLANK_TEXT


def _quantity_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:,.4g}"


def _number_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:g}"


def _ratio_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:.2f}x"


def _money_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return format_currency(number)


def _pnl_text(amount: object, pct: object = None) -> str:
    amount_number = _number(amount)
    pct_number = _number(pct)
    if amount_number is None and pct_number is None:
        return BLANK_TEXT
    if amount_number is None:
        return _percent_or_dash(pct_number)
    if pct_number is None:
        return _money_text(amount_number)
    return f"{_money_text(amount_number)} / {_percent_or_dash(pct_number)}"


def _holding_days(value: object) -> int | None:
    opened_at = _parse_iso_date(value)
    if opened_at is None:
        return None
    return max(0, (date.today() - opened_at).days)


def _position_holding_days_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{int(number)} 天"


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
        return "数量和价格需要填写数字。"
    if "cannot be negative" in message:
        return "数量和价格不能为负数。"
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
        .trade-journal-summary.performance {
            margin-top: 0.36rem;
        }
        .trade-journal-table.performance {
            min-width: 920px;
        }
        .trade-journal-table.performance.group {
            min-width: 520px;
            margin-bottom: 0.55rem;
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
        .trade-journal-table .gain {
            color: #15803D;
            font-weight: 760;
        }
        .trade-journal-table .loss {
            color: #B91C1C;
            font-weight: 760;
        }
        .trade-journal-table.performance td strong,
        .trade-journal-table.performance td small {
            display: block;
            line-height: 1.16;
        }
        .trade-journal-table.performance td small {
            margin-top: 0.12rem;
            color: #64748b;
            font-size: 10.5px;
            font-weight: 620;
        }
        .trade-journal-table.performance .performance-status {
            display: flex;
            flex-wrap: wrap;
            gap: 0.18rem;
            align-items: center;
        }
        .performance-badge {
            display: inline-flex;
            align-items: center;
            height: 20px;
            padding: 0 0.42rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 999px;
            background: #f8fafc;
            color: #64748b;
            font-size: 10.5px;
            font-weight: 720;
            white-space: nowrap;
        }
        .performance-badge.ok {
            border-color: rgba(79, 157, 120, 0.18);
            background: rgba(79, 157, 120, 0.08);
            color: #276749;
        }
        .performance-badge.warn {
            border-color: rgba(181, 106, 50, 0.18);
            background: rgba(255, 251, 235, 0.9);
            color: #8A4B00;
        }
        .performance-badge.review {
            border-color: rgba(37, 99, 235, 0.16);
            background: rgba(239, 246, 255, 0.9);
            color: #1d4ed8;
        }
        .performance-detail-row td {
            height: auto;
            padding: 0.42rem 0.62rem 0.64rem;
            background: rgba(248, 250, 252, 0.68);
        }
        .trade-journal-table tr.performance-detail-row:hover td {
            background: rgba(248, 250, 252, 0.78);
        }
        .performance-detail-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.6rem;
        }
        .performance-detail-grid > div {
            padding: 0.5rem 0.56rem;
            border: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 7px;
            background: rgba(255, 255, 255, 0.76);
        }
        .performance-detail-grid b {
            display: block;
            margin-bottom: 0.36rem;
            color: #0f172a;
            font-size: 11px;
            font-weight: 820;
        }
        .performance-detail-grid p {
            display: grid;
            grid-template-columns: 96px minmax(0, 1fr);
            gap: 0.42rem;
            margin: 0.16rem 0;
            color: #475569;
            font-size: 11px;
        }
        .performance-detail-grid span {
            color: #94a3b8;
        }
        .performance-detail-grid strong {
            min-width: 0;
            color: #334155;
            font-weight: 700;
            overflow-wrap: anywhere;
        }
        .trade-row-detail-toggle {
            min-width: 92px;
        }
        .trade-row-detail-toggle summary {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 24px;
            padding: 0 0.5rem;
            border: 1px solid rgba(15, 23, 42, 0.1);
            border-radius: 999px;
            background: #fff;
            color: #334155;
            font-size: 11px;
            font-weight: 760;
            cursor: pointer;
            list-style: none;
            white-space: nowrap;
        }
        .trade-row-detail-toggle summary::-webkit-details-marker {
            display: none;
        }
        .trade-row-detail-toggle[open] {
            min-width: 520px;
        }
        .trade-row-detail-toggle[open] summary {
            margin-bottom: 0.42rem;
            color: #0f172a;
            background: #f8fafc;
        }
        .trade-review-compact {
            display: inline-block;
            max-width: 220px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            vertical-align: middle;
            color: #334155;
            font-size: 11px;
            font-weight: 700;
        }
        .trade-muted-cell {
            color: #94a3b8;
            font-size: 11px;
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
        .discipline-tag-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.36rem;
            margin-top: 0.45rem;
        }
        .discipline-tag-chip-row span {
            border: 1px solid #dbeafe;
            background: #eff6ff;
            color: #1d4ed8;
            border-radius: 999px;
            padding: 0.14rem 0.46rem;
            font-size: 0.78rem;
            font-weight: 800;
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
        .trade-intent-record {
            margin: 0.35rem 0 0.9rem;
            padding: 0.65rem;
            border: 1px solid rgba(37, 99, 235, 0.12);
            border-radius: 8px;
            background: rgba(239, 246, 255, 0.5);
        }
        .trade-intent-record h4 {
            margin: 0 0 0.45rem;
            color: #0f172a;
            font-size: 0.82rem;
            font-weight: 840;
        }
        .trade-intent-title {
            margin: -0.18rem 0 0.5rem;
            color: #475569;
            font-size: 0.76rem;
            font-weight: 800;
        }
        .trade-intent-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.42rem;
        }
        .trade-intent-grid div {
            padding: 0.42rem 0.5rem;
            border: 1px solid rgba(37, 99, 235, 0.1);
            border-radius: 6px;
            background: #fff;
        }
        .trade-intent-grid span {
            display: block;
            color: #64748b;
            font-size: 0.66rem;
            font-weight: 760;
        }
        .trade-intent-grid strong {
            display: block;
            margin-top: 0.12rem;
            color: #0f172a;
            font-size: 0.8rem;
            font-weight: 820;
        }
        .trade-intent-attention {
            margin-top: 0.52rem;
            padding: 0.48rem 0.55rem;
            border: 1px solid rgba(245, 158, 11, 0.22);
            border-radius: 7px;
            background: rgba(255, 251, 235, 0.74);
            color: #92400e;
            font-size: 0.76rem;
        }
        .trade-intent-attention.muted {
            border-color: rgba(148, 163, 184, 0.2);
            background: rgba(248, 250, 252, 0.72);
            color: #64748b;
        }
        .trade-intent-attention b {
            display: block;
            margin-bottom: 0.28rem;
            color: #78350f;
        }
        .trade-intent-attention span {
            display: inline-flex;
            margin: 0 0.28rem 0.25rem 0;
            padding: 0.12rem 0.42rem;
            border-radius: 999px;
            background: rgba(245, 158, 11, 0.14);
            color: #92400e;
            font-weight: 800;
        }
        .trade-intent-snapshot {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.42rem;
            margin-top: 0.52rem;
        }
        .trade-intent-snapshot div {
            padding: 0.42rem 0.5rem;
            border: 1px solid rgba(148, 163, 184, 0.18);
            border-radius: 6px;
            background: rgba(248, 250, 252, 0.74);
        }
        .trade-intent-snapshot span,
        .trade-intent-snapshot strong {
            display: block;
        }
        .trade-intent-snapshot span {
            color: #64748b;
            font-size: 0.66rem;
            font-weight: 760;
        }
        .trade-intent-snapshot strong {
            color: #0f172a;
            font-size: 0.84rem;
            margin-top: 0.1rem;
        }
        .trade-intent-empty {
            margin: 0.35rem 0 0.9rem;
            padding: 0.58rem 0.68rem;
            border: 1px dashed rgba(148, 163, 184, 0.3);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.72);
            color: #64748b;
            font-size: 0.78rem;
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
        .trade-activity-month-title {
            display:flex;
            align-items:center;
            justify-content:center;
            height:38px;
            border:1px solid rgba(15,23,42,0.08);
            border-radius:8px;
            background:#FFFFFF;
            color:#0F172A;
            font-weight:760;
        }
        .trade-activity-calendar-wrap {
            margin:0.8rem 0 1rem;
            overflow-x:auto;
            border:1px solid rgba(15,23,42,0.08);
            border-radius:8px;
            background:#FFFFFF;
        }
        .trade-activity-calendar {
            width:100%;
            border-collapse:collapse;
            table-layout:fixed;
        }
        .trade-activity-calendar th {
            padding:0.55rem;
            background:#F8FAFC;
            color:#64748B;
            font-size:0.76rem;
            text-align:left;
        }
        .trade-activity-day {
            height:92px;
            border-top:1px solid rgba(15,23,42,0.06);
            border-right:1px solid rgba(15,23,42,0.06);
            vertical-align:top;
        }
        .trade-activity-day.empty {
            background:#F8FAFC;
        }
        .trade-activity-cell {
            display:flex;
            flex-direction:column;
            gap:0.22rem;
            min-height:92px;
            padding:0.55rem;
            color:#334155 !important;
            text-decoration:none !important;
        }
        .trade-activity-cell b {
            color:#0F172A;
            font-size:0.88rem;
        }
        .trade-activity-cell span,
        .trade-activity-cell em {
            color:#64748B;
            font-size:0.72rem;
            font-style:normal;
        }
        .trade-activity-cell strong {
            width:max-content;
            margin-top:auto;
            padding:0.12rem 0.4rem;
            border-radius:999px;
            font-size:0.68rem;
            background:#F1F5F9;
            color:#475569;
        }
        .trade-activity-cell.level-low strong {
            background:#DCFCE7;
            color:#166534;
        }
        .trade-activity-cell.level-medium strong {
            background:#FEF3C7;
            color:#92400E;
        }
        .trade-activity-cell.level-high strong {
            background:#FFEDD5;
            color:#9A3412;
        }
        .trade-activity-cell.level-critical strong {
            background:#FEE2E2;
            color:#991B1B;
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
        .trade-radar-gate {
            margin: 0.45rem 0 0.7rem;
            padding: 0.68rem 0.74rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: rgba(248, 250, 252, 0.92);
        }
        .trade-radar-gate.ok {
            border-left: 4px solid rgba(22, 101, 52, 0.78);
            background: rgba(240, 253, 244, 0.64);
        }
        .trade-radar-gate.warning {
            border-left: 4px solid rgba(180, 83, 9, 0.72);
            background: rgba(255, 251, 235, 0.72);
        }
        .trade-radar-gate.blocked {
            border-left: 4px solid rgba(185, 28, 28, 0.82);
            background: rgba(255, 241, 242, 0.82);
        }
        .trade-radar-gate-head {
            display: flex;
            justify-content: space-between;
            gap: 0.8rem;
            align-items: baseline;
            margin-bottom: 0.5rem;
        }
        .trade-radar-gate-head b {
            color: #0F172A;
            font-size: 0.84rem;
            font-weight: 860;
        }
        .trade-radar-gate-head span {
            color: #64748B;
            font-size: 0.68rem;
            text-align: right;
        }
        .trade-radar-gate-grid {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            border: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 7px;
            overflow: hidden;
            background: rgba(255, 255, 255, 0.58);
        }
        .trade-radar-gate-grid div {
            display: grid;
            min-width: 0;
            gap: 0.08rem;
            padding: 0.42rem 0.48rem;
            border-right: 1px solid rgba(15, 23, 42, 0.055);
            border-bottom: 1px solid rgba(15, 23, 42, 0.045);
        }
        .trade-radar-gate-grid span,
        .trade-radar-reasons b,
        .trade-radar-requirements b {
            color: #64748B;
            font-size: 0.64rem;
        }
        .trade-radar-gate-grid strong {
            color: #0F172A;
            font-size: 0.74rem;
            font-weight: 820;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .trade-radar-reasons,
        .trade-radar-requirements {
            margin-top: 0.55rem;
        }
        .trade-radar-reasons ul,
        .trade-radar-requirements ul {
            margin: 0.22rem 0 0;
            padding-left: 1.05rem;
            color: #334155;
            font-size: 0.7rem;
            line-height: 1.45;
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
