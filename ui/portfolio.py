from __future__ import annotations

from html import escape
from urllib.parse import quote
import streamlit as st

from data.decision_log import DecisionLogStore, TradeJournalStore
from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.portfolio_view_model import build_portfolio_view_model
from data.stock_plan import StockPlanStore
from data.trading_discipline import evaluate_trading_discipline, load_trading_discipline_config
from formatting import format_currency, format_percent
from settings import load_watchlist
from ui.theme import render_page_header, render_section_title


EMPTY_POSITION = {
    "symbol": "",
    "quantity": "",
    "average_cost": "",
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
    "sell_put": "卖 Put",
    "covered_call": "Covered Call",
    "skip": "放弃操作",
}
POSITION_CLASS_LABELS = {
    "A": "A 类核心仓",
    "B": "B 类平衡仓",
    "C": "C 类交易仓",
}


def _render_editor(
    position_store: PortfolioPositionStore,
    settings_store: PortfolioSettingsStore,
    rows: list[dict],
    settings: dict,
) -> None:
    position_open = bool(st.session_state.get("portfolio_position_editor_open", False))
    with st.expander("添加/编辑持仓", expanded=position_open):
        st.session_state["portfolio_position_editor_open"] = False
        symbols = [str(row.get("symbol") or "") for row in rows]
        options = ["新增持仓", *symbols]
        preferred = st.session_state.pop("portfolio_edit_symbol", "")
        if preferred in options:
            st.session_state["portfolio-edit-symbol"] = preferred
        selected_index = options.index(preferred) if preferred in options else 0
        selected = st.selectbox("编辑对象", options, index=selected_index, key="portfolio-edit-symbol")
        editing = selected != "新增持仓"
        current = position_store.get_position(selected) if editing else None
        current = current or EMPTY_POSITION
        watchlist_symbols = _available_watchlist_symbols(symbols)
        form_key = _position_form_key(selected)
        save_symbol = str(current.get("symbol") or "") if editing else ""

        with st.form("portfolio-position-form"):
            st.markdown('<div class="portfolio-form-section">基础持仓</div>', unsafe_allow_html=True)
            basic_cols = st.columns(2)
            if editing:
                symbol = basic_cols[0].text_input(
                    "股票代码",
                    value=str(current.get("symbol") or ""),
                    disabled=True,
                    key=f"{form_key}:symbol-disabled",
                )
            else:
                _symbol_input_from_watchlist(basic_cols[0], watchlist_symbols, form_key)
            quantity = basic_cols[1].text_input(
                "持股数量",
                value=_input_value(current.get("quantity")),
                key=f"{form_key}:quantity",
            )
            cost_cols = st.columns(2)
            average_cost = cost_cols[0].text_input(
                "平均成本",
                value=_input_value(current.get("average_cost")),
                key=f"{form_key}:average_cost",
            )
            cost_cols[1].write("")

            st.markdown('<div class="portfolio-form-section">计划参数</div>', unsafe_allow_html=True)
            plan_cols = st.columns(2)
            target_position_pct = plan_cols[0].text_input(
                "目标仓位",
                value=_input_value(current.get("target_position_pct")),
                key=f"{form_key}:target_position_pct",
            )
            max_acceptable_position_pct = plan_cols[1].text_input(
                "最大可接受仓位",
                value=_input_value(current.get("max_acceptable_position_pct")),
                key=f"{form_key}:max_acceptable_position_pct",
            )
            sell_cols = st.columns(2)
            planned_sell_price = sell_cols[0].text_input(
                "计划卖出价",
                value=_input_value(current.get("planned_sell_price")),
                key=f"{form_key}:planned_sell_price",
            )
            first_trim_price = sell_cols[1].text_input(
                "第一减仓价",
                value=_input_value(current.get("first_trim_price")),
                key=f"{form_key}:first_trim_price",
            )
            review_cols = st.columns(2)
            second_trim_price = review_cols[0].text_input(
                "第二减仓价",
                value=_input_value(current.get("second_trim_price")),
                key=f"{form_key}:second_trim_price",
            )
            review_price = review_cols[1].text_input(
                "复核线",
                value=_input_value(current.get("review_price")),
                key=f"{form_key}:review_price",
            )
            notes = st.text_area(
                "备注",
                value=str(current.get("notes") or ""),
                height=96,
                key=f"{form_key}:notes",
            )

            st.form_submit_button(
                "保存持仓",
                width="stretch",
                on_click=_save_position_from_form,
                args=(position_store, save_symbol, form_key),
            )

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


def _available_watchlist_symbols(active_symbols: list[str]) -> list[str]:
    active = {symbol.upper() for symbol in active_symbols}
    return [symbol for symbol in load_watchlist() if symbol.upper() not in active]


