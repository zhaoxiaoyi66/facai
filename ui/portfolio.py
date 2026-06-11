from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from urllib.parse import quote
import streamlit as st

from data.decision_log import DecisionLogStore, TradeJournalStore
from data.portfolio import (
    PortfolioPositionStore,
    PortfolioSettingsStore,
    format_position_tier_label,
    position_tier_badge_class,
)
from data.portfolio_reconciliation import build_portfolio_reconciliation
from data.portfolio_trade_entry import submit_portfolio_buy_add
from data.portfolio_view_model import build_portfolio_view_model
from data.macro_regime import load_macro_regime, macro_regime_trade_hint_text
from data.stock_plan import StockPlanStore, get_buy_plan_status
from data.trading_discipline import evaluate_trading_discipline, load_trading_discipline_config
from formatting import format_currency, format_percent
from settings import load_watchlist
from ui.theme import render_page_header, render_section_title


EMPTY_POSITION = {
    "symbol": "",
    "quantity": "",
    "average_cost": "",
    "position_tier": "",
    "target_position_pct": "",
    "max_acceptable_position_pct": "",
    "planned_sell_price": "",
    "first_trim_price": "",
    "second_trim_price": "",
    "review_price": "",
    "notes": "",
}
BLANK_TEXT = "—"
TRADE_ACTION_LABELS = {
    "buy": "买入",
    "sell": "卖出",
    "add": "加仓",
    "trim": "减仓",
    "skip": "放弃操作",
}
POSITION_CLASS_LABELS = {
    "A": "A 类核心仓",
    "B": "B 类平衡仓",
    "C": "C 类交易仓",
}
POSITION_TIER_FORM_OPTIONS = {
    "请选择等级": "",
    "A类：核心仓/核心资产": "A",
    "B类：中等仓位/优质但非最高确定性": "B",
    "C类：交易仓/高波动/小仓观察": "C",
}
PORTFOLIO_BUY_MOOD_OPTIONS = {
    "请选择": "",
    "深思熟虑": "well_reasoned",
    "计划内执行": "plan_execution",
    "FOMO": "fomo",
    "焦虑": "anxiety",
    "抄底冲动": "bottom_fishing_impulse",
    "复仇交易": "revenge_trade",
}
ENTRY_MODE_OPTIONS = {
    "普通买入": "normal_buy",
    "分批计划买入": "planned_ladder_buy",
    "A类底仓建仓": "starter_position",
}
BUY_PLAN_TYPE_OPTIONS = {
    "A类底仓建仓": "starter_position",
    "分批买入": "ladder_buy",
    "C类事件交易": "event_trade",
    "仅观察": "watch_only",
}
BUY_PLAN_INVALIDATION_OPTIONS = [
    "财报证伪",
    "thesis 破裂",
    "估值逻辑失效",
    "基本面恶化",
    "数据需复核",
    "自定义",
]
BUY_PLAN_DEFAULT_MAX_PCT = {"A": 10, "B": 8, "C": 3}
FRESH_PLAN_REVIEW_MINUTES = 30


def _render_editor(
    position_store: PortfolioPositionStore,
    settings_store: PortfolioSettingsStore,
    rows: list[dict],
    settings: dict,
) -> None:
    _render_portfolio_buy_add_form(position_store, rows)
    _render_buy_plan_manager(rows)
    _render_position_tier_editor(position_store, rows)
    _render_portfolio_settings_form(settings_store, settings)
    return