def _symbol_input_from_watchlist(column, watchlist_symbols: list[str], form_key: str) -> None:
    if watchlist_symbols:
        choice = column.selectbox(
            "股票代码",
            [*watchlist_symbols, "手动输入"],
            help="优先从观察池选择。",
            key=f"{form_key}:symbol-choice",
        )
        if choice != "手动输入":
            return
        column.text_input("手动股票代码", key=f"{form_key}:manual-symbol")
        return
    column.text_input("股票代码", help="观察池股票都已有持仓，可手动输入其他代码。", key=f"{form_key}:symbol")


def _position_form_key(selected: str) -> str:
    safe = "".join(ch for ch in str(selected or "new").upper() if ch.isalnum() or ch in {"-", "_"})
    return f"portfolio-position-form:{safe or 'NEW'}"


def _form_value(form_key: str, field: str) -> object:
    return st.session_state.get(f"{form_key}:{field}")


def _form_symbol(form_key: str, fallback: str) -> str:
    if fallback:
        return fallback
    choice = str(_form_value(form_key, "symbol-choice") or "").strip()
    if choice and choice != "手动输入":
        return choice
    return str(_form_value(form_key, "manual-symbol") or _form_value(form_key, "symbol") or "").strip()


def _save_position_from_form(position_store: PortfolioPositionStore, symbol: str, form_key: str) -> None:
    try:
        saved = position_store.save_position(
            _form_symbol(form_key, symbol),
            {
                "quantity": _form_value(form_key, "quantity"),
                "average_cost": _form_value(form_key, "average_cost"),
                "target_position_pct": _form_value(form_key, "target_position_pct"),
                "max_acceptable_position_pct": _form_value(form_key, "max_acceptable_position_pct"),
                "planned_sell_price": _form_value(form_key, "planned_sell_price"),
                "first_trim_price": _form_value(form_key, "first_trim_price"),
                "second_trim_price": _form_value(form_key, "second_trim_price"),
                "review_price": _form_value(form_key, "review_price"),
                "notes": _form_value(form_key, "notes"),
                "is_active": True,
            },
        )
        saved_symbol = str(saved.get("symbol") or "").strip().upper()
        st.session_state["portfolio-drawer-action-symbol"] = saved_symbol
        st.session_state["portfolio_save_notice"] = ("success", f"{saved_symbol} 持仓已保存。")
    except ValueError as exc:
        st.session_state["portfolio_position_editor_open"] = True
        st.session_state["portfolio_save_notice"] = ("error", str(exc))


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
    if int(row.get("unsyncedTradeCount") or 0) > 0:
        return "有未同步交易记录"
    deviation = _deviation_text(row)
    return deviation if deviation != "暂无偏离提示" else _price_status_text(row.get("priceStatus"))


def _trade_sync_text(row: dict) -> str:
    count = int(row.get("unsyncedTradeCount") or 0)
    if count <= 0:
        return "已同步"
    return f"有 {count} 条未同步交易记录，请到交易日志处理"


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
        "tranche_buy": "分批买区",
        "heavy_buy": "重仓买区",
        "below_heavy_buy": "低于重仓区",
        "no_chase": "禁止追高",
        "data_insufficient": "数据不足",
        "invalid_zone": "买区异常",
        "low_confidence_zone": "低置信买区",
    }.get(str(value), "未生成")


def _reason_text(value: object) -> str:
    reasons = _translated_reasons(value)
    return "，".join(reasons) if reasons else BLANK_TEXT


def _translated_reasons(value: object) -> list[str]:
    labels = {
        "buy_zone": "买区阻断",
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

    _render_deactivate_dialog_if_needed(position_store)
    _render_overview_strip(view["summary"])
    _render_action_panel(view["actionGroups"])
    _render_positions_table(rows, position_store, plan_store)
    _render_editor(position_store, settings_store, rows, settings)


def _consume_portfolio_edit_query() -> None:
    symbol = str(st.query_params.get("portfolioEdit") or "").strip().upper()
    if not symbol:
        return
    st.session_state["portfolio_position_editor_open"] = True
    st.session_state["portfolio_edit_symbol"] = symbol
    if "portfolioEdit" in st.query_params:
        st.query_params.pop("portfolioEdit")


def _render_portfolio_notice() -> None:
    notice = st.session_state.pop("portfolio_save_notice", None)
    if not notice:
        return
    level, message = notice
    if level == "error":
        st.error(str(message))
    else:
        st.success(str(message))


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
            "<span>添加第一只股票后，这里会显示仓位、盈亏、系统参考和计划状态。</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        return

    headers = ["股票", "持仓 / 成本", "现价 / 盈亏", "仓位 / 上限", "系统参考", "我的计划", "操作"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row) for row in rows)
    decision_store = DecisionLogStore()
    trade_store = TradeJournalStore()
    drawer_html = "".join(_drawer_html(row, plan_store, decision_store, trade_store) for row in rows)
    archive_html = "".join(_archive_confirm_html(row) for row in rows)
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
        f"{drawer_html}"
        f"{archive_html}",
        unsafe_allow_html=True,
    )


def _position_row_html(row: dict) -> str:
    symbol = str(row.get("symbol") or "")
    drawer_id = _drawer_id(symbol)
    archive_id = _archive_id(symbol)
    research_href = f"?page=detail&symbol={quote(symbol)}"
    return (
        "<tr>"
        f'<td class="portfolio-symbol-cell">{_cell_html(symbol, _row_status_text(row))}</td>'
        f"<td>{_cell_html(_share_count_text(row.get('quantity')), '成本 ' + _money_text(row.get('costBasis')) + ' / 均价 ' + _money_text(row.get('averageCost')))}</td>"
        f"<td>{_cell_html(_money_text(row.get('currentPrice')), _money_text(row.get('unrealizedPnl')) + ' / ' + _percent_text(row.get('unrealizedPnlPct')))}</td>"
        f"<td>{_cell_html(_percent_text(row.get('positionPct')), '系统 ' + _percent_text(row.get('systemMaxPosition')) + ' / 个人 ' + _percent_text(row.get('maxAcceptablePositionPct')))}</td>"
        f"<td>{_system_cell_html(row)}</td>"
        f"<td>{_plan_cell_html(row)}</td>"
        '<td><div class="portfolio-row-actions">'
        f'<a class="portfolio-view-link" href="#{escape(drawer_id)}">查看</a>'
        f'<a class="portfolio-view-link portfolio-research-link" href="{escape(research_href, quote=True)}" target="_self">研究</a>'
        f'<a class="portfolio-view-link portfolio-archive-link" href="#{escape(archive_id)}">归档</a>'
        "</div></td>"
        "</tr>"
    )