def _render_portfolio_settings_form(settings_store: PortfolioSettingsStore, settings: dict) -> None:
    with st.expander("组合设置", expanded=False):
        with st.form("portfolio-settings-form"):
            st.caption("组合总资产用于仓位基准；现金由总资产减当前证券市值自动得出。")
            total_value = st.text_input("组合总资产", value=_input_value(settings.get("total_portfolio_value")))
            base_currency = st.text_input("币种", value=str(settings.get("base_currency") or "USD"))
            if st.form_submit_button("保存组合设置", width="stretch"):
                try:
                    settings_store.save_settings(
                        {
                            "total_portfolio_value": total_value,
                            "cash_balance": None,
                            "base_currency": base_currency,
                        }
                    )
                    st.success("组合设置已保存。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))


def _render_portfolio_buy_add_form(position_store: PortfolioPositionStore, rows: list[dict]) -> None:
    position_open = bool(st.session_state.get("portfolio_position_editor_open", False))
    st.markdown('<div id="portfolio-trade-entry"></div>', unsafe_allow_html=True)
    with st.expander("买入 / 加仓", expanded=position_open):
        st.session_state["portfolio_position_editor_open"] = False
        symbols = [str(row.get("symbol") or "") for row in rows]
        options = ["手动输入", *symbols, *_available_watchlist_symbols(symbols)]
        preferred = st.session_state.pop("portfolio_edit_symbol", "")
        if preferred in options:
            st.session_state["portfolio-edit-symbol"] = preferred
        selected_index = options.index(preferred) if preferred in options else 0
        selected = st.selectbox("交易对象", options, index=selected_index, key="portfolio-edit-symbol")
        selected_symbol = "" if selected == "手动输入" else str(selected or "").strip().upper()
        form_key = _position_form_key(selected_symbol or "manual")
        manual_symbol = ""
        if not selected_symbol:
            manual_symbol = st.text_input("股票代码", key=f"{form_key}:symbol")
        effective_ticker = _effective_trade_ticker(selected_symbol, manual_symbol)
        current = position_store.get_position(effective_ticker) if effective_ticker else None
        current = current or EMPTY_POSITION
        tier_options = list(POSITION_TIER_FORM_OPTIONS.keys())
        current_tier = str(current.get("position_tier") or "").strip().upper()
        current_tier_label = next(
            (label for label, value in POSITION_TIER_FORM_OPTIONS.items() if value == current_tier),
            tier_options[0],
        )
        tier_choice = st.selectbox(
            "持仓等级",
            tier_options,
            index=tier_options.index(current_tier_label),
            help="A/B/C 是当前持仓属性，必须手动选择，不自动按股票猜测。",
            key=f"{form_key}:position_tier",
        )
        selected_tier = POSITION_TIER_FORM_OPTIONS.get(str(tier_choice), "")
        _render_buy_execution_plan_summary(effective_ticker, current, selected_tier)
        _render_macro_regime_buy_hint()
        with st.form("portfolio-buy-add-form"):
            st.markdown('<div class="portfolio-form-section">真实买入 / 加仓</div>', unsafe_allow_html=True)
            basic_cols = st.columns([1.2, 1, 1])
            basic_cols[0].text_input("股票代码", value=effective_ticker, disabled=True, key=f"{form_key}:symbol-disabled")
            basic_cols[1].text_input("数量", key=f"{form_key}:quantity")
            basic_cols[2].text_input("成交价", key=f"{form_key}:price")
            discipline_cols = st.columns([1.2, 1])
            discipline_cols[0].selectbox("交易心理", list(PORTFOLIO_BUY_MOOD_OPTIONS), key=f"{form_key}:decision_mood")
            discipline_cols[1].text_input(
                "卖出目标价",
                value=_input_value(_plan_target_sell_price(StockPlanStore().get_plan(effective_ticker)) or current.get("planned_sell_price")),
                key=f"{form_key}:target_sell_price",
            )
            st.checkbox("仅观察记录，不同步到组合持仓", key=f"{form_key}:observation_only")
            st.text_area(
                "买入理由",
                height=86,
                placeholder="本次为什么执行？例如：触发计划第1档 / 建底仓第一笔 / 仅观察记录",
                key=f"{form_key}:buy_reason",
            )
            if st.form_submit_button("提交买入 / 加仓", width="stretch"):
                _submit_portfolio_buy_add(form_key, effective_ticker)


def _render_macro_regime_buy_hint() -> None:
    try:
        snapshot = load_macro_regime()
    except Exception:
        return
    st.info(macro_regime_trade_hint_text(snapshot, context="buy"))


def _render_starter_check_card(form_key: str, current: dict) -> None:
    mode = ENTRY_MODE_OPTIONS.get(str(st.session_state.get(f"{form_key}:entry_mode") or ""), "normal_buy")
    tier = POSITION_TIER_FORM_OPTIONS.get(str(st.session_state.get(f"{form_key}:position_tier") or ""), "")
    if mode != "starter_position":
        return
    thesis_text = str(st.session_state.get(f"{form_key}:starter_thesis") or st.session_state.get(f"{form_key}:buy_reason") or "").strip()
    add_plan_text = str(st.session_state.get(f"{form_key}:starter_add_plan") or "").strip()
    invalidation_text = str(st.session_state.get(f"{form_key}:starter_invalidation") or "").strip()
    target = _number(st.session_state.get(f"{form_key}:target_sell_price"))
    items = [
        ("持仓等级", "A类" if tier == "A" else "仅 A 类可用"),
        ("底仓上限", "7%"),
        ("thesis", "已填写" if thesis_text else "可用买入理由补足"),
        ("后续加仓计划", "已填写" if add_plan_text else "请填写后续加仓计划"),
        ("失效条件", "已填写" if invalidation_text else "请填写失效条件"),
        ("目标卖出价", "已填写" if target is not None else "请填写目标卖出价"),
    ]
    html = "".join(
        '<div class="starter-check-item">'
        f"<span>{escape(label)}</span>"
        f"<b>{escape(value)}</b>"
        "</div>"
        for label, value in items
    )
    st.markdown(
        '<div class="starter-check-card">'
        "<strong>A类底仓检查</strong>"
        f"{html}"
        "<small>底仓建仓不要求已有分批买入计划；仍需通过数据、仓位、情绪和后端快照校验。</small>"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_buy_execution_plan_summary(symbol: str, current: dict, tier: str = "") -> None:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        st.markdown(
            '<div class="buy-execution-plan-summary is-empty">'
            "<strong>计划摘要</strong>"
            "<span>先选择或输入股票代码，系统会读取该股票的买入计划。</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        return
    plan = StockPlanStore().get_plan(ticker)
    if not _plan_has_buy_execution_evidence(plan):
        href = f"?page=portfolio&portfolioPlan={quote(ticker)}#portfolio-buy-plan"
        missing_text = _missing_buy_plan_summary_text(ticker, tier)
        create_text = _create_buy_plan_link_text(tier)
        st.markdown(
            '<div class="buy-execution-plan-summary is-empty">'
            "<strong>计划摘要</strong>"
            f"<span>{escape(missing_text)} 本次买入将按 Radar / 普通买入门禁判断。</span>"
            f'<a href="{escape(href, quote=True)}" target="_self">{escape(create_text)}</a>'
            "</div>",
            unsafe_allow_html=True,
        )
        return

    status = get_buy_plan_status(plan, current_price=current.get("currentPrice"), is_stale=False)
    level = status.get("level") or {}
    cooldown = _buy_plan_cooldown_status(plan)
    condition = _execution_plan_condition_text(plan, status, cooldown)
    rows = [
        ("计划类型", _buy_plan_type_display(plan.get("plan_type"))),
        ("下一档触发价", _money_text(level.get("trigger_price"))),
        ("计划股数", _share_count_text(level.get("planned_quantity"))),
        ("剩余可买", _share_count_text(level.get("remaining_quantity"))),
        ("最大仓位", _percent_plain(plan.get("max_position_pct") or plan.get("target_position_pct"))),
        ("目标卖出价", _money_text(plan.get("target_sell_price"))),
        ("失效条件", str(plan.get("invalidation_condition") or BLANK_TEXT)),
        ("复盘标记", str(cooldown.get("label") or BLANK_TEXT)),
        ("执行条件", condition),
    ]
    html = "".join(
        '<div class="buy-execution-plan-summary-item">'
        f"<span>{escape(label)}</span>"
        f"<b>{escape(str(value or BLANK_TEXT))}</b>"
        "</div>"
        for label, value in rows
    )
    st.markdown(
        '<div class="buy-execution-plan-summary">'
        "<strong>计划摘要</strong>"
        f"{html}"
        "<small>计划只提供执行依据；真正提交仍会经过 Radar / 计划内加仓 / 底仓建仓门禁。</small>"
        "</div>",
        unsafe_allow_html=True,
    )


def _effective_trade_ticker(selected_symbol: object, manual_symbol: object) -> str:
    return str(selected_symbol or manual_symbol or "").strip().upper()


def _missing_buy_plan_summary_text(ticker: str, tier: object) -> str:
    label = _plan_tier_label(tier)
    if label == "A":
        return f"未找到 {ticker} A 类底仓/分批买入计划。"
    if label == "B":
        return f"未找到 {ticker} B 类分批买入计划。"
    if label == "C":
        return f"未找到 {ticker} C 类事件/观察计划。"
    return f"未找到 {ticker} 买入计划。"


def _create_buy_plan_link_text(tier: object) -> str:
    label = _plan_tier_label(tier)
    if label == "A":
        return "创建 A 类底仓或分批买入计划"
    if label == "B":
        return "创建 B 类分批买入计划"
    if label == "C":
        return "创建 C 类事件/观察计划"
    return "创建买入计划"


def _plan_tier_label(tier: object) -> str:
    value = str(tier or "").strip().upper()
    return value if value in {"A", "B", "C"} else ""


def _execution_plan_condition_text(plan: dict, status: dict, cooldown: dict) -> str:
    plan_type = str(plan.get("plan_type") or "").strip()
    fresh_suffix = "；临时计划执行将供复盘" if bool(cooldown.get("fresh")) else ""
    if status.get("status") == "triggered":
        return f"当前价格已触发计划档位{fresh_suffix}"
    if status.get("status") == "near_trigger":
        return f"接近触发，仍需提交时校验{fresh_suffix}"
    if plan_type == "starter_position":
        return f"底仓资料将由计划区提供{fresh_suffix}"
    if status.get("status") == "needs_review":
        return "计划资料需复核"
    if status.get("status") == "stale_or_missing_data":
        return "价格数据需复核"
    return f"尚未触发计划档位{fresh_suffix}"


def _plan_has_buy_execution_evidence(plan: dict) -> bool:
    if not plan:
        return False
    if str(plan.get("plan_type") or "").strip():
        return True
    return bool(plan.get("buy_plan_tranches") or plan.get("target_sell_price") or plan.get("thesis"))


def _plan_target_sell_price(plan: dict) -> object:
    return (plan or {}).get("target_sell_price") or (plan or {}).get("planned_sell_price")


def _buy_execution_plan_submit_fields(plan: dict, tier: str) -> dict[str, object]:
    plan_type = str((plan or {}).get("plan_type") or "").strip()
    clean_tier = str(tier or "").strip().upper()
    if plan_type == "ladder_buy":
        entry_mode = "planned_ladder_buy"
    elif plan_type == "starter_position" and clean_tier == "A":
        entry_mode = "starter_position"
    else:
        entry_mode = "normal_buy"
    starter_plan = plan if entry_mode == "starter_position" else {}
    return {
        "entry_mode": entry_mode,
        "starter_thesis": str(starter_plan.get("thesis") or "").strip(),
        "starter_add_plan": str(starter_plan.get("follow_up_plan") or "").strip(),
        "starter_invalidation_condition": str(starter_plan.get("invalidation_condition") or "").strip(),
    }


def _submit_portfolio_buy_add(form_key: str, selected_symbol: str) -> None:
    symbol = selected_symbol or str(_form_value(form_key, "symbol") or "").strip().upper()
    plan = StockPlanStore().get_plan(symbol) if symbol else {}
    tier = _form_position_tier(form_key)
    plan_fields = _buy_execution_plan_submit_fields(plan, tier)
    try:
        result = submit_portfolio_buy_add(
            symbol,
            {
                "quantity": _form_value(form_key, "quantity"),
                "price": _form_value(form_key, "price"),
                "position_tier": tier,
                "decision_mood": PORTFOLIO_BUY_MOOD_OPTIONS.get(str(_form_value(form_key, "decision_mood") or ""), ""),
                "entry_mode": plan_fields["entry_mode"],
                "buy_reason": _form_value(form_key, "buy_reason"),
                "target_sell_price": _form_value(form_key, "target_sell_price"),
                "starter_thesis": plan_fields["starter_thesis"],
                "starter_add_plan": plan_fields["starter_add_plan"],
                "starter_invalidation_condition": plan_fields["starter_invalidation_condition"],
                "radar_observation_only": bool(_form_value(form_key, "observation_only")),
            },
        )
    except ValueError as exc:
        st.session_state["portfolio_save_notice"] = ("error", str(exc))
        st.rerun()
    gate = result.get("gate") or {}
    entry = result.get("entry") or {}
    sync = result.get("sync") or {}
    if result.get("synced"):
        plan_gate = result.get("planGate") or {}
        if bool(plan_gate.get("planned_ladder_buy")):
            level = str(plan_gate.get("buy_plan_level") or "计划档位").strip()
            message = f"{entry.get('symbol')} 已按分批买入计划（{level}）记录并同步持仓。"
        else:
            message = f"{entry.get('symbol')} 买入/加仓已记录，组合持仓已同步。"
        st.session_state["portfolio_save_notice"] = ("success", message)
    elif bool(gate.get("is_blocked")) or bool(gate.get("is_observation_only")):
        st.session_state["portfolio_save_notice"] = (
            "buy_gate_blocked",
            {
                "symbol": entry.get("symbol"),
                "gate": gate,
                "planGate": result.get("planGate") or {},
                "starterGate": result.get("starterGate") or {},
                "marketStatus": result.get("marketStatus") or {},
                "entryMode": entry.get("entry_mode") or result.get("actionType"),
                "positionTier": tier,
            },
        )
    else:
        message = f"{entry.get('symbol')} 已保存为交易日志，但组合持仓同步失败：{sync.get('error') or '未知错误'}"
        st.session_state["portfolio_save_notice"] = ("error", message)
    st.rerun()
    return


def _render_ladder_buy_plan_reference(symbol: str) -> None:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return
    plan = StockPlanStore().get_plan(ticker)
    levels = plan.get("buy_plan_tranches") or []
    if not levels:
        st.markdown(
            '<div class="ladder-buy-reference is-empty">'
            "<strong>分批买入计划</strong>"
            "<span>未找到计划；本次买入将按 Radar / 买入门禁判断。</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        return
    max_pct = _percent_text(plan.get("target_position_pct") or plan.get("planned_position_pct"))
    invalidation = str(plan.get("invalidation_condition") or plan.get("stop_adding_condition") or "").strip()
    level_items = []
    for item in levels[:3]:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "计划档位").strip()
        price = _money_text(item.get("price"))
        shares = _number(item.get("shares"))
        shares_text = f"{shares:g} 股" if shares is not None else "数量未设"
        level_items.append(f"<li><b>{escape(label)}</b><span>{escape(price)} / {escape(shares_text)}</span></li>")
    level_html = "".join(level_items) or "<li><span>计划档位未完整设置</span></li>"
    st.markdown(
        '<div class="ladder-buy-reference">'
        "<strong>分批买入计划</strong>"
        f"<ul>{level_html}</ul>"
        '<div class="ladder-buy-reference-meta">'
        f"<span>买后上限 {escape(max_pct)}</span>"
        f"<span>失效条件：{escape(invalidation or '未设置')}</span>"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_buy_plan_manager(rows: list[dict]) -> None:
    st.markdown('<div id="portfolio-buy-plan"></div>', unsafe_allow_html=True)
    symbols = sorted({str(row.get("symbol") or "").strip().upper() for row in rows if row.get("symbol")})
    options = [*symbols, *_available_watchlist_symbols(symbols)]
    preferred = st.session_state.pop("portfolio_plan_symbol", "")
    if preferred and preferred not in options:
        options.insert(0, preferred)
    with st.expander("买入计划", expanded=bool(preferred)):
        _render_buy_plan_notice()
        if not options:
            st.caption("暂无持仓或观察池股票。先添加观察池或持仓后，再创建买入计划。")
            return
        selected = st.selectbox(
            "计划股票",
            options,
            index=options.index(preferred) if preferred in options else 0,
            key="portfolio-buy-plan-symbol",
        )
        symbol = str(selected or "").strip().upper()
        row = next((item for item in rows if str(item.get("symbol") or "").strip().upper() == symbol), {})
        store = StockPlanStore()
        plan = store.get_plan(symbol)
        status = get_buy_plan_status(plan, current_price=row.get("currentPrice"), is_stale=False)
        _render_buy_plan_status(symbol, plan, status)
        _render_buy_plan_actions(symbol, plan, status)
        _render_buy_plan_form(store, symbol, plan, row)


def _render_buy_plan_notice() -> None:
    notice = st.session_state.pop("portfolio_buy_plan_notice", None)
    if not notice:
        return
    level, message = notice
    if level == "error":
        st.error(str(message))
    else:
        st.success(str(message))


def _render_buy_plan_status(symbol: str, plan: dict, status: dict) -> None:
    level = status.get("level") or {}
    trigger = _money_text(level.get("trigger_price"))
    qty = _quantity_text(level.get("remaining_quantity"))
    cooldown = _buy_plan_cooldown_status(plan)
    plan_type = _buy_plan_type_display(plan.get("plan_type"))
    max_pct = _percent_plain(plan.get("max_position_pct") or plan.get("target_position_pct"))
    next_line = "暂无有效档位"
    if level:
        next_line = f"{trigger} 买 {qty}"
    st.markdown(
        '<div class="buy-plan-status-strip">'
        f"<b>{escape(symbol)}</b>"
        f"<span>当前计划：{escape(plan_type)}</span>"
        f"<small>最大仓位：{escape(max_pct)} / 下一档：{escape(next_line)}</small>"
        f"<small>当前状态：{escape(str(status.get('label') or '暂无计划'))}</small>"
        f"<small>计划时间：{escape(cooldown['label'])}</small>"
        f"<small>可作为计划内依据：{escape('是' if _buy_plan_can_be_gate_evidence(plan, status) else '否')}</small>"
        f"<small>{escape(str(status.get('message') or ''))}</small>"
        "</div>",
        unsafe_allow_html=True,
    )


def _render_buy_plan_actions(symbol: str, plan: dict, status: dict) -> None:
    level = status.get("level") or {}
    action_cols = st.columns([1, 1, 3])
    if action_cols[0].button("执行买入", key=f"buy-plan-execute:{symbol}", disabled=status.get("status") != "triggered"):
        _prefill_buy_form_from_plan(symbol, plan, level)
        st.rerun()
    with action_cols[1]:
        st.caption("执行买入只会预填表单，仍需提交并通过门禁。")
    with st.form(f"buy-plan-pause-form:{symbol}"):
        reason = st.selectbox(
            "暂缓 / 不买原因",
            ["thesis 破裂", "财报证伪", "数据缺失", "价格到位但需复核", "我情绪悲观 / 不敢买", "其他"],
            key=f"buy-plan-pause-reason:{symbol}",
        )
        detail = st.text_input("补充说明", key=f"buy-plan-pause-note:{symbol}")
        if st.form_submit_button("记录暂缓 / 不买", width="stretch"):
            _save_buy_plan_pause_note(symbol, plan, reason, detail)
            st.success("已记录到计划备注；未创建交易日志，未改变持仓。")
            st.rerun()


def _render_buy_plan_form(store: StockPlanStore, symbol: str, plan: dict, row: dict) -> None:
    level_count_key = f"buy-plan-level-count:{symbol}"
    existing_levels = plan.get("buy_plan_tranches") or []
    if level_count_key not in st.session_state:
        st.session_state[level_count_key] = max(1, min(3, len(existing_levels) or 1))
    add_cols = st.columns([1, 1, 4])
    if add_cols[0].button("添加第 2 档", key=f"buy-plan-add-level-2:{symbol}", disabled=st.session_state[level_count_key] >= 2):
        st.session_state[level_count_key] = 2
        st.rerun()
    if add_cols[1].button("添加第 3 档", key=f"buy-plan-add-level-3:{symbol}", disabled=st.session_state[level_count_key] >= 3):
        st.session_state[level_count_key] = 3
        st.rerun()

    with st.form(f"buy-plan-form:{symbol}"):
        st.markdown('<div class="portfolio-form-section">创建 / 编辑买入计划</div>', unsafe_allow_html=True)
        tier_label = _position_tier_form_label(plan.get("position_class") or row.get("positionTier"))
        plan_type_label = _plan_type_label(plan.get("plan_type") or _default_buy_plan_type_for_tier(POSITION_TIER_FORM_OPTIONS.get(tier_label, "")))
        top = st.columns([1, 1, 1, 1])
        top[0].text_input("股票代码", value=symbol, disabled=True)
        tier_choice = top[1].selectbox(
            "持仓等级",
            list(POSITION_TIER_FORM_OPTIONS),
            index=list(POSITION_TIER_FORM_OPTIONS).index(tier_label),
            key=f"buy-plan-tier:{symbol}",
        )
        selected_tier = POSITION_TIER_FORM_OPTIONS.get(str(tier_choice), "")
        plan_type_choice = top[2].selectbox(
            "计划类型",
            list(BUY_PLAN_TYPE_OPTIONS),
            index=list(BUY_PLAN_TYPE_OPTIONS).index(plan_type_label),
            key=f"buy-plan-type:{symbol}",
        )
        default_max_pct = plan.get("max_position_pct") or plan.get("target_position_pct") or _default_buy_plan_max_pct(selected_tier)
        top[3].text_input("最大仓位 %", value=_input_value(default_max_pct), key=f"buy-plan-max-pct:{symbol}")

        mid = st.columns([1, 1])
        mid[0].text_input("目标卖出价", value=_input_value(plan.get("target_sell_price") or row.get("plannedSellPrice")), key=f"buy-plan-target-sell:{symbol}")
        invalidation_text = str(plan.get("invalidation_condition") or "")
        invalidation_choice = _invalidation_choice(invalidation_text)
        mid[1].selectbox(
            "失效条件",
            BUY_PLAN_INVALIDATION_OPTIONS,
            index=BUY_PLAN_INVALIDATION_OPTIONS.index(invalidation_choice),
            key=f"buy-plan-invalidation-choice:{symbol}",
        )
        if invalidation_choice == "自定义" or invalidation_text not in BUY_PLAN_INVALIDATION_OPTIONS:
            st.text_input(
                "失效条件备注",
                value="" if invalidation_text in BUY_PLAN_INVALIDATION_OPTIONS else invalidation_text,
                placeholder="例如：连续两个季度订单增速失效",
                key=f"buy-plan-invalidation-note:{symbol}",
            )

        st.text_area(
            "买入逻辑",
            value=str(plan.get("thesis") or ""),
            height=56,
            placeholder="为什么这个位置值得买？",
            key=f"buy-plan-thesis:{symbol}",
        )

        levels = plan.get("buy_plan_tranches") or []
        visible_count = int(st.session_state.get(level_count_key) or 1)
        tranche_cols = st.columns(max(1, visible_count))
        for index in range(visible_count):
            item = levels[index] if index < len(levels) and isinstance(levels[index], dict) else {}
            with tranche_cols[index]:
                st.caption(f"第 {index + 1} 档")
                st.text_input("触发价", value=_input_value(item.get("price") or item.get("trigger_price")), key=f"buy-plan-level-price:{symbol}:{index}")
                st.text_input("计划股数", value=_input_value(item.get("shares") or item.get("planned_quantity")), key=f"buy-plan-level-shares:{symbol}:{index}")

        with st.expander("高级设置", expanded=False):
            st.text_input("后续计划", value=str(plan.get("follow_up_plan") or ""), key=f"buy-plan-follow-up:{symbol}")
            for index in range(visible_count):
                item = levels[index] if index < len(levels) and isinstance(levels[index], dict) else {}
                st.text_input(f"第 {index + 1} 档备注", value=str(item.get("note") or ""), key=f"buy-plan-level-note:{symbol}:{index}")
            if BUY_PLAN_TYPE_OPTIONS.get(str(plan_type_choice), "") == "event_trade":
                event_cols = st.columns(4)
                event_cols[0].text_input("事件名称", value=str(plan.get("event_name") or ""), key=f"buy-plan-event-name:{symbol}")
                event_cols[1].text_input("事件日期", value=str(plan.get("event_date") or ""), key=f"buy-plan-event-date:{symbol}")
                event_cols[2].text_input("无反应退出", value=str(plan.get("exit_if_no_reaction") or ""), key=f"buy-plan-exit-no-reaction:{symbol}")
                event_cols[3].text_input("止损价", value=_input_value(plan.get("stop_loss_price")), key=f"buy-plan-stop-loss:{symbol}")
            st.text_area("备注", value=str(plan.get("notes") or ""), height=70, key=f"buy-plan-notes:{symbol}")
        if st.form_submit_button("保存买入计划", width="stretch"):
            try:
                saved = _save_buy_plan_from_form(store, symbol, visible_count)
            except ValueError as exc:
                st.session_state["portfolio_buy_plan_notice"] = ("error", f"保存失败：{exc}")
            except Exception as exc:  # pragma: no cover - defensive UI guard
                st.session_state["portfolio_buy_plan_notice"] = ("error", f"保存失败：{exc}")
            else:
                st.session_state["portfolio_plan_symbol"] = symbol
                st.session_state["portfolio_buy_plan_notice"] = (
                    "success",
                    f"已保存 {symbol} 买入计划。created_at: {saved.get('created_at') or '—'} / updated_at: {saved.get('updated_at') or '—'}",
                )
            st.rerun()


def _save_buy_plan_from_form(store: StockPlanStore, symbol: str, visible_level_count: int = 1) -> dict:
    levels = []
    for index in range(max(1, min(3, int(visible_level_count or 1)))):
        price = st.session_state.get(f"buy-plan-level-price:{symbol}:{index}")
        shares = st.session_state.get(f"buy-plan-level-shares:{symbol}:{index}")
        note = st.session_state.get(f"buy-plan-level-note:{symbol}:{index}")
        if _number(price) is None and _number(shares) is None and not str(note or "").strip():
            continue
        levels.append(
            {
                "label": f"第 {index + 1} 档",
                "price": _number(price),
                "shares": _number(shares),
                "note": str(note or "").strip(),
            }
        )
    values = {
        "position_class": POSITION_TIER_FORM_OPTIONS.get(str(st.session_state.get(f"buy-plan-tier:{symbol}") or ""), ""),
        "plan_type": BUY_PLAN_TYPE_OPTIONS.get(str(st.session_state.get(f"buy-plan-type:{symbol}") or ""), ""),
        "max_position_pct": st.session_state.get(f"buy-plan-max-pct:{symbol}"),
        "target_position_pct": st.session_state.get(f"buy-plan-max-pct:{symbol}"),
        "target_sell_price": st.session_state.get(f"buy-plan-target-sell:{symbol}"),
        "invalidation_condition": _buy_plan_invalidation_value(symbol),
        "follow_up_plan": st.session_state.get(f"buy-plan-follow-up:{symbol}"),
        "thesis": st.session_state.get(f"buy-plan-thesis:{symbol}"),
        "buy_plan_tranches": levels,
        "event_name": st.session_state.get(f"buy-plan-event-name:{symbol}"),
        "event_date": st.session_state.get(f"buy-plan-event-date:{symbol}"),
        "exit_if_no_reaction": st.session_state.get(f"buy-plan-exit-no-reaction:{symbol}"),
        "stop_loss_price": st.session_state.get(f"buy-plan-stop-loss:{symbol}"),
        "notes": st.session_state.get(f"buy-plan-notes:{symbol}"),
    }
    _validate_buy_plan_form_values(symbol, values)
    return store.save_plan(symbol, values)


def _validate_buy_plan_form_values(symbol: str, values: dict) -> None:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        raise ValueError("股票代码无效。")
    if str(values.get("position_class") or "").strip().upper() not in {"A", "B", "C"}:
        raise ValueError("持仓等级必须选择 A / B / C。")
    if str(values.get("plan_type") or "").strip() not in {"starter_position", "ladder_buy", "event_trade", "watch_only"}:
        raise ValueError("计划类型无效。")
    max_pct = _number(values.get("max_position_pct"))
    if max_pct is None or max_pct <= 0:
        raise ValueError("最大仓位不能为空，且必须大于 0。")
    if not str(values.get("invalidation_condition") or "").strip():
        raise ValueError("失效条件不能为空。")
    levels = values.get("buy_plan_tranches") or []
    if not levels:
        raise ValueError("至少需要 1 个有效档位。")
    for index, item in enumerate(levels, start=1):
        price = _number(item.get("price") or item.get("trigger_price"))
        shares = _number(item.get("shares") or item.get("planned_quantity"))
        if price is None or price <= 0:
            raise ValueError(f"第 {index} 档触发价必须大于 0。")
        if shares is None or shares <= 0:
            raise ValueError(f"第 {index} 档计划股数必须大于 0。")


def _buy_plan_invalidation_value(symbol: str) -> str:
    choice = str(st.session_state.get(f"buy-plan-invalidation-choice:{symbol}") or "").strip()
    note = str(st.session_state.get(f"buy-plan-invalidation-note:{symbol}") or "").strip()
    if choice == "自定义":
        return note
    if note and note != choice:
        return f"{choice}：{note}"
    return choice


def _prefill_buy_form_from_plan(symbol: str, plan: dict, level: dict) -> None:
    form_key = _position_form_key(symbol)
    st.session_state["portfolio_position_editor_open"] = True
    st.session_state["portfolio_edit_symbol"] = symbol
    st.session_state[f"{form_key}:quantity"] = _input_value(level.get("remaining_quantity") or level.get("planned_quantity"))
    st.session_state[f"{form_key}:price"] = _input_value(level.get("trigger_price"))
    st.session_state[f"{form_key}:target_sell_price"] = _input_value(plan.get("target_sell_price"))
    st.session_state[f"{form_key}:position_tier"] = _position_tier_form_label(plan.get("position_class"))
    st.session_state[f"{form_key}:decision_mood"] = "计划内执行"
    st.session_state[f"{form_key}:buy_reason"] = f"执行买入计划：{level.get('label') or '计划档位'}"


def _save_buy_plan_pause_note(symbol: str, plan: dict, reason: str, detail: str) -> None:
    notes = str(plan.get("notes") or "").strip()
    line = f"暂缓 / 不买：{reason}"
    if str(detail or "").strip():
        line += f"；{str(detail).strip()}"
    plan["notes"] = "\n".join(item for item in (notes, line) if item)
    plan["material_updated_at"] = plan.get("material_updated_at") or plan.get("updated_at") or plan.get("created_at")
    StockPlanStore().save_plan(symbol, plan)


def _position_tier_form_label(tier: object) -> str:
    clean = str(tier or "").strip().upper()
    return next((label for label, value in POSITION_TIER_FORM_OPTIONS.items() if value == clean), list(POSITION_TIER_FORM_OPTIONS)[0])


def _default_buy_plan_type_for_tier(tier: object) -> str:
    clean = str(tier or "").strip().upper()
    if clean == "A":
        return "starter_position"
    if clean == "C":
        return "event_trade"
    return "ladder_buy"


def _default_buy_plan_max_pct(tier: object) -> int:
    return BUY_PLAN_DEFAULT_MAX_PCT.get(str(tier or "").strip().upper(), 8)


def _plan_type_label(plan_type: object) -> str:
    clean = str(plan_type or "").strip()
    return next((label for label, value in BUY_PLAN_TYPE_OPTIONS.items() if value == clean), "分批买入")


def _buy_plan_type_display(plan_type: object) -> str:
    return _plan_type_label(plan_type) if str(plan_type or "").strip() else "暂无计划"


def _invalidation_choice(value: object) -> str:
    text = str(value or "").strip()
    if text in BUY_PLAN_INVALIDATION_OPTIONS:
        return text
    return "自定义" if text else BUY_PLAN_INVALIDATION_OPTIONS[0]


def _buy_plan_cooldown_status(plan: dict, *, now: datetime | None = None) -> dict:
    created_at = _parse_plan_datetime(plan.get("created_at"))
    material_updated_at = _parse_plan_datetime(plan.get("material_updated_at")) or _parse_plan_datetime(plan.get("updated_at"))
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    candidates = [value for value in (created_at, material_updated_at) if value is not None]
    if not candidates:
        return {"met": True, "fresh": False, "remaining_minutes": None, "plan_age_minutes": None, "label": "计划时间缺失，仅供复盘"}
    effective = max(candidates)
    age_minutes = max(0.0, (current - effective).total_seconds() / 60)
    if age_minutes < FRESH_PLAN_REVIEW_MINUTES:
        return {
            "met": True,
            "fresh": True,
            "remaining_minutes": 0,
            "plan_age_minutes": round(age_minutes, 2),
            "label": "临时计划执行标记",
        }
    return {
        "met": True,
        "fresh": False,
        "remaining_minutes": 0,
        "plan_age_minutes": round(age_minutes, 2),
        "label": "计划时间已记录",
    }


def _buy_plan_can_be_gate_evidence(plan: dict, status: dict) -> bool:
    return bool(
        status.get("status") in {"triggered", "near_trigger"}
        and str(plan.get("thesis") or "").strip()
        and str(plan.get("invalidation_condition") or "").strip()
    )


def _parse_plan_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _render_position_tier_editor(position_store: PortfolioPositionStore, rows: list[dict]) -> None:
    active_rows = [row for row in rows if str(row.get("symbol") or "").strip()]
    if not active_rows:
        return
    with st.expander("编辑持仓属性", expanded=False):
        st.caption("这里只能修改 A/B/C 持仓属性，不会改变持股数量、成本或仓位。")
        symbols = [str(row.get("symbol") or "").strip().upper() for row in active_rows]
        selected = st.selectbox("持仓", symbols, key="portfolio-tier-edit-symbol")
        row = next((item for item in active_rows if str(item.get("symbol") or "").strip().upper() == selected), {})
        current_tier = str(row.get("positionTier") or "").strip().upper()
        tier_labels = [label for label, value in POSITION_TIER_FORM_OPTIONS.items() if value]
        current_label = next(
            (label for label, value in POSITION_TIER_FORM_OPTIONS.items() if value == current_tier),
            tier_labels[0],
        )
        selected_label = st.selectbox(
            "持仓等级",
            tier_labels,
            index=tier_labels.index(current_label),
            key="portfolio-tier-edit-tier",
        )
        if st.button("保存持仓等级", key="portfolio-tier-edit-save", width="stretch"):
            try:
                position_store.update_position_tier(selected, POSITION_TIER_FORM_OPTIONS[selected_label])
            except ValueError as exc:
                st.session_state["portfolio_save_notice"] = ("error", str(exc))
            else:
                st.session_state["portfolio_save_notice"] = ("success", f"{selected} 持仓等级已更新。")
            st.rerun()


def _available_watchlist_symbols(active_symbols: list[str]) -> list[str]:
    active = {symbol.upper() for symbol in active_symbols}
    return [symbol for symbol in load_watchlist() if symbol.upper() not in active]


def _position_form_key(selected: str) -> str:
    safe = "".join(ch for ch in str(selected or "new").upper() if ch.isalnum() or ch in {"-", "_"})
    return f"portfolio-position-form:{safe or 'NEW'}"


def _form_value(form_key: str, field: str) -> object:
    return st.session_state.get(f"{form_key}:{field}")


def _form_position_tier(form_key: str) -> str:
    selected = str(_form_value(form_key, "position_tier") or "").strip()
    return POSITION_TIER_FORM_OPTIONS.get(selected, selected).strip().upper()


def _action_group_tone(key: object) -> str:
    return {
        "addable": "green",
        "hold": "neutral",
        "nearTrim": "yellow",
        "overweight": "red",
        "review": "yellow",
    }.get(str(key), "neutral")


def _cell_html(primary: object, secondary: object) -> str:
    return (
        '<div class="portfolio-cell">'
        f"<b>{escape(str(primary or BLANK_TEXT))}</b>"
        f"<small>{escape(str(secondary or BLANK_TEXT))}</small>"
        "</div>"
    )


def _quantity_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:,.4g}"


def _share_count_text(value: object) -> str:
    quantity = _quantity_text(value)
    if quantity == BLANK_TEXT:
        return quantity
    return f"{quantity} 股"


def _percent_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return format_percent(number)


def _percent_plain(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return f"{number:g}%"


def _money_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return BLANK_TEXT
    return format_currency(number)


def _price_status_text(value: object) -> str:
    return {
        "quote_snapshot": "实时报价",
        "price_history": "历史收盘价",
        "provided": "手动价格",
        "missing": "缺少价格",
    }.get(str(value), "缺少价格")


def _system_action_text(row: dict) -> str:
    lane = str(row.get("decisionLane") or "").strip()
    action = str(row.get("systemAction") or "").strip()
    if row.get("overweightSystem"):
        return "超系统上限"
    if lane == "review":
        return "待复核"
    if lane == "blocked":
        return "禁止追高"
    if lane == "actionable":
        return "可加仓"
    if lane == "wait":
        return "只观察"
    if "观察" in action or "只" in action:
        return "只观察"
    return action or "未生成"


def _system_reason_text(row: dict) -> str:
    deviation = _deviation_text(row)
    if deviation != "暂无偏离提示":
        return deviation
    reasons = [*_translated_reasons(row.get("blockReasons")), *_translated_reasons(row.get("reviewReasons"))]
    if reasons:
        return "，".join(reasons[:2])
    add = _percent_text(row.get("systemCurrentAdd"))
    return "当前可加 " + add if add != BLANK_TEXT else "无系统提示"


def _plan_status_text(row: dict) -> str:
    if row.get("nearTrimPrice"):
        return "接近减仓价"
    current = _number(row.get("currentPrice"))
    review = _number(row.get("reviewPrice"))
    if current is not None and review is not None and current <= review:
        return "触及复核线"
    if any(_money_text(row.get(key)) != BLANK_TEXT for key in ("plannedSellPrice", "firstTrimPrice", "secondTrimPrice", "reviewPrice")):
        return "已设置计划"
    return "未设置计划"


def _plan_sub_text(row: dict) -> str:
    sell = _money_text(row.get("plannedSellPrice"))
    first = _money_text(row.get("firstTrimPrice"))
    review = _money_text(row.get("reviewPrice"))
    if sell != BLANK_TEXT:
        return "卖出 " + sell
    if first != BLANK_TEXT:
        return "减仓 " + first
    if review != BLANK_TEXT:
        return "复核 " + review
    return "计划未设置"


def _row_status_text(row: dict) -> str:
    reconciliation = row.get("reconciliation") or {}
    if str(reconciliation.get("status") or "") in {"warning", "mismatch"}:
        return _reconciliation_reason_text(reconciliation)
    if int(row.get("unsyncedTradeCount") or 0) > 0:
        return "有未同步交易记录"
    deviation = _deviation_text(row)
    return deviation if deviation != "暂无偏离提示" else _price_status_text(row.get("priceStatus"))


def _trade_sync_text(row: dict) -> str:
    count = int(row.get("unsyncedTradeCount") or 0)
    if count <= 0:
        return "已同步"
    return f"有 {count} 条未同步交易记录，请到交易日志处理"


def _safe_portfolio_reconciliation() -> list[dict]:
    try:
        return build_portfolio_reconciliation()
    except Exception:
        return []


def _attach_reconciliation(row: dict, reconciliation_by_symbol: dict[str, dict]) -> dict:
    symbol = str(row.get("symbol") or "").upper()
    return {**row, "reconciliation": reconciliation_by_symbol.get(symbol) or _empty_reconciliation(symbol)}


def _empty_reconciliation(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "status": "warning",
        "reasons": ["position_without_synced_journal"],
        "unsyncedTradeCount": 0,
        "quantityDiff": None,
        "costDiff": None,
        "positionQuantity": None,
        "journalQuantity": None,
        "positionAverageCost": None,
        "journalAverageCost": None,
    }


def _first_unsynced_reconciliation_symbol(items: list[dict]) -> str:
    for item in items:
        if int(item.get("unsyncedTradeCount") or 0) <= 0:
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if symbol:
            return symbol
    return ""


def _trade_journal_symbol_href(symbol: str) -> str:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return "?page=trade-journal#trade-journal-list"
    return f"?page=trade-journal&symbol={quote(ticker)}#trade-journal-list"


def _unsynced_trade_action_html(symbol: str) -> str:
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return ""
    href = _trade_journal_symbol_href(ticker)
    return (
        f'<a class="portfolio-reconciliation-action" '
        f'href="{escape(href, quote=True)}" target="_self">查看未同步交易</a>'
    )


def _drawer_raw_html(value: str) -> dict[str, str]:
    return {"__html": value}


def _drawer_value_html(value: object) -> str:
    if isinstance(value, dict) and "__html" in value:
        return str(value["__html"])
    return escape(str(value))


def _render_reconciliation_strip(items: list[dict]) -> None:
    summary = _reconciliation_summary(items)
    metrics = [
        ("一致", summary["ok"]),
        ("未同步交易", summary["unsynced"]),
        ("数量不一致", summary["quantityMismatch"]),
        ("成本不一致", summary["costMismatch"]),
        ("来源不明", summary["unknownSource"]),
    ]
    html = "".join(
        '<div class="portfolio-reconciliation-item">'
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(str(value))}</strong>"
        "</div>"
        for label, value in metrics
    )
    tone = "ok" if summary["problemCount"] == 0 else "warning" if summary["mismatch"] == 0 else "mismatch"
    action_html = _unsynced_trade_action_html(_first_unsynced_reconciliation_symbol(items))
    action_class = " has-action" if action_html else ""
    title = "账务一致" if summary["problemCount"] == 0 else "账务需复核"
    st.markdown(
        (
            f'<section class="portfolio-reconciliation-strip {escape(tone + action_class)}">'
            f'<div class="portfolio-reconciliation-title"><strong>{escape(title)}</strong><span>交易日志 / 当前持仓</span></div>'
            f'<div class="portfolio-reconciliation-grid">{html}</div>'
            f"{action_html}"
            "</section>"
        ),
        unsafe_allow_html=True,
    )


def _reconciliation_summary(items: list[dict]) -> dict[str, int]:
    ok = warning = mismatch = unsynced = quantity_mismatch = cost_mismatch = unknown_source = 0
    for item in items:
        status = str(item.get("status") or "")
        reasons = {str(reason) for reason in item.get("reasons") or []}
        if status == "ok":
            ok += 1
        elif status == "mismatch":
            mismatch += 1
        else:
            warning += 1
        if "unsynced_trades_exist" in reasons:
            unsynced += 1
        if "quantity_mismatch" in reasons or "synced_journal_without_active_position" in reasons:
            quantity_mismatch += 1
        if "average_cost_mismatch" in reasons:
            cost_mismatch += 1
        if "position_without_synced_journal" in reasons:
            unknown_source += 1
    return {
        "ok": ok,
        "warning": warning,
        "mismatch": mismatch,
        "unsynced": unsynced,
        "quantityMismatch": quantity_mismatch,
        "costMismatch": cost_mismatch,
        "unknownSource": unknown_source,
        "problemCount": warning + mismatch,
    }


def _reconciliation_status_text(item: dict | None) -> str:
    status = str((item or {}).get("status") or "warning")
    return {
        "ok": "账务一致",
        "warning": "账务需复核",
        "mismatch": "账务不一致",
    }.get(status, "账务需复核")


def _reconciliation_reason_text(item: dict | None) -> str:
    reasons = [str(reason) for reason in (item or {}).get("reasons") or []]
    labels = {
        "unsynced_trades_exist": "有未同步交易记录",
        "quantity_mismatch": "当前持仓数量和交易流水不一致",
        "average_cost_mismatch": "当前持仓成本和交易流水不一致",
        "position_without_synced_journal": "有持仓但找不到同步交易来源",
        "synced_journal_without_active_position": "交易流水有持仓但当前持仓未启用",
    }
    translated = [labels.get(reason, reason) for reason in reasons]
    return "；".join(translated) if translated else "交易日志和当前持仓一致"


def _reconciliation_drawer_items(item: dict | None) -> list[tuple[str, object]]:
    current = item or {}
    items = [
        ("状态", _reconciliation_status_text(current)),
        ("原因", _reconciliation_reason_text(current)),
        ("未同步交易", int(current.get("unsyncedTradeCount") or 0)),
        ("持仓数量 / 日志数量", _quantity_text(current.get("positionQuantity")) + " / " + _quantity_text(current.get("journalQuantity"))),
        ("数量差异", _quantity_text(current.get("quantityDiff"))),
        ("持仓成本 / 日志成本", _money_text(current.get("positionAverageCost")) + " / " + _money_text(current.get("journalAverageCost"))),
        ("成本差异", _money_text(current.get("costDiff"))),
    ]
    symbol = str(current.get("symbol") or "").strip().upper()
    if int(current.get("unsyncedTradeCount") or 0) > 0 and symbol:
        items.append(("处理入口", _drawer_raw_html(_unsynced_trade_action_html(symbol))))
    return items


def _decision_lane_text(value: object) -> str:
    return {
        "actionable": "可执行",
        "review": "需复核",
        "blocked": "阻断",
        "wait": "等待",
    }.get(str(value), "未生成")


def _buy_zone_status_text(value: object) -> str:
    return {
        "fair_observation": "观察区",
        "tranche_buy": "估值分批区",
        "heavy_buy": "估值重仓区",
        "below_heavy_buy": "低于重仓区",
        "no_chase": "禁止追高",
        "data_insufficient": "数据不足",
        "invalid_zone": "估值参考异常",
        "low_confidence_zone": "低置信估值参考",
    }.get(str(value), "未生成")


def _reason_text(value: object) -> str:
    reasons = _translated_reasons(value)
    return "，".join(reasons) if reasons else BLANK_TEXT


def _translated_reasons(value: object) -> list[str]:
    labels = {
        "buy_zone": "系统估值参考阻断",
        "data_confidence": "数据置信度",
        "valuation_status": "估值状态",
        "entry_rating": "入场评级",
        "risk_rating": "风险评级",
    }
    items = value if isinstance(value, list) else []
    return [labels.get(str(item), str(item)) for item in items]


def _trim_prices_text(row: dict) -> str:
    first = _money_text(row.get("firstTrimPrice"))
    second = _money_text(row.get("secondTrimPrice"))
    items = []
    if first != BLANK_TEXT:
        items.append("第一减仓 " + first)
    if second != BLANK_TEXT:
        items.append("第二减仓 " + second)
    return " / ".join(items) if items else "未设置"


def render() -> None:
    _render_styles()
    _render_final_portfolio_styles()
    render_page_header("组合持仓", "真实持仓、仓位偏离和下一步动作。")
    _consume_portfolio_edit_query()
    _render_portfolio_notice()

    position_store = PortfolioPositionStore()
    settings_store = PortfolioSettingsStore()
    plan_store = StockPlanStore()
    view = build_portfolio_view_model()
    settings = view["settings"]
    rows = view["rows"]
    reconciliation_rows = _safe_portfolio_reconciliation()
    reconciliation_by_symbol = {
        str(item.get("symbol") or "").upper(): item
        for item in reconciliation_rows
    }
    rows = [_attach_reconciliation(row, reconciliation_by_symbol) for row in rows]

    _render_overview_strip(view["summary"])
    _render_reconciliation_strip(reconciliation_rows)
    _render_action_panel(view["actionGroups"])
    _render_positions_table(rows, position_store, plan_store)
    _render_editor(position_store, settings_store, rows, settings)


def _consume_portfolio_edit_query() -> None:
    symbol = str(st.query_params.get("portfolioEdit") or "").strip().upper()
    plan_symbol = str(st.query_params.get("portfolioPlan") or "").strip().upper()
    if symbol:
        st.session_state["portfolio_position_editor_open"] = True
        st.session_state["portfolio_edit_symbol"] = symbol
        if "portfolioEdit" in st.query_params:
            st.query_params.pop("portfolioEdit")
    if plan_symbol:
        st.session_state["portfolio_plan_symbol"] = plan_symbol
        if "portfolioPlan" in st.query_params:
            st.query_params.pop("portfolioPlan")


def _render_portfolio_notice() -> None:
    notice = st.session_state.pop("portfolio_save_notice", None)
    if not notice:
        return
    level, message = notice
    if level == "buy_gate_blocked":
        st.markdown(_portfolio_buy_gate_notice_html(message), unsafe_allow_html=True)
        return
    if level == "error":
        st.error(str(message))
    else:
        st.success(str(message))


def _portfolio_buy_gate_notice_html(payload: object) -> str:
    data = dict(payload or {}) if isinstance(payload, dict) else {"symbol": "", "gate": {}}
    symbol = str(data.get("symbol") or "").strip().upper() or "该股票"
    gate = dict(data.get("gate") or {})
    plan_gate = dict(data.get("planGate") or {})
    starter_gate = dict(data.get("starterGate") or {})
    market_status = dict(data.get("marketStatus") or {})
    entry_mode = str(data.get("entryMode") or "").strip()
    tier = str(data.get("positionTier") or data.get("positionClass") or "").strip().upper()
    reasons = _portfolio_buy_gate_reasons(gate)
    plan_reasons = [] if entry_mode == "starter_position" else _portfolio_buy_plan_reasons(plan_gate)
    starter_reasons = _portfolio_starter_reasons(starter_gate)
    primary_reasons = starter_reasons if entry_mode == "starter_position" and starter_reasons else reasons
    primary_title = "底仓检查结果" if entry_mode == "starter_position" else "Radar 拦截原因"
    market_items = _portfolio_buy_market_status_items(market_status, gate)
    actions = _portfolio_buy_gate_actions(plan_reasons, starter_reasons, tier=tier)
    reason_html = "".join(f"<li>{escape(item)}</li>" for item in primary_reasons) or "<li>Radar / 买入门禁未通过。</li>"
    market_html = "".join(f"<li>{escape(item)}</li>" for item in market_items) or "<li>当前市场状态需复核。</li>"
    action_html = "".join(f"<li>{escape(item)}</li>" for item in actions)
    return (
        '<section class="portfolio-gate-notice">'
        '<div class="portfolio-gate-notice-head">'
        f"<strong>{escape(symbol)} 已保存日志，未同步持仓</strong>"
        "<span>这不是系统错误；真实组合持仓没有变化。</span>"
        "</div>"
        '<div class="portfolio-gate-notice-grid">'
        f"<div><b>{escape(primary_title)}</b><ul>{reason_html}</ul></div>"
        f"<div><b>当前市场状态</b><ul>{market_html}</ul></div>"
        f"<div><b>可修正动作</b><ul>{action_html}</ul></div>"
        "</div>"
        "</section>"
    )


def _portfolio_buy_gate_reasons(gate: dict) -> list[str]:
    raw_items = [*(gate.get("reasons") or []), *(gate.get("required_actions") or [])]
    reasons = [_portfolio_buy_gate_reason_text(item) for item in raw_items if str(item).strip()]
    allowed_add_pct = _number(gate.get("allowed_add_pct"))
    if allowed_add_pct is not None and allowed_add_pct <= 0:
        reasons.append("当前 Radar 允许新增仓位为 0%，本次买入不能同步到真实组合。")
    return _dedupe_text(reasons)


def _portfolio_buy_gate_reason_text(value: object) -> str:
    text = str(value or "").strip()
    lower = text.lower()
    mappings = [
        ("current price is above the discipline buy zone", "当前价高于纪律买入区。"),
        ("current price is in or above chase zone", "当前仍未进入纪律买入区。"),
        ("valuation score below 40", "估值评分低于 40，禁止高仓位。"),
        ("final score below 70", "综合评分低于 70，禁止核心仓。"),
        ("core position is not allowed", "不允许核心仓。"),
        ("heavy position is not allowed", "不允许高仓位。"),
        ("data missing", "数据缺失，不能同步真实买入。"),
        ("stale", "缓存已过期，不能作为真实买入依据。"),
        ("missing current price", "缺少当前价格。"),
        ("missing valuation", "缺少估值指标。"),
    ]
    for needle, label in mappings:
        if needle in lower:
            return label
    if "Radar" in text or "买入后仓位" in text or "情绪交易风险" in text or "仅观察记录" in text:
        return text
    return text or "Radar / 买入门禁未通过。"


def _portfolio_buy_market_status_items(market_status: dict, gate: dict) -> list[str]:
    items: list[str] = []
    for key in ("technical_status", "valuation_status", "discipline_status"):
        value = str(market_status.get(key) or "").strip()
        if value:
            items.append(value)
    for note in market_status.get("notes") or []:
        if str(note).strip():
            items.append(str(note).strip())
    if not items:
        allowed_add_pct = _number(gate.get("allowed_add_pct"))
        if allowed_add_pct is not None and allowed_add_pct <= 0:
            items.append("当前允许新增仓位为 0%。")
        items.append("价格到达或下跌不等于自动可以买。")
    return _dedupe_text(items)


def _portfolio_buy_gate_actions(
    plan_reasons: list[str] | None = None,
    starter_reasons: list[str] | None = None,
    *,
    tier: object = "",
) -> list[str]:
    clean_tier = str(tier or "").strip().upper()
    actions = ["等待回到纪律买入区。"]
    if clean_tier == "A":
        actions.extend(["建立 A 类底仓计划。", "建立分批买入计划。"])
    elif clean_tier == "B":
        actions.extend(
            [
                "建立 B 类分批买入计划。",
                "如果你认为它应是 A 类，请改为 A 类并创建 A 类底仓计划。",
            ]
        )
    elif clean_tier == "C":
        actions.extend(["建立 C 类事件交易计划。", "建立小仓观察计划。"])
    else:
        actions.append("先选择 A/B/C 持仓等级，再创建对应买入计划。")
    actions.extend(
        [
            "降低买入数量，直到买入后仓位不超过 Radar 上限。",
            "改为仅观察记录，不同步真实组合。",
            "重新复核该股票的 Radar 区间和买入计划。",
        ]
    )
    context_items = [*(plan_reasons or []), *(starter_reasons or [])]
    for item in context_items:
        if item and item not in actions:
            actions.append(item)
    return _dedupe_text(actions)


def _portfolio_buy_plan_reasons(plan_gate: dict) -> list[str]:
    status = str(plan_gate.get("plan_match_status") or "").strip()
    labels = {
        "no_plan": "未找到分批买入计划。",
        "not_triggered": "当前价尚未触发下一档计划买入价。",
        "quantity_exceeds_level": "买入数量超过计划档位剩余数量。",
        "position_exceeds_plan": "买入后仓位超过计划上限。",
        "mood_blocked": "交易心理不符合计划内执行。",
        "data_missing": "价格或 Radar 数据缺失 / 过期，不能按计划同步。",
        "price_missing": "缺少当前价格，不能匹配计划。",
        "quantity_missing": "买入数量无效，不能匹配计划档位。",
        "level_filled": "该计划档位已没有剩余可买数量。",
        "plan_created_too_late": "计划创建时间较近；现在仅作为复盘标记，不再硬拦截。",
        "plan_modified_too_late": "计划修改时间较近；现在仅作为复盘标记，不再硬拦截。",
        "plan_timestamp_missing": "计划时间戳缺失；仅影响复盘标记，不作为硬拦截。",
        "plan_cooldown_not_met": "计划刚创建或刚修改；现在仅作为复盘标记，不再硬拦截。",
        "missing_position_limit": "计划缺少买后仓位上限。",
        "missing_exit_condition": "计划缺少失效条件 / 退出条件。",
        "valuation_review_required": "估值评分低于 40，计划缺少复核说明。",
        "allow_planned_add": "已匹配计划内分批买入档位。",
    }
    reasons = [labels.get(status, status)] if status else []
    reasons.extend(str(item) for item in (plan_gate.get("plan_block_reasons") or []) if str(item).strip())
    return _dedupe_text(reasons)


def _portfolio_starter_reasons(starter_gate: dict) -> list[str]:
    status = str(starter_gate.get("starter_match_status") or "").strip()
    labels = {
        "not_selected": "",
        "starter_blocked": "A 类底仓建仓条件未通过。",
        "starter_review_required": "估值评分过低，底仓建仓需要复核，不能直接同步。",
        "allow_starter_position": "已匹配 A 类底仓建仓条件。",
    }
    reasons = [labels.get(status, status)] if labels.get(status, status) else []
    reasons.extend(str(item) for item in (starter_gate.get("starter_block_reasons") or []) if str(item).strip())
    return _dedupe_text(reasons)


def _dedupe_text(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _render_overview_strip(summary: dict) -> None:
    items = [
        ("持仓数", str(summary.get("positionCount", 0)), "active"),
        ("总市值", _money_or_dash(summary.get("marketValue"), zero_dash=True), "market value"),
        ("总成本", _money_or_dash(summary.get("costBasis"), zero_dash=True), "cost basis"),
        ("浮动盈亏", _money_or_dash(summary.get("unrealizedPnl")), _percent_or_dash(summary.get("unrealizedPnlPct"))),
        ("组合基准", _money_or_dash(summary.get("totalPortfolioValue"), zero_dash=True), "manual total"),
        ("现金", _money_or_dash(summary.get("cashBalance")), "auto cash"),
    ]
    html = "".join(
        '<div class="portfolio-stat compact">'
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        f"<em>{escape(detail)}</em>"
        "</div>"
        for label, value, detail in items
    )
    st.markdown(f'<div class="portfolio-overview compact">{html}</div>', unsafe_allow_html=True)


def _render_action_panel(action_groups: list[dict]) -> None:
    lanes = []
    for group in action_groups:
        key = str(group.get("key") or "")
        label = str(group.get("label") or _lane_label(key))
        symbols = [str(symbol) for symbol in (group.get("symbols") or [])]
        content = "，".join(symbols[:4]) + ("…" if len(symbols) > 4 else "") if symbols else "暂无"
        lanes.append(
            f'<div class="portfolio-lane tone-{escape(_action_group_tone(key))}">'
            f"<span>{escape(label)}</span>"
            f"<b>{escape(str(group.get('count') or 0))}</b>"
            f"<small>{escape(content)}</small>"
            "</div>"
        )
    st.markdown(
        '<div class="portfolio-radar-head">'
        "<strong>组合风险雷达</strong>"
        "<span>按持仓状态聚合下一步动作</span>"
        "</div>"
        f'<div class="portfolio-lanes">{"".join(lanes)}</div>',
        unsafe_allow_html=True,
    )


def _render_positions_table(rows: list[dict], position_store: PortfolioPositionStore, plan_store: StockPlanStore) -> None:
    title_cols = st.columns([5, 1])
    with title_cols[0]:
        render_section_title("持仓清单", "状态优先，详情从右侧查看。")
    with title_cols[1]:
        st.write("")
        if st.button("添加持仓", key="portfolio-list-add", width="stretch"):
            st.session_state["portfolio_position_editor_open"] = True
            st.rerun()

    if not rows:
        st.markdown(
            '<div class="portfolio-empty">'
            "<div>暂无持仓</div>"
            "<span>添加第一只股票后，这里会显示仓位、盈亏、系统估值参考和计划状态。</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    headers = ["股票", "持仓 / 成本", "现价 / 盈亏", "仓位 / 上限", "系统估值参考", "我的计划", "操作"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row, plan_store) for row in rows)
    decision_store = DecisionLogStore()
    trade_store = TradeJournalStore()
    drawer_html = "".join(_drawer_html(row, plan_store, decision_store, trade_store) for row in rows)
    colgroup = (
        '<colgroup>'
        '<col class="portfolio-col-symbol">'
        '<col class="portfolio-col-cost">'
        '<col class="portfolio-col-pnl">'
        '<col class="portfolio-col-weight">'
        '<col class="portfolio-col-system">'
        '<col class="portfolio-col-plan">'
        '<col class="portfolio-col-actions">'
        "</colgroup>"
    )
    st.markdown(
        '<div id="portfolio-table"></div>'
        '<div class="portfolio-table-wrap terminal">'
        '<table class="portfolio-table terminal">'
        f"{colgroup}"
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>"
        f"{drawer_html}",
        unsafe_allow_html=True,
    )


def _position_row_html(row: dict, plan_store: StockPlanStore | None = None) -> str:
    symbol = str(row.get("symbol") or "")
    drawer_id = _drawer_id(symbol)
    research_href = f"?page=detail&symbol={quote(symbol)}"
    add_href = f"?page=portfolio&portfolioEdit={quote(symbol)}#portfolio-trade-entry"
    plan_href = f"?page=portfolio&portfolioPlan={quote(symbol)}#portfolio-buy-plan"
    return (
        "<tr>"
        f'<td class="portfolio-symbol-cell">{_symbol_cell_html(row)}</td>'
        f"<td>{_cell_html(_share_count_text(row.get('quantity')), '成本 ' + _money_text(row.get('costBasis')) + ' / 均价 ' + _money_text(row.get('averageCost')))}</td>"
        f"<td>{_cell_html(_money_text(row.get('currentPrice')), _money_text(row.get('unrealizedPnl')) + ' / ' + _percent_text(row.get('unrealizedPnlPct')))}</td>"
        f"<td>{_cell_html(_percent_text(row.get('positionPct')), '系统 ' + _percent_text(row.get('systemMaxPosition')) + ' / 个人 ' + _percent_text(row.get('maxAcceptablePositionPct')))}</td>"
        f"<td>{_system_cell_html(row)}</td>"
        f"<td>{_plan_cell_html(row, plan_store)}</td>"
        '<td><div class="portfolio-row-actions">'
        f'<a class="portfolio-view-link" href="{escape(add_href, quote=True)}" target="_self">加仓</a>'
        f'<a class="portfolio-view-link" href="{escape(plan_href, quote=True)}" target="_self">计划</a>'
        f'<a class="portfolio-view-link" href="#{escape(drawer_id)}">查看</a>'
        f'<a class="portfolio-view-link portfolio-research-link" href="{escape(research_href, quote=True)}" target="_self">研究</a>'
        "</div></td>"
        "</tr>"
    )


def _symbol_cell_html(row: dict) -> str:
    symbol = str(row.get("symbol") or "")
    tier = row.get("positionTier")
    return (
        '<div class="portfolio-symbol-stack">'
        f"<b>{escape(symbol)}</b>"
        f"<small>{escape(_row_status_text(row))}</small>"
        f"{_position_tier_badge_html(tier)}"
        "</div>"
    )


def _position_tier_badge_html(tier: object) -> str:
    return (
        f'<em class="portfolio-tier-badge {escape(position_tier_badge_class(tier))}">'
        f"{escape(format_position_tier_label(tier))}"
        "</em>"
    )


def _current_detail_symbol(symbols: list[str]) -> str:
    selected = str(st.session_state.get("portfolio-drawer-action-symbol") or "").strip().upper()
    if selected in symbols:
        return selected
    return symbols[0]


def _system_cell_html(row: dict) -> str:
    tone = _system_tone_class(row)
    return (
        f'<div class="portfolio-system-cell {escape(tone)}">'
        f"<b>{escape(_system_action_text(row))}</b>"
        f"<small>{escape(_system_reason_short(row))}</small>"
        "</div>"
    )


def _plan_cell_html(row: dict, plan_store: StockPlanStore | None = None) -> str:
    status = _buy_plan_status_for_row(row, plan_store)
    label = str(status.get("label") or _plan_status_text(row))
    tone = "is-empty" if status.get("status") == "no_plan" else "is-set"
    return (
        f'<div class="portfolio-plan-cell {escape(tone)}">'
        f"<b>{escape(label)}</b>"
        f"<small>{escape(_buy_plan_status_sub_text(row, status))}</small>"
        "</div>"
    )


def _buy_plan_status_for_row(row: dict, plan_store: StockPlanStore) -> dict:
    symbol = str(row.get("symbol") or "").strip().upper()
    if not symbol or plan_store is None:
        return {"status": "no_plan", "label": "暂无计划", "message": ""}
    try:
        plan = plan_store.get_plan(symbol)
        return get_buy_plan_status(plan, current_price=row.get("currentPrice"), is_stale=False)
    except Exception:
        return {"status": "needs_review", "label": "需复核", "message": "计划读取失败"}


def _buy_plan_status_sub_text(row: dict, status: dict) -> str:
    if status.get("status") == "no_plan":
        return "创建买入计划"
    level = status.get("level") or {}
    trigger = _money_text(level.get("trigger_price"))
    remaining = _quantity_text(level.get("remaining_quantity"))
    if trigger != BLANK_TEXT:
        return f"{trigger} / 剩余 {remaining}"
    fallback = _plan_sub_text(row)
    return fallback if fallback != "未设置" else str(status.get("message") or "")


def _system_tone_class(row: dict) -> str:
    if row.get("overweightSystem"):
        return "tone-risk"
    lane = str(row.get("decisionLane") or "").strip()
    if lane == "actionable":
        return "tone-green"
    if lane == "review":
        return "tone-amber"
    if lane == "blocked":
        return "tone-risk"
    return "tone-neutral"


def _system_reason_short(row: dict) -> str:
    reason = _system_reason_text(row)
    if "，" in reason:
        return reason.split("，", 1)[0]
    if len(reason) > 20:
        return reason[:20] + "…"
    return reason


def _drawer_html(
    row: dict,
    plan_store: StockPlanStore,
    decision_store: DecisionLogStore,
    trade_store: TradeJournalStore,
) -> str:
    symbol = str(row.get("symbol") or "")
    drawer_id = _drawer_id(symbol)
    edit_href = f"?page=portfolio&portfolioEdit={quote(symbol)}#portfolio-table"
    research_notes = _research_notes(symbol, plan_store)
    signal_items = _recent_signal_items(symbol, decision_store)
    trade_items = _recent_trade_items(symbol, trade_store)
    discipline_items = _trading_discipline_items(row)
    sections = [
        ("持仓摘要", [
            ("持股数量", _quantity_text(row.get("quantity"))),
            ("平均成本", _money_text(row.get("averageCost"))),
            ("现价", _money_text(row.get("currentPrice"))),
            ("价格状态", _price_status_text(row.get("priceStatus"))),
            ("市值", _money_text(row.get("marketValue"))),
            ("浮动盈亏", _money_text(row.get("unrealizedPnl")) + " / " + _percent_text(row.get("unrealizedPnlPct"))),
            ("当前仓位", _percent_text(row.get("positionPct"))),
            ("交易同步", _trade_sync_text(row)),
        ]),
        ("账务一致性", _reconciliation_drawer_items(row.get("reconciliation"))),
        ("系统估值参考", [
            ("怎么看", _system_explanation_text(row)),
            ("系统动作", _system_action_text(row)),
            ("系统上限", _percent_text(row.get("systemMaxPosition"))),
            ("当前可加", _percent_text(row.get("systemCurrentAdd"))),
            ("决策通道", _decision_lane_text(row.get("decisionLane"))),
            ("估值参考状态", _buy_zone_status_text(row.get("buyZoneStatus"))),
            ("阻断原因", _reason_text(row.get("blockReasons"))),
            ("复核原因", _reason_text(row.get("reviewReasons"))),
        ]),
        ("交易纪律", discipline_items),
        ("最近信号", signal_items),
        ("最近操作", trade_items),
        ("研究备忘录", [
            ("备忘录", research_notes),
        ]),
        ("我的计划", [
            ("计划状态", _plan_status_text(row)),
            ("计划卖出价", _money_text(row.get("plannedSellPrice"))),
            ("减仓价", _trim_prices_text(row)),
            ("复核线", _money_text(row.get("reviewPrice"))),
            ("备注", row.get("notes") or "未填写"),
        ]),
        ("偏离提示", [
            ("提醒", _deviation_text(row)),
        ]),
    ]
    body = "".join(_drawer_section_html(title, items) for title, items in sections)
    return (
        f'<aside id="{escape(drawer_id)}" class="portfolio-drawer">'
        '<a class="portfolio-drawer-backdrop" href="#portfolio-table"></a>'
        '<div class="portfolio-drawer-panel">'
        '<div class="portfolio-drawer-head">'
        f"<div><strong>{escape(symbol)}</strong><span>{escape(_system_action_text(row))} · {escape(_plan_status_text(row))}</span></div>"
        '<div class="portfolio-drawer-actions">'
        f'<a href="{escape(edit_href, quote=True)}" target="_self">编辑持仓</a>'
        '<a href="#portfolio-table">关闭</a>'
        "</div>"
        "</div>"
        f"{body}"
        "</div>"
        "</aside>"
    )


def _drawer_section_html(title: str, items: list[tuple[str, object]]) -> str:
    rows = "".join(
        f"<span>{escape(label)}</span><b>{_drawer_value_html(value)}</b>"
        for label, value in items
    )
    return f'<section class="portfolio-drawer-section"><h4>{escape(title)}</h4><div>{rows}</div></section>'


def _trading_discipline_items(row: dict) -> list[tuple[str, object]]:
    symbol = str(row.get("symbol") or "").upper()
    position_class = _position_class_for_row(row)
    if position_class not in {"A", "B", "C"}:
        return [
            ("鑲＄エ鍒嗙被", format_position_tier_label(row.get("positionTier"))),
            ("绾緥鎻愰啋", "请先编辑持仓等级；A/B/C 是手动持仓属性，不按股票或仓位自动猜测。"),
        ]
    config = load_trading_discipline_config()
    class_rules = dict(config.get("position_classes", {}).get(position_class, {}))
    core_pct = _number(class_rules.get("core_position_pct")) or 0.0
    trading_pct = _number(class_rules.get("trading_position_pct"))
    if trading_pct is None:
        trading_pct = max(0.0, 1.0 - core_pct)
    macro_check = evaluate_trading_discipline(
        symbol=symbol,
        positionClass=position_class,
        corePositionPct=core_pct,
        tradingPositionPct=trading_pct,
        unrealizedGainPct=_number(row.get("unrealizedPnlPct")),
        plannedAction="sell",
        plannedSellPct=1.0,
        sellReasonType="macro",
        thesisBroken=False,
        positionOverLimit=bool(row.get("overweightSystem") or row.get("overweightPersonal")),
        hasReentryPlan=False,
        config=config,
    )
    trim_check = evaluate_trading_discipline(
        symbol=symbol,
        positionClass=position_class,
        corePositionPct=core_pct,
        tradingPositionPct=trading_pct,
        unrealizedGainPct=_number(row.get("unrealizedPnlPct")),
        plannedAction="trim",
        plannedSellPct=min(max(trading_pct, 0.0), 0.1),
        sellReasonType="technical",
        thesisBroken=False,
        positionOverLimit=bool(row.get("overweightSystem") or row.get("overweightPersonal")),
        hasReentryPlan=False,
        config=config,
    )
    can_sell_core = "允许" if macro_check.canSellCore else "不允许，除非投资逻辑已确认破裂"
    requires_reentry = "需要" if trim_check.requiresReentryPlan else "不需要"
    return [
        ("股票分类", POSITION_CLASS_LABELS.get(position_class, position_class)),
        ("核心仓比例", format_percent(core_pct, already_percent=False)),
        ("交易仓比例", format_percent(trading_pct, already_percent=False)),
        ("允许卖核心仓", can_sell_core),
        ("需要回补计划", requires_reentry),
        ("纪律提醒", _discipline_reminder_text(row, macro_check, trim_check)),
    ]


def _position_class_for_row(row: dict) -> str:
    tier = str(row.get("positionTier") or "").strip().upper()
    return tier if tier in {"A", "B", "C"} else ""


def _discipline_reminder_text(row: dict, macro_check, trim_check) -> str:
    position_class = _position_class_for_row(row)
    if macro_check.blockers:
        return "宏观恐慌不能作为清仓理由；先处理交易仓，核心仓只在投资逻辑破裂后复核。"
    if trim_check.requiresReentryPlan:
        return "技术或估值减仓前先写回补条件，避免卖飞后没有再入场计划。"
    if position_class == "C":
        return "交易仓可更灵活，但仍按计划减仓，不用情绪替代规则。"
    return str(macro_check.reminderText or "继续按持仓纪律执行，不做情绪化卖出。")


def _research_notes(symbol: str, plan_store: StockPlanStore) -> str:
    try:
        notes = str(plan_store.get_plan(symbol).get("notes") or "").strip()
    except Exception:
        notes = ""
    return notes or "暂无研究备忘录"


def _recent_signal_items(symbol: str, decision_store: DecisionLogStore) -> list[tuple[str, object]]:
    try:
        snapshot = (decision_store.list_snapshots(symbol) or [None])[0]
    except Exception:
        snapshot = None
    if not snapshot:
        return [("状态", "暂无系统信号")]
    return [
        ("系统动作", _snapshot_action_text(snapshot.get("final_action"))),
        ("决策通道", _decision_lane_text(snapshot.get("decision_lane"))),
        ("信号价格", _money_text(snapshot.get("price"))),
        ("信号日期", snapshot.get("decision_date") or BLANK_TEXT),
        ("主要原因", _snapshot_reason_text(snapshot)),
    ]


def _recent_trade_items(symbol: str, trade_store: TradeJournalStore) -> list[tuple[str, object]]:
    try:
        entry = (trade_store.list_entries(symbol) or [None])[0]
    except Exception:
        entry = None
    if not entry:
        return [("状态", "暂无操作记录")]
    return [
        ("操作类型", _trade_action_text(entry.get("action_type"))),
        ("日期", entry.get("trade_date") or BLANK_TEXT),
        ("价格", _money_text(entry.get("price"))),
        ("数量", _quantity_text(entry.get("quantity"))),
        ("备注", entry.get("notes") or "未填写"),
    ]


def _snapshot_action_text(value: object) -> str:
    text = str(value or "").strip()
    return {
        "add": "加仓",
        "buy": "买入",
        "wait": "等待",
        "review": "复核",
        "blocked": "阻断",
        "unknown": "未标记",
    }.get(text, text or BLANK_TEXT)


def _trade_action_text(value: object) -> str:
    text = str(value or "").strip()
    return TRADE_ACTION_LABELS.get(text, text or BLANK_TEXT)


def _snapshot_reason_text(snapshot: dict) -> str:
    reasons = [*_translated_reasons(snapshot.get("block_reasons")), *_translated_reasons(snapshot.get("review_reasons"))]
    if reasons:
        return "，".join(reasons[:2])
    raw_text = str(snapshot.get("reason_text") or "").strip()
    if not raw_text:
        return "暂无主要原因"
    raw_items = [item.strip() for item in raw_text.replace("；", ";").split(";") if item.strip()]
    translated = _translated_reasons(raw_items)
    return "，".join(translated[:2]) if translated else raw_text


def _system_explanation_text(row: dict) -> str:
    lane = str(row.get("decisionLane") or "").strip()
    max_position = _percent_text(row.get("systemMaxPosition"))
    current_add = _percent_text(row.get("systemCurrentAdd"))
    reason = _main_reason_text(row)
    if lane == "actionable":
        return f"系统允许新增，当前可加 {current_add}，系统仓位上限 {max_position}。"
    if lane == "review":
        return f"系统建议先复核；主要原因：{reason}。"
    if lane == "blocked":
        return f"系统当前阻断新增；主要原因：{reason}。"
    if lane == "wait":
        return f"系统建议等待，不急于新增；主要原因：{reason}。"
    return "系统估值参考不足，先按手动计划管理。"


def _main_reason_text(row: dict) -> str:
    warnings = _deviation_items(row)
    if warnings:
        return "，".join(warnings[:2])
    reasons = [*_translated_reasons(row.get("blockReasons")), *_translated_reasons(row.get("reviewReasons"))]
    return "，".join(reasons[:2]) if reasons else "暂无明确阻断/复核原因"


def _deviation_text(row: dict) -> str:
    items = _deviation_items(row)
    return "，".join(items) if items else "暂无偏离提示"


def _deviation_items(row: dict) -> list[str]:
    items: list[str] = []
    if row.get("overweightSystem"):
        items.append("超系统上限")
    if row.get("overweightPersonal"):
        items.append("超个人上限")
    if row.get("nearTrimPrice"):
        items.append("接近减仓价")
    if _review_line_touched(row):
        items.append("触及复核线")
    if _system_review_with_position(row):
        items.append("系统建议复核但仍有仓位")
    return items


def _review_line_touched(row: dict) -> bool:
    current = _number(row.get("currentPrice"))
    review = _number(row.get("reviewPrice"))
    return current is not None and review is not None and current <= review


def _system_review_with_position(row: dict) -> bool:
    quantity = _number(row.get("quantity"))
    lane = str(row.get("decisionLane") or "").strip()
    return quantity is not None and quantity > 0 and lane == "review"


def _drawer_id(symbol: str) -> str:
    safe = "".join(ch for ch in str(symbol).upper() if ch.isalnum() or ch in {"-", "_"})
    return f"portfolio-drawer-{safe or 'position'}"


def _money_or_dash(value: object, zero_dash: bool = False) -> str:
    number = _number(value)
    if number is None or (zero_dash and number == 0):
        return "—"
    return format_currency(number)


def _percent_or_dash(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return format_percent(number)


def _lane_label(key: str) -> str:
    return {
        "addable": "可加仓",
        "hold": "持有观察",
        "nearTrim": "接近减仓价",
        "overweight": "超仓位",
        "review": "需复核",
    }.get(key, key)


def _render_final_portfolio_styles() -> None:
    st.markdown(
        """
        <style>
        .portfolio-overview.compact {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0;
            margin: 0.35rem 0 0.75rem;
            padding: 0.34rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
        }
        .portfolio-stat.compact {
            min-height: 58px;
            padding: 0.48rem 0.68rem;
            border: 0;
            border-right: 1px solid rgba(15, 23, 42, 0.06);
            border-radius: 0;
            background: transparent;
            box-shadow: none;
        }
        .portfolio-stat.compact:last-child {
            border-right: 0;
        }
        .portfolio-stat.compact span {
            color: #64748b;
            font-size: 0.66rem;
            font-weight: 760;
        }
        .portfolio-stat.compact strong {
            margin-top: 0.18rem;
            color: #0f172a;
            font-size: 1.22rem;
            letter-spacing: 0;
        }
        .portfolio-stat.compact em {
            margin-top: 0.12rem;
            color: #94a3b8;
            opacity: 1;
            font-size: 0.62rem;
        }
        .ladder-buy-reference {
            margin: 0.55rem 0 0.25rem;
            padding: 0.58rem 0.68rem;
            border: 1px solid rgba(59, 130, 246, 0.13);
            border-left: 3px solid #5B7FA6;
            border-radius: 8px;
            background: rgba(59, 130, 246, 0.045);
        }
        .ladder-buy-reference.is-empty {
            border-color: rgba(148, 163, 184, 0.18);
            border-left-color: #94a3b8;
            background: rgba(148, 163, 184, 0.06);
        }
        .ladder-buy-reference strong {
            display: block;
            color: #0f172a;
            font-size: 0.78rem;
            margin-bottom: 0.28rem;
        }
        .ladder-buy-reference span,
        .ladder-buy-reference li {
            color: #64748b;
            font-size: 0.72rem;
        }
        .ladder-buy-reference ul {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.35rem;
            margin: 0;
            padding: 0;
            list-style: none;
        }
        .ladder-buy-reference li {
            min-width: 0;
            padding: 0.36rem 0.44rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-radius: 7px;
            background: rgba(255, 255, 255, 0.62);
        }
        .ladder-buy-reference li b {
            display: block;
            color: #0f172a;
            font-size: 0.72rem;
        }
        .ladder-buy-reference-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.38rem;
        }
        .buy-plan-status-strip {
            display: grid;
            grid-template-columns: minmax(72px, 0.5fr) minmax(96px, 0.7fr) 1fr 1.4fr;
            gap: 0.55rem;
            align-items: center;
            margin: 0.35rem 0 0.75rem;
            padding: 0.58rem 0.68rem;
            border: 1px solid rgba(59, 130, 246, 0.13);
            border-left: 3px solid #5B7FA6;
            border-radius: 8px;
            background: rgba(59, 130, 246, 0.045);
        }
        .buy-plan-status-strip b,
        .buy-plan-status-strip span {
            color: #0f172a;
            font-size: 0.78rem;
            font-weight: 780;
        }
        .buy-plan-status-strip small {
            color: #64748b;
            font-size: 0.7rem;
        }
        .portfolio-gate-notice {
            margin: 0.35rem 0 0.75rem;
            padding: 0.7rem 0.85rem;
            border: 1px solid rgba(181, 80, 80, 0.22);
            border-left: 3px solid #B55050;
            border-radius: 8px;
            background: rgba(181, 80, 80, 0.08);
        }
        .portfolio-gate-notice-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 0.75rem;
            margin-bottom: 0.55rem;
        }
        .portfolio-gate-notice-head strong {
            color: #8F3030;
            font-size: 0.88rem;
        }
        .portfolio-gate-notice-head span,
        .portfolio-gate-notice li {
            color: #64748b;
            font-size: 0.76rem;
        }
        .portfolio-gate-notice-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.7rem;
        }
        .portfolio-gate-notice-grid > div {
            padding: 0.55rem 0.62rem;
            border: 1px solid rgba(181, 80, 80, 0.12);
            border-radius: 7px;
            background: rgba(255, 255, 255, 0.55);
        }
        .portfolio-gate-notice-grid b {
            display: block;
            margin-bottom: 0.32rem;
            color: #0f172a;
            font-size: 0.76rem;
        }
        .portfolio-gate-notice ul {
            margin: 0;
            padding-left: 1.05rem;
        }
        .portfolio-reconciliation-strip {
            display: grid;
            grid-template-columns: 132px minmax(0, 1fr);
            gap: 0;
            margin: -0.3rem 0 0.72rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-left: 3px solid #4F9D78;
            border-radius: 8px;
            background: #FFFFFF;
            overflow: hidden;
        }
        .portfolio-reconciliation-strip.has-action {
            grid-template-columns: 132px minmax(0, 1fr) 142px;
        }
        .portfolio-reconciliation-strip.warning {
            border-left-color: #C59A32;
        }
        .portfolio-reconciliation-strip.mismatch {
            border-left-color: #B34A4A;
        }
        .portfolio-reconciliation-title {
            display: grid;
            align-content: center;
            gap: 0.08rem;
            padding: 0.42rem 0.62rem;
            border-right: 1px solid rgba(15, 23, 42, 0.055);
            background: #F8FAFC;
        }
        .portfolio-reconciliation-title strong {
            color: #0f172a;
            font-size: 0.74rem;
            font-weight: 820;
        }
        .portfolio-reconciliation-title span {
            color: #94a3b8;
            font-size: 0.62rem;
            font-weight: 680;
        }
        .portfolio-reconciliation-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            min-width: 0;
        }
        .portfolio-reconciliation-item {
            display: grid;
            align-content: center;
            min-height: 44px;
            padding: 0.34rem 0.56rem;
            border-right: 1px solid rgba(15, 23, 42, 0.04);
        }
        .portfolio-reconciliation-item:last-child {
            border-right: 0;
        }
        .portfolio-reconciliation-item span {
            color: #64748b;
            font-size: 0.62rem;
            font-weight: 760;
        }
        .portfolio-reconciliation-item strong {
            margin-top: 0.08rem;
            color: #0f172a;
            font-size: 0.88rem;
            font-weight: 820;
            font-variant-numeric: tabular-nums;
        }
        .portfolio-reconciliation-action {
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 44px;
            padding: 0 0.72rem;
            border-left: 1px solid rgba(15, 23, 42, 0.055);
            color: #334155;
            background: rgba(248, 250, 252, 0.86);
            font-size: 0.68rem;
            font-weight: 820;
            text-decoration: none;
            white-space: nowrap;
        }
        .portfolio-reconciliation-action:hover {
            color: #0f172a;
            background: #EEF2F7;
        }
        .portfolio-radar-head {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 1rem;
            margin: 0.15rem 0 0.35rem;
        }
        .portfolio-radar-head strong {
            color: #0f172a;
            font-size: 0.95rem;
            font-weight: 820;
        }
        .portfolio-radar-head span {
            color: #94a3b8;
            font-size: 0.72rem;
            font-weight: 680;
        }
        .portfolio-lanes {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.38rem;
            margin: 0 0 0.85rem;
        }
        .portfolio-lane {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 0.08rem 0.48rem;
            align-items: baseline;
            min-height: 44px;
            padding: 0.42rem 0.56rem;
            border: 1px solid rgba(15, 23, 42, 0.07);
            border-left: 3px solid var(--zhx-line-strong);
            border-radius: 6px;
            background: #FBFCFE;
        }
        .portfolio-lane span {
            color: #64748b;
            font-size: 0.66rem;
            font-weight: 820;
        }
        .portfolio-lane b {
            color: #0f172a;
            font-size: 0.95rem;
        }
        .portfolio-lane small {
            grid-column: 1 / -1;
            color: #94a3b8;
            font-size: 0.66rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .portfolio-lane.tone-green { border-left-color: #4F9D78; }
        .portfolio-lane.tone-yellow { border-left-color: #C59A32; }
        .portfolio-lane.tone-red { border-left-color: #B56A32; }
        .portfolio-lane.tone-neutral { border-left-color: #6B83A6; }
        .portfolio-table-wrap.terminal {
            margin-top: 0.3rem;
            border-color: rgba(15, 23, 42, 0.08);
            border-radius: 7px;
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.035);
        }
        .portfolio-table.terminal {
            table-layout: fixed;
            min-width: 1030px;
            font-size: 0.72rem;
        }
        .portfolio-col-symbol { width: 110px; }
        .portfolio-col-cost { width: 150px; }
        .portfolio-col-pnl { width: 140px; }
        .portfolio-col-weight { width: 130px; }
        .portfolio-col-system { width: 220px; }
        .portfolio-col-plan { width: 150px; }
        .portfolio-col-actions { width: 165px; }
        .portfolio-table.terminal th {
            height: 28px;
            padding: 0.28rem 0.52rem;
            background: #FAFBFC;
            color: #7b8798;
            font-size: 0.63rem;
            font-weight: 760;
            letter-spacing: 0;
            text-transform: none;
        }
        .portfolio-table.terminal td {
            height: 50px;
            padding: 0.34rem 0.52rem;
            vertical-align: middle;
        }
        .portfolio-table.terminal th:last-child,
        .portfolio-table.terminal td:last-child {
            width: 165px;
            padding-left: 0.4rem;
            padding-right: 0.4rem;
            text-align: center;
        }
        .portfolio-table.terminal tr:hover td {
            background: #FBFCFE;
        }
        .portfolio-symbol-stack {
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 2px;
            min-width: 0;
        }
        .portfolio-symbol-stack b {
            font-size: 0.83rem;
            font-weight: 860;
            line-height: 1;
        }
        .portfolio-symbol-stack small {
            max-width: 100%;
            color: #7b8798;
            font-size: 0.62rem;
            line-height: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .portfolio-tier-badge {
            display: inline-flex;
            align-items: center;
            height: 16px;
            padding: 0 6px;
            border-radius: 999px;
            border: 1px solid rgba(100, 116, 139, 0.18);
            background: #F8FAFC;
            color: #475569;
            font-size: 0.58rem;
            font-style: normal;
            font-weight: 760;
            line-height: 1;
            white-space: nowrap;
        }
        .portfolio-tier-badge.tier-a {
            border-color: rgba(22, 101, 52, 0.18);
            background: rgba(240, 253, 244, 0.72);
            color: #166534;
        }
        .portfolio-tier-badge.tier-b {
            border-color: rgba(146, 64, 14, 0.16);
            background: rgba(255, 251, 235, 0.78);
            color: #92400E;
        }
        .portfolio-tier-badge.tier-c {
            border-color: rgba(30, 64, 175, 0.16);
            background: rgba(239, 246, 255, 0.76);
            color: #1E40AF;
        }
        .portfolio-tier-badge.tier-missing {
            border-color: rgba(185, 28, 28, 0.16);
            background: rgba(254, 242, 242, 0.78);
            color: #991B1B;
        }
        .portfolio-cell {
            gap: 0.1rem;
            max-height: 32px;
        }
        .portfolio-cell b {
            font-size: 0.76rem;
            line-height: 1.1;
        }
        .portfolio-cell small {
            max-width: 100%;
            color: #7b8798;
            font-size: 0.66rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .portfolio-row-actions {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 0;
            border: 0;
            border-radius: 0;
            background: transparent;
            box-shadow: none;
            white-space: nowrap;
        }
        .portfolio-view-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 38px;
            height: 26px;
            padding: 0 0.42rem;
            border: 1px solid transparent;
            border-radius: 4px;
            color: #52657F;
            background: transparent;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
        }
        .portfolio-table a.portfolio-view-link,
        .portfolio-table a.portfolio-view-link:visited,
        .portfolio-table a.portfolio-view-link:hover,
        .portfolio-table a.portfolio-view-link:active {
            text-decoration: none !important;
        }
        .portfolio-view-link:hover {
            color: #0f172a;
            background: #FFFFFF;
            border-color: rgba(15, 23, 42, 0.10);
            text-decoration: none;
        }
        .portfolio-row-actions .portfolio-view-link:first-child {
            color: #0f172a;
            background: #FFFFFF;
            border-color: rgba(15, 23, 42, 0.08);
        }
        .portfolio-research-link {
            color: #52657F;
            background: transparent;
            border-color: transparent;
        }
        .portfolio-system-cell,
        .portfolio-plan-cell {
            display: grid;
            gap: 0.1rem;
            min-width: 0;
            padding-left: 0.48rem;
            border-left: 3px solid #CBD5E1;
        }
        .portfolio-system-cell b,
        .portfolio-plan-cell b {
            color: #0f172a;
            font-size: 0.75rem;
            line-height: 1.1;
            font-weight: 820;
        }
        .portfolio-system-cell small,
        .portfolio-plan-cell small {
            color: #7b8798;
            font-size: 0.65rem;
            line-height: 1.15;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .portfolio-system-cell.tone-green { border-left-color: #4F9D78; }
        .portfolio-system-cell.tone-amber { border-left-color: #C59A32; }
        .portfolio-system-cell.tone-risk { border-left-color: #B56A32; background: linear-gradient(90deg, rgba(181, 106, 50, 0.06), transparent 70%); }
        .portfolio-system-cell.tone-neutral { border-left-color: #6B83A6; }
        .portfolio-plan-cell.is-empty b,
        .portfolio-plan-cell.is-empty small {
            color: #94a3b8;
            font-weight: 680;
        }
        .portfolio-plan-cell.is-set { border-left-color: #6B83A6; }
        .portfolio-drawer-actions a:first-child {
            color: #0f172a;
            font-weight: 760;
        }
        .portfolio-drawer {
            pointer-events: none;
            position: fixed;
            inset: 0;
            z-index: 9999;
            opacity: 0;
            transition: opacity 0.16s ease;
        }
        .portfolio-drawer:target {
            pointer-events: auto;
            opacity: 1;
        }
        .portfolio-drawer-backdrop {
            position: absolute;
            inset: 0;
            background: rgba(15, 23, 42, 0.18);
        }
        .portfolio-drawer-panel {
            position: absolute;
            top: 0;
            right: 0;
            width: min(420px, 92vw);
            height: 100vh;
            overflow: auto;
            background: #FFFFFF;
            border-left: 1px solid var(--zhx-line);
            box-shadow: -20px 0 45px rgba(15, 23, 42, 0.12);
            transform: translateX(100%);
            transition: transform 0.18s ease;
        }
        .portfolio-drawer:target .portfolio-drawer-panel {
            transform: translateX(0);
        }
        .portfolio-drawer-head {
            position: sticky;
            top: 0;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            padding: 0.8rem 0.95rem;
            border-bottom: 1px solid var(--zhx-line);
            background: #FFFFFF;
        }
        .portfolio-drawer-head strong {
            display: block;
            color: var(--zhx-text);
            font-size: 1rem;
        }
        .portfolio-drawer-head span,
        .portfolio-drawer-head a {
            color: var(--zhx-muted);
            font-size: 0.74rem;
            text-decoration: none;
        }
        .portfolio-drawer-actions {
            display: flex;
            align-items: center;
            gap: 8px;
            flex-shrink: 0;
        }
        .portfolio-drawer-actions a {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            height: 26px;
            padding: 0 0.56rem;
            border: 1px solid transparent;
            border-radius: 4px;
            color: #52657F;
            background: transparent;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
        }
        .portfolio-drawer-actions a:hover {
            color: #0f172a;
            background: #FFFFFF;
            border-color: rgba(15, 23, 42, 0.10);
            text-decoration: none;
        }
        .portfolio-drawer-actions a:first-child {
            color: var(--zhx-text);
            font-weight: 760;
            background: #FFFFFF;
            border-color: rgba(15, 23, 42, 0.10);
        }
        .portfolio-drawer-section {
            padding: 0.75rem 0.95rem;
            border-bottom: 1px solid var(--zhx-line);
        }
        .portfolio-drawer-section h4 {
            margin: 0 0 0.45rem;
            color: var(--zhx-text);
            font-size: 0.78rem;
        }
        .portfolio-drawer-section > div {
            display: grid;
            grid-template-columns: 118px 1fr;
            gap: 0.34rem 0.7rem;
        }
        .portfolio-drawer-section span {
            color: var(--zhx-muted);
            font-size: 0.72rem;
        }
        .portfolio-drawer-section b {
            color: var(--zhx-text);
            font-size: 0.74rem;
            font-weight: 720;
            overflow-wrap: anywhere;
        }
        .portfolio-drawer-danger {
            padding: 0.8rem 0.95rem 1rem;
        }
        .portfolio-drawer-danger a {
            color: #8A4B00;
            font-size: 0.74rem;
            font-weight: 760;
            text-decoration: none;
        }
        @media (max-width: 1100px) {
            .portfolio-lanes {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 720px) {
            .portfolio-lanes {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _input_value(value: object) -> str:
    number = _number(value)
    if number is None:
        return ""
    if number == int(number):
        return str(int(number))
    return str(number)


def _number(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _render_styles() -> None:
    st.markdown(
        """
        <style>
        .portfolio-overview {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.65rem;
            margin: 0.65rem 0 1.05rem;
        }
        .portfolio-stat,
        .portfolio-action-card {
            border: 1px solid var(--zhx-line);
            background: var(--zhx-surface);
            border-radius: 8px;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.045);
        }
        .portfolio-stat {
            padding: 0.72rem 0.78rem;
            min-height: 88px;
        }
        .portfolio-stat span,
        .portfolio-action-card span {
            display: block;
            color: var(--zhx-muted);
            font-size: 0.72rem;
            font-weight: 760;
        }
        .portfolio-stat strong {
            display: block;
            margin-top: 0.28rem;
            color: var(--zhx-text);
            font-size: 1.08rem;
            line-height: 1.1;
        }
        .portfolio-stat em {
            display: block;
            margin-top: 0.28rem;
            color: var(--zhx-faint);
            font-size: 0.68rem;
            font-style: normal;
            text-transform: uppercase;
        }
        .portfolio-action-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.65rem;
            margin: 0.5rem 0 1.1rem;
        }
        .portfolio-action-card {
            padding: 0.72rem 0.78rem;
            min-height: 102px;
            border-left: 4px solid var(--zhx-line-strong);
        }
        .portfolio-action-card strong {
            display: block;
            margin-top: 0.18rem;
            font-size: 1.35rem;
            line-height: 1;
        }
        .portfolio-action-card p {
            margin: 0.45rem 0 0;
            color: var(--zhx-muted);
            font-size: 0.74rem;
            line-height: 1.35;
        }
        .portfolio-action-card.tone-green { border-left-color: var(--zhx-green); }
        .portfolio-action-card.tone-yellow { border-left-color: var(--zhx-yellow); }
        .portfolio-action-card.tone-orange { border-left-color: var(--zhx-orange); }
        .portfolio-action-card.tone-red { border-left-color: var(--zhx-red); }
        .portfolio-action-card.tone-neutral { border-left-color: var(--zhx-blue); }
        .portfolio-action-card.muted { border-left-color: var(--zhx-line-strong); }
        .portfolio-table-wrap {
            margin: 0.45rem 0 1.1rem;
            overflow-x: auto;
            border: 1px solid var(--zhx-line);
            border-radius: 8px;
            background: var(--zhx-surface);
        }
        .portfolio-table {
            width: 100%;
            border-collapse: collapse;
            min-width: 1120px;
            font-size: 0.78rem;
        }
        .portfolio-table.compact {
            min-width: 920px;
            font-size: 0.76rem;
        }
        .portfolio-table th {
            padding: 0.46rem 0.58rem;
            text-align: left;
            color: var(--zhx-muted);
            background: #F8FAFC;
            border-bottom: 1px solid var(--zhx-line);
            font-size: 0.66rem;
            font-weight: 820;
            text-transform: none;
        }
        .portfolio-table td {
            height: 46px;
            padding: 0.42rem 0.62rem;
            border-bottom: 1px solid var(--zhx-line);
            color: var(--zhx-text);
            white-space: nowrap;
            vertical-align: middle;
        }
        .portfolio-table th:last-child,
        .portfolio-table td:last-child {
            width: 128px;
            padding-left: 0.72rem;
            padding-right: 0.72rem;
            text-align: center;
        }
        .portfolio-table tr:last-child td {
            border-bottom: 0;
        }
        .portfolio-table td.notes {
            min-width: 220px;
            max-width: 360px;
            white-space: normal;
            color: var(--zhx-muted);
        }
        .portfolio-cell {
            display: grid;
            gap: 0.16rem;
            min-width: 0;
            max-height: 34px;
        }
        .portfolio-cell b {
            color: var(--zhx-text);
            font-size: 0.8rem;
            font-weight: 820;
            line-height: 1.15;
        }
        .portfolio-cell small {
            color: var(--zhx-muted);
            font-size: 0.68rem;
            line-height: 1.2;
            max-width: 210px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .portfolio-row-actions {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 6px;
            padding: 0;
            border: 0;
            border-radius: 0;
            background: transparent;
            box-shadow: none;
            white-space: nowrap;
        }
        .portfolio-view-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 38px;
            height: 26px;
            padding: 0 0.42rem;
            border: 1px solid transparent;
            border-radius: 4px;
            color: #52657F;
            background: transparent;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
        }
        .portfolio-table a.portfolio-view-link,
        .portfolio-table a.portfolio-view-link:visited,
        .portfolio-table a.portfolio-view-link:hover,
        .portfolio-table a.portfolio-view-link:active {
            text-decoration: none !important;
        }
        .portfolio-view-link:hover {
            text-decoration: none;
        }
        .portfolio-research-link {
            color: #52657F;
            background: transparent;
            border-color: transparent;
        }
        .portfolio-detail-panel {
            margin: 0.65rem 0 1rem;
            border: 1px solid var(--zhx-line);
            border-radius: 8px;
            background: #FFFFFF;
        }
        .portfolio-detail-head {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 1rem;
            padding: 0.62rem 0.78rem;
            border-bottom: 1px solid var(--zhx-line);
        }
        .portfolio-detail-head strong {
            color: var(--zhx-text);
            font-size: 0.9rem;
        }
        .portfolio-detail-head span {
            color: var(--zhx-muted);
            font-size: 0.74rem;
        }
        .portfolio-detail-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.42rem 0.85rem;
            padding: 0.72rem 0.78rem;
            background: #F8FAFC;
            white-space: normal;
        }
        .portfolio-detail-grid span {
            color: var(--zhx-muted);
            font-size: 0.68rem;
        }
        .portfolio-detail-grid b {
            color: var(--zhx-text);
            font-size: 0.7rem;
            font-weight: 720;
            overflow-wrap: anywhere;
        }
        .portfolio-empty {
            margin: 0.45rem 0 0.5rem;
            padding: 0.85rem 1rem;
            border: 1px dashed var(--zhx-line-strong);
            border-radius: 8px;
            background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
            text-align: center;
        }
        .portfolio-empty div {
            color: var(--zhx-text);
            font-size: 1.05rem;
            font-weight: 820;
        }
        .portfolio-empty span {
            display: block;
            margin-top: 0.35rem;
            color: var(--zhx-muted);
            font-size: 0.82rem;
        }
        [data-testid="stExpander"] {
            border-color: var(--zhx-line);
            border-radius: 8px;
            background: var(--zhx-surface);
        }
        [data-testid="stExpander"] details summary {
            font-size: 0.86rem;
            font-weight: 760;
        }
        [data-testid="stFormSubmitButton"] button {
            background: #0B1220 !important;
            border-color: #0B1220 !important;
            color: #F8FAFC !important;
        }
        [data-testid="stButton"] button[kind="primary"] {
            background: var(--zhx-red) !important;
            border-color: var(--zhx-red) !important;
            color: #FFFFFF !important;
        }
        .portfolio-form-section {
            margin: 0.25rem 0 0.35rem;
            color: var(--zhx-muted);
            font-size: 0.72rem;
            font-weight: 820;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }
        .buy-execution-plan-summary {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.48rem 0.72rem;
            margin: 0.5rem 0 0.72rem;
            padding: 0.7rem 0.78rem;
            border: 1px solid rgba(15, 23, 42, 0.08);
            border-radius: 8px;
            background: #F8FAFC;
        }
        .buy-execution-plan-summary strong,
        .buy-execution-plan-summary small,
        .buy-execution-plan-summary a,
        .buy-execution-plan-summary > span {
            grid-column: 1 / -1;
        }
        .buy-execution-plan-summary strong {
            color: var(--zhx-text);
            font-size: 0.84rem;
        }
        .buy-execution-plan-summary small,
        .buy-execution-plan-summary > span {
            color: var(--zhx-muted);
            font-size: 0.72rem;
        }
        .buy-execution-plan-summary a {
            color: #1D4ED8;
            font-size: 0.76rem;
            font-weight: 760;
            text-decoration: none;
        }
        .buy-execution-plan-summary-item {
            min-width: 0;
        }
        .buy-execution-plan-summary-item span {
            display: block;
            color: var(--zhx-muted);
            font-size: 0.66rem;
        }
        .buy-execution-plan-summary-item b {
            color: var(--zhx-text);
            font-size: 0.75rem;
            overflow-wrap: anywhere;
        }
        @media (max-width: 1100px) {
            .portfolio-overview {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
            .portfolio-action-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }
        @media (max-width: 720px) {
            .portfolio-overview,
            .portfolio-action-grid {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