def _archive_confirm_html(row: dict) -> str:
    symbol = str(row.get("symbol") or "")
    archive_id = _archive_id(symbol)
    return (
        f'<aside id="{escape(archive_id)}" class="portfolio-archive-modal">'
        '<a class="portfolio-archive-backdrop" href="#portfolio-table"></a>'
        '<div class="portfolio-archive-card">'
        "<span>归档持仓</span>"
        f"<strong>{escape(symbol)}</strong>"
        "<p>确认后，该标的将从当前持仓视图移出，并作为历史持仓留档。</p>"
        '<div class="portfolio-archive-actions">'
        '<a href="#portfolio-table">取消</a>'
        '<form method="get" action="/">'
        '<input type="hidden" name="page" value="portfolio">'
        f'<input type="hidden" name="portfolioArchiveConfirm" value="{escape(symbol, quote=True)}">'
        '<button type="submit">确认归档</button>'
        "</form>"
        "</div>"
        "</div>"
        "</aside>"
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


def _plan_cell_html(row: dict) -> str:
    status = _plan_status_text(row)
    tone = "is-empty" if status == "未设置计划" else "is-set"
    return (
        f'<div class="portfolio-plan-cell {escape(tone)}">'
        f"<b>{escape(status)}</b>"
        f"<small>{escape(_plan_sub_text(row))}</small>"
        "</div>"
    )


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
        ("系统参考", [
            ("怎么看", _system_explanation_text(row)),
            ("系统动作", _system_action_text(row)),
            ("系统上限", _percent_text(row.get("systemMaxPosition"))),
            ("当前可加", _percent_text(row.get("systemCurrentAdd"))),
            ("决策通道", _decision_lane_text(row.get("decisionLane"))),
            ("买区状态", _buy_zone_status_text(row.get("buyZoneStatus"))),
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
        f"<span>{escape(label)}</span><b>{escape(str(value))}</b>"
        for label, value in items
    )
    return f'<section class="portfolio-drawer-section"><h4>{escape(title)}</h4><div>{rows}</div></section>'


def _trading_discipline_items(row: dict) -> list[tuple[str, object]]:
    symbol = str(row.get("symbol") or "").upper()
    position_class = _position_class_for_row(row)
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
    anchor = (
        _number(row.get("targetPositionPct"))
        or _number(row.get("systemMaxPosition"))
        or _number(row.get("positionPct"))
        or 0.0
    )
    if anchor >= 8:
        return "A"
    if anchor >= 4:
        return "B"
    return "C"


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
    return "系统参考不足，先按手动计划管理。"


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


def _render_deactivate_dialog_if_needed(position_store: PortfolioPositionStore) -> None:
    confirmed = str(st.query_params.get("portfolioArchiveConfirm", "")).strip().upper()
    if confirmed:
        position_store.deactivate_position(confirmed)
        if "portfolioArchiveConfirm" in st.query_params:
            st.query_params.pop("portfolioArchiveConfirm")
        st.session_state["portfolio_save_notice"] = ("success", f"{confirmed} 已归档。")
        st.rerun()
    symbol = str(st.session_state.get("portfolio_archive_symbol", "")).strip().upper()
    if symbol:
        _confirm_deactivate_dialog(symbol, position_store)


@st.dialog("归档持仓")
def _confirm_deactivate_dialog(symbol: str, position_store: PortfolioPositionStore) -> None:
    st.write(f"确认将 {symbol} 移入归档？归档后该标的将退出当前持仓视图，仅保留历史持仓记录。")
    cols = st.columns(2)
    if cols[0].button("确认归档", type="primary", width="stretch"):
        position_store.deactivate_position(symbol)
        st.session_state.pop("portfolio_archive_symbol", None)
        st.rerun()
    if cols[1].button("取消", width="stretch"):
        st.session_state.pop("portfolio_archive_symbol", None)
        st.rerun()


def _drawer_id(symbol: str) -> str:
    safe = "".join(ch for ch in str(symbol).upper() if ch.isalnum() or ch in {"-", "_"})
    return f"portfolio-drawer-{safe or 'position'}"


def _archive_id(symbol: str) -> str:
    safe = "".join(ch for ch in str(symbol).upper() if ch.isalnum() or ch in {"-", "_"})
    return f"portfolio-archive-{safe or 'position'}"


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
        .portfolio-col-actions { width: 130px; }
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
            height: 42px;
            padding: 0.34rem 0.52rem;
            vertical-align: middle;
        }
        .portfolio-table.terminal th:last-child,
        .portfolio-table.terminal td:last-child {
            width: 130px;
            padding-left: 0.4rem;
            padding-right: 0.4rem;
            text-align: center;
        }
        .portfolio-table.terminal tr:hover td {
            background: #FBFCFE;
        }
        .portfolio-symbol-cell .portfolio-cell b {
            font-size: 0.83rem;
            font-weight: 860;
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
        .portfolio-archive-link {
            color: #6b7280;
            background: transparent;
            border-color: transparent;
            padding-left: 0.2rem;
            padding-right: 0.2rem;
            font-weight: 650;
        }
        .portfolio-archive-link:hover {
            color: #334155;
            background: #FFFFFF;
            border-color: rgba(15, 23, 42, 0.10);
        }
        .portfolio-archive-modal {
            pointer-events: none;
            position: fixed;
            inset: 0;
            z-index: 10000;
            opacity: 0;
            transition: opacity 0.14s ease;
        }
        .portfolio-archive-modal:target {
            pointer-events: auto;
            opacity: 1;
        }
        .portfolio-archive-backdrop {
            position: absolute;
            inset: 0;
            background: rgba(15, 23, 42, 0.18);
        }
        .portfolio-archive-card {
            position: absolute;
            top: 50%;
            left: 50%;
            width: min(360px, calc(100vw - 2rem));
            transform: translate(-50%, -50%);
            border: 1px solid rgba(15, 23, 42, 0.10);
            border-radius: 10px;
            background: #FFFFFF;
            box-shadow: 0 22px 52px rgba(15, 23, 42, 0.16);
            padding: 1rem;
        }
        .portfolio-archive-card span {
            display: block;
            color: #64748b;
            font-size: 0.72rem;
            font-weight: 760;
        }
        .portfolio-archive-card strong {
            display: block;
            margin-top: 0.2rem;
            color: #0f172a;
            font-size: 1.05rem;
            font-weight: 860;
        }
        .portfolio-archive-card p {
            margin: 0.55rem 0 0;
            color: #64748b;
            font-size: 0.78rem;
            line-height: 1.55;
        }
        .portfolio-archive-actions {
            display: flex;
            justify-content: flex-end;
            gap: 0.5rem;
            margin-top: 0.85rem;
        }
        .portfolio-archive-actions form {
            margin: 0;
        }
        .portfolio-archive-actions a,
        .portfolio-archive-actions button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            height: 30px;
            padding: 0 0.8rem;
            border-radius: 7px;
            font-size: 0.72rem;
            font-weight: 760;
            text-decoration: none;
            cursor: pointer;
        }
        .portfolio-archive-actions a {
            border: 1px solid rgba(15, 23, 42, 0.10);
            color: #334155;
            background: #FFFFFF;
        }
        .portfolio-archive-actions button {
            border: 1px solid #B42318;
            color: #FFFFFF;
            background: #B42318;
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
        .portfolio-archive-link {
            color: #6b7280;
            background: transparent;
            border-color: transparent;
            padding-left: 0.2rem;
            padding-right: 0.2rem;
            font-weight: 650;
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
