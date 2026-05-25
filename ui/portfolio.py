from __future__ import annotations

from html import escape

import streamlit as st

from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.portfolio_view_model import build_portfolio_view_model
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


def render() -> None:
    _render_styles()
    render_page_header("组合持仓", "记录真实持仓、目标仓位和减仓复核线。")

    position_store = PortfolioPositionStore()
    settings_store = PortfolioSettingsStore()
    view = build_portfolio_view_model()
    settings = view["settings"]
    rows = view["rows"]

    _render_overview_strip(view["summary"], settings)
    _render_action_panel(view["actionGroups"])
    _render_positions_table(rows, position_store)
    _render_editor(position_store, settings_store, rows, settings)


def _render_overview_strip(summary: dict, settings: dict) -> None:
    items = [
        ("持仓数", str(summary.get("positionCount", 0)), "active positions"),
        ("市值", _money_text(summary.get("marketValue")), "market value"),
        ("成本", _money_text(summary.get("costBasis")), "cost basis"),
        ("浮动盈亏", _money_text(summary.get("unrealizedPnl")), _percent_text(summary.get("unrealizedPnlPct"))),
        ("组合基准", _money_text(settings.get("total_portfolio_value")), "manual total"),
        ("现金", _money_text(settings.get("cash_balance")), "cash balance"),
    ]
    html = "".join(
        '<div class="portfolio-stat">'
        f"<span>{escape(label)}</span>"
        f"<strong>{escape(value)}</strong>"
        f"<em>{escape(detail)}</em>"
        "</div>"
        for label, value, detail in items
    )
    st.markdown(f'<div class="portfolio-overview">{html}</div>', unsafe_allow_html=True)


def _render_action_panel(action_groups: list[dict]) -> None:
    render_section_title("持仓行动面板", "来自组合 view model；当前不读取行情。")
    html = "".join(
        f'<div class="portfolio-action-card tone-{escape(_action_group_tone(group.get("key")))}">'
        f"<span>{escape(str(group.get('label') or ''))}</span>"
        f"<strong>{escape(_action_group_value(group))}</strong>"
        f"<p>{escape(_action_group_detail(group))}</p>"
        "</div>"
        for group in action_groups
    )
    st.markdown(f'<div class="portfolio-action-grid">{html}</div>', unsafe_allow_html=True)


def _render_positions_table(rows: list[dict], position_store: PortfolioPositionStore) -> None:
    title_cols = st.columns([5, 1])
    with title_cols[0]:
        render_section_title("持仓清单", "当前只显示手动录入的 active 持仓。")
    if not rows:
        st.markdown(
            '<div class="portfolio-empty">'
            "<div>暂无持仓，添加第一只股票。</div>"
            "</div>",
            unsafe_allow_html=True,
        )
        empty_cols = st.columns([1, 1, 1])
        with empty_cols[1]:
            if st.button("添加第一只股票", key="portfolio-empty-add", width="stretch"):
                st.session_state["portfolio_position_editor_open"] = True
                st.rerun()
        return

    with title_cols[1]:
        st.write("")
        if st.button("添加持仓", key="portfolio-list-add", width="stretch"):
            st.session_state["portfolio_position_editor_open"] = True
            st.rerun()

    headers = [
        "股票代码",
        "持股数量",
        "平均成本",
        "市值",
        "浮动盈亏",
        "当前仓位",
        "目标仓位",
        "最大可接受仓位",
        "计划卖出价",
        "第一减仓价",
        "第二减仓价",
        "复核线",
        "备注",
        "状态管理",
    ]
    headers = ["股票", "持仓 / 成本", "现价 / 盈亏", "仓位 / 上限", "系统参考", "我的计划", "查看"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row) for row in rows)
    st.markdown(
        '<div class="portfolio-table-wrap">'
        '<table class="portfolio-table">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>",
        unsafe_allow_html=True,
    )
    action_cols = st.columns([1, 1, 1, 3])
    action_symbol = action_cols[0].selectbox(
        "归档持仓",
        [str(row.get("symbol") or "") for row in rows],
        key="portfolio-deactivate-symbol",
        label_visibility="collapsed",
    )
    if action_cols[1].button("归档", type="primary", key="portfolio-deactivate-from-list", width="stretch"):
        position_store.deactivate_position(action_symbol)
        st.success(f"{action_symbol} 已归档。")
        st.rerun()


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
        selected = st.selectbox("编辑对象", ["新增持仓", *symbols], key="portfolio-edit-symbol")
        editing = selected != "新增持仓"
        current = position_store.get_position(selected) if editing else None
        current = current or EMPTY_POSITION
        watchlist_symbols = _available_watchlist_symbols(symbols)

        with st.form("portfolio-position-form"):
            st.markdown('<div class="portfolio-form-section">基础持仓</div>', unsafe_allow_html=True)
            basic_cols = st.columns(2)
            if editing:
                symbol = basic_cols[0].text_input("股票代码", value=str(current.get("symbol") or ""), disabled=True)
            else:
                symbol = _symbol_input_from_watchlist(basic_cols[0], watchlist_symbols)
            quantity = basic_cols[1].text_input("持股数量", value=_input_value(current.get("quantity")))
            cost_cols = st.columns(2)
            average_cost = cost_cols[0].text_input("平均成本", value=_input_value(current.get("average_cost")))
            cost_cols[1].write("")

            st.markdown('<div class="portfolio-form-section">计划参数</div>', unsafe_allow_html=True)
            plan_cols = st.columns(2)
            target_position_pct = plan_cols[0].text_input("目标仓位", value=_input_value(current.get("target_position_pct")))
            max_acceptable_position_pct = plan_cols[1].text_input(
                "最大可接受仓位",
                value=_input_value(current.get("max_acceptable_position_pct")),
            )
            sell_cols = st.columns(2)
            planned_sell_price = sell_cols[0].text_input("计划卖出价", value=_input_value(current.get("planned_sell_price")))
            first_trim_price = sell_cols[1].text_input("第一减仓价", value=_input_value(current.get("first_trim_price")))
            review_cols = st.columns(2)
            second_trim_price = review_cols[0].text_input("第二减仓价", value=_input_value(current.get("second_trim_price")))
            review_price = review_cols[1].text_input("复核线", value=_input_value(current.get("review_price")))
            notes = st.text_area("备注", value=str(current.get("notes") or ""), height=96)

            submitted = st.form_submit_button("保存持仓", width="stretch")
            if submitted:
                try:
                    position_store.save_position(
                        str(current.get("symbol") or symbol) if editing else symbol,
                        {
                            "quantity": quantity,
                            "average_cost": average_cost,
                            "target_position_pct": target_position_pct,
                            "max_acceptable_position_pct": max_acceptable_position_pct,
                            "planned_sell_price": planned_sell_price,
                            "first_trim_price": first_trim_price,
                            "second_trim_price": second_trim_price,
                            "review_price": review_price,
                            "notes": notes,
                            "is_active": True,
                        },
                    )
                    st.success("持仓已保存。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

        if editing:
            if st.button(f"归档 {selected}", type="primary", width="stretch"):
                position_store.deactivate_position(selected)
                st.success(f"{selected} 已归档。")
                st.rerun()

    with st.expander("组合设置", expanded=False):
        with st.form("portfolio-settings-form"):
            st.caption("仅用于组合基准和现金显示。")
            settings_cols = st.columns(2)
            total_value = settings_cols[0].text_input("组合总资产", value=_input_value(settings.get("total_portfolio_value")))
            cash_balance = settings_cols[1].text_input("现金余额", value=_input_value(settings.get("cash_balance")))
            base_currency = st.text_input("币种", value=str(settings.get("base_currency") or "USD"))
            if st.form_submit_button("保存组合设置", width="stretch"):
                try:
                    settings_store.save_settings(
                        {
                            "total_portfolio_value": total_value,
                            "cash_balance": cash_balance,
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


def _symbol_input_from_watchlist(column, watchlist_symbols: list[str]) -> str:
    if watchlist_symbols:
        choice = column.selectbox("股票代码", [*watchlist_symbols, "手动输入"], help="优先从观察池选择。")
        if choice != "手动输入":
            return str(choice)
        return column.text_input("手动股票代码")
    return column.text_input("股票代码", help="观察池股票都已有持仓，可手动输入其他代码。")


def _position_row_html(row: dict) -> str:
    return (
        "<tr>"
        f"<td><strong>{escape(str(row.get('symbol') or ''))}</strong></td>"
        f"<td>{escape(_quantity_text(row.get('quantity')))}</td>"
        f"<td>{escape(_money_text(row.get('averageCost')))}</td>"
        f"<td>{escape(_money_text(row.get('currentPrice')))}</td>"
        f"<td>{escape(_price_status_text(row.get('priceStatus')))}</td>"
        f"<td>{escape(_money_text(row.get('marketValue')))}</td>"
        f"<td>{escape(_money_text(row.get('unrealizedPnl')))} / {escape(_percent_text(row.get('unrealizedPnlPct')))}</td>"
        f"<td>{escape(_percent_text(row.get('positionPct')))}</td>"
        f"<td>{escape(_percent_text(row.get('targetPositionPct')))}</td>"
        f"<td>{escape(_percent_text(row.get('maxAcceptablePositionPct')))}</td>"
        f"<td>{escape(str(row.get('systemAction') or '-'))}</td>"
        f"<td>{escape(_percent_text(row.get('systemMaxPosition')))}</td>"
        f"<td>{escape(_percent_text(row.get('systemCurrentAdd')))}</td>"
        f"<td>{escape(str(row.get('decisionLane') or '-'))}</td>"
        f"<td>{escape(_warnings_text(row.get('deviationWarnings')))}</td>"
        f"<td>{escape(_money_text(row.get('plannedSellPrice')))}</td>"
        f"<td>{escape(_money_text(row.get('firstTrimPrice')))}</td>"
        f"<td>{escape(_money_text(row.get('secondTrimPrice')))}</td>"
        f"<td>{escape(_money_text(row.get('reviewPrice')))}</td>"
        f"<td class=\"notes\">{escape(str(row.get('notes') or ''))}</td>"
        "<td>下方选择归档</td>"
        "</tr>"
    )


def _symbols_detail(symbols: list[str], fallback: str) -> str:
    if not symbols:
        return fallback
    visible = [str(symbol) for symbol in symbols[:5]]
    suffix = "..." if len(symbols) > 5 else ""
    return ", ".join(visible) + suffix


def _action_group_tone(key: object) -> str:
    return {
        "addable": "green",
        "hold": "neutral",
        "nearTrim": "yellow",
        "overweight": "red",
        "review": "yellow",
    }.get(str(key), "neutral")


def _action_group_value(group: dict) -> str:
    count = int(group.get("count") or 0)
    if count:
        return str(count)
    return {
        "addable": "暂无可加仓",
        "hold": "暂无持有观察",
        "nearTrim": "暂无接近减仓价",
        "overweight": "暂无超仓",
        "review": "暂无需复核",
    }.get(str(group.get("key")), "暂无")


def _action_group_detail(group: dict) -> str:
    return _symbols_detail(list(group.get("symbols") or []), "无对应持仓。")


def _quantity_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return f"{number:,.4g}"


def _percent_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return format_percent(number)


def _money_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "-"
    return format_currency(number)


def _price_status_text(value: object) -> str:
    return {
        "quote_snapshot": "quote",
        "price_history": "history",
        "provided": "provided",
        "missing": "missing",
    }.get(str(value), "missing")


def _warnings_text(value: object) -> str:
    labels = {
        "overweight_system": "system overweight",
        "overweight_personal": "personal overweight",
        "system_not_addable": "system review/observe",
        "near_trim_price": "near trim",
    }
    items = value if isinstance(value, list) else []
    return ", ".join(labels.get(str(item), str(item)) for item in items) or "-"


def _position_row_html(row: dict) -> str:
    return (
        "<tr>"
        f"<td>{_cell_html(str(row.get('symbol') or ''), _row_status_text(row))}</td>"
        f"<td>{_cell_html(_quantity_text(row.get('quantity')), '成本 ' + _money_text(row.get('costBasis')) + ' / 均价 ' + _money_text(row.get('averageCost')))}</td>"
        f"<td>{_cell_html(_money_text(row.get('currentPrice')), _money_text(row.get('unrealizedPnl')) + ' / ' + _percent_text(row.get('unrealizedPnlPct')))}</td>"
        f"<td>{_cell_html(_percent_text(row.get('positionPct')), '系统 ' + _percent_text(row.get('systemMaxPosition')) + ' / 个人 ' + _percent_text(row.get('maxAcceptablePositionPct')))}</td>"
        f"<td>{_cell_html(_system_action_text(row), _system_reason_text(row))}</td>"
        f"<td>{_cell_html(_plan_main_text(row), _plan_sub_text(row))}</td>"
        f"<td>{_details_html(row)}</td>"
        "</tr>"
    )


def _cell_html(primary: object, secondary: object) -> str:
    return (
        '<div class="portfolio-cell">'
        f"<b>{escape(str(primary or '-'))}</b>"
        f"<small>{escape(str(secondary or '-'))}</small>"
        "</div>"
    )


def _row_status_text(row: dict) -> str:
    alerts = _warnings_text(row.get("deviationWarnings"))
    return alerts if alerts != "-" else _price_status_text(row.get("priceStatus"))


def _system_action_text(row: dict) -> str:
    action = str(row.get("systemAction") or "").strip()
    lane = str(row.get("decisionLane") or "").strip()
    if lane == "actionable":
        return "可加仓"
    if lane == "blocked":
        return "禁止追高"
    if lane == "review":
        return "待复核"
    if action:
        return action
    return "未生成"


def _system_reason_text(row: dict) -> str:
    warnings = _warnings_text(row.get("deviationWarnings"))
    if warnings != "-":
        return warnings
    reasons = [*list(row.get("blockReasons") or []), *list(row.get("reviewReasons") or [])]
    if reasons:
        return ", ".join(str(item) for item in reasons[:2])
    add = _percent_text(row.get("systemCurrentAdd"))
    return "当前可加 " + add if add != "-" else "无系统提示"


def _plan_main_text(row: dict) -> str:
    sell = _money_text(row.get("plannedSellPrice"))
    first = _money_text(row.get("firstTrimPrice"))
    second = _money_text(row.get("secondTrimPrice"))
    if sell != "-":
        return "卖出 " + sell
    if first != "-":
        return "减仓 " + first
    if second != "-":
        return "减仓 " + second
    return "未设置"


def _plan_sub_text(row: dict) -> str:
    parts = []
    first = _money_text(row.get("firstTrimPrice"))
    second = _money_text(row.get("secondTrimPrice"))
    review = _money_text(row.get("reviewPrice"))
    if first != "-":
        parts.append("一减 " + first)
    if second != "-":
        parts.append("二减 " + second)
    if review != "-":
        parts.append("复核 " + review)
    return " / ".join(parts) if parts else "计划未设置"


def _details_html(row: dict) -> str:
    details = [
        ("价格状态", _price_status_text(row.get("priceStatus"))),
        ("市值", _money_text(row.get("marketValue"))),
        ("系统可加", _percent_text(row.get("systemCurrentAdd"))),
        ("决策通道", row.get("decisionLane") or "-"),
        ("买区状态", row.get("buyZoneStatus") or "-"),
        ("阻断原因", ", ".join(str(item) for item in (row.get("blockReasons") or [])) or "-"),
        ("复核原因", ", ".join(str(item) for item in (row.get("reviewReasons") or [])) or "-"),
        ("计划卖出价", _money_text(row.get("plannedSellPrice"))),
        ("第一减仓价", _money_text(row.get("firstTrimPrice"))),
        ("第二减仓价", _money_text(row.get("secondTrimPrice"))),
        ("复核线", _money_text(row.get("reviewPrice"))),
        ("备注", row.get("notes") or "-"),
    ]
    items = "".join(
        f"<span>{escape(str(label))}</span><b>{escape(str(value))}</b>"
        for label, value in details
    )
    return (
        '<details class="portfolio-row-details">'
        "<summary>查看</summary>"
        f'<div class="portfolio-detail-grid">{items}</div>'
        "</details>"
    )


def _render_positions_table(rows: list[dict], position_store: PortfolioPositionStore) -> None:
    title_cols = st.columns([5, 1])
    with title_cols[0]:
        render_section_title("持仓清单", "仓位、盈亏、系统参考和计划状态。")
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

    headers = ["股票", "持仓 / 成本", "现价 / 盈亏", "仓位 / 上限", "系统参考", "我的计划", "查看"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row) for row in rows)
    st.markdown(
        '<div class="portfolio-table-wrap compact">'
        '<table class="portfolio-table compact">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>",
        unsafe_allow_html=True,
    )

    with st.expander("停用持仓", expanded=False):
        action_cols = st.columns([1.4, 1, 1, 3])
        action_symbol = action_cols[0].selectbox(
            "选择持仓",
            [str(row.get("symbol") or "") for row in rows],
            key="portfolio-deactivate-symbol",
            label_visibility="collapsed",
        )
        confirm = action_cols[1].checkbox("确认停用", key="portfolio-deactivate-confirm")
        if action_cols[2].button("停用", key="portfolio-deactivate-from-list", width="stretch", disabled=not confirm):
            position_store.deactivate_position(action_symbol)
            st.success(f"{action_symbol} 已停用。")
            st.rerun()


def _render_positions_table(rows: list[dict], position_store: PortfolioPositionStore) -> None:
    title_cols = st.columns([5, 1])
    with title_cols[0]:
        render_section_title("持仓清单", "仓位、盈亏、系统参考和计划状态。")
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

    selected_symbol = str(st.session_state.get("portfolio_detail_symbol") or rows[0].get("symbol") or "")
    headers = ["股票", "持仓 / 成本", "现价 / 盈亏", "仓位 / 上限", "系统参考", "我的计划", "查看"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row) for row in rows)
    st.markdown(
        '<div class="portfolio-table-wrap compact">'
        '<table class="portfolio-table compact">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>",
        unsafe_allow_html=True,
    )

    detail_options = [str(row.get("symbol") or "") for row in rows]
    detail_index = detail_options.index(selected_symbol) if selected_symbol in detail_options else 0
    detail_cols = st.columns([1.2, 4])
    selected_symbol = detail_cols[0].selectbox(
        "查看详情",
        detail_options,
        index=detail_index,
        key="portfolio-detail-symbol-select",
        label_visibility="collapsed",
    )
    st.session_state["portfolio_detail_symbol"] = selected_symbol
    selected_row = next((row for row in rows if str(row.get("symbol") or "") == selected_symbol), rows[0])
    _render_detail_panel(selected_row)

    with st.expander("停用持仓", expanded=False):
        action_cols = st.columns([1.4, 1, 1, 3])
        action_symbol = action_cols[0].selectbox(
            "选择持仓",
            detail_options,
            key="portfolio-deactivate-symbol",
            label_visibility="collapsed",
        )
        confirm = action_cols[1].checkbox("确认停用", key="portfolio-deactivate-confirm")
        if action_cols[2].button("停用", key="portfolio-deactivate-from-list", width="stretch", disabled=not confirm):
            position_store.deactivate_position(action_symbol)
            st.success(f"{action_symbol} 已停用。")
            st.rerun()


def _position_row_html(row: dict) -> str:
    return (
        "<tr>"
        f"<td>{_cell_html(str(row.get('symbol') or ''), _row_status_text(row))}</td>"
        f"<td>{_cell_html(_quantity_text(row.get('quantity')), '成本 ' + _money_text(row.get('costBasis')) + ' / 均价 ' + _money_text(row.get('averageCost')))}</td>"
        f"<td>{_cell_html(_money_text(row.get('currentPrice')), _money_text(row.get('unrealizedPnl')) + ' / ' + _percent_text(row.get('unrealizedPnlPct')))}</td>"
        f"<td>{_cell_html(_percent_text(row.get('positionPct')), '系统 ' + _percent_text(row.get('systemMaxPosition')) + ' / 个人 ' + _percent_text(row.get('maxAcceptablePositionPct')))}</td>"
        f"<td>{_cell_html(_system_action_text(row), _system_reason_text(row))}</td>"
        f"<td>{_cell_html(_plan_status_text(row), _plan_sub_text(row))}</td>"
        f'<td><span class="portfolio-view-link">下方详情</span></td>'
        "</tr>"
    )


def _render_detail_panel(row: dict) -> None:
    st.markdown(
        f"""
        <div class="portfolio-detail-panel">
            <div class="portfolio-detail-head">
                <strong>{escape(str(row.get("symbol") or ""))}</strong>
                <span>{escape(_system_action_text(row))} · {escape(_plan_status_text(row))}</span>
            </div>
            <div class="portfolio-detail-grid">
                {_detail_items_html(row)}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _detail_items_html(row: dict) -> str:
    details = [
        ("价格状态", _price_status_text(row.get("priceStatus"))),
        ("市值", _money_text(row.get("marketValue"))),
        ("系统可加", _percent_text(row.get("systemCurrentAdd"))),
        ("决策通道", _decision_lane_text(row.get("decisionLane"))),
        ("买区状态", _buy_zone_status_text(row.get("buyZoneStatus"))),
        ("阻断原因", _reason_text(row.get("blockReasons"))),
        ("计划卖出价", _money_text(row.get("plannedSellPrice"))),
        ("减仓价", _trim_prices_text(row)),
        ("复核线", _money_text(row.get("reviewPrice"))),
        ("备注", row.get("notes") or "未填写"),
    ]
    return "".join(
        f"<span>{escape(str(label))}</span><b>{escape(str(value))}</b>"
        for label, value in details
    )


def _price_status_text(value: object) -> str:
    return {
        "quote_snapshot": "实时报价",
        "price_history": "历史收盘价",
        "provided": "手动价格",
        "missing": "缺少价格",
    }.get(str(value), "缺少价格")


def _warnings_text(value: object) -> str:
    labels = {
        "overweight_system": "超系统上限",
        "overweight_personal": "超个人上限",
        "system_not_addable": "系统建议复核/观察",
        "near_trim_price": "接近减仓价",
    }
    items = value if isinstance(value, list) else []
    return "，".join(labels.get(str(item), str(item)) for item in items) or "-"


def _system_action_text(row: dict) -> str:
    warnings = set(row.get("deviationWarnings") or [])
    lane = str(row.get("decisionLane") or "").strip()
    action = str(row.get("systemAction") or "").strip()
    if "overweight_system" in warnings:
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
    return "未生成"


def _system_reason_text(row: dict) -> str:
    warnings = _warnings_text(row.get("deviationWarnings"))
    if warnings != "-":
        return warnings
    reasons = [*_translated_reasons(row.get("blockReasons")), *_translated_reasons(row.get("reviewReasons"))]
    if reasons:
        return "，".join(reasons[:2])
    add = _percent_text(row.get("systemCurrentAdd"))
    return "当前可加 " + add if add != "-" else "无系统提示"


def _plan_status_text(row: dict) -> str:
    if row.get("nearTrimPrice"):
        return "接近减仓价"
    current = _number(row.get("currentPrice"))
    review = _number(row.get("reviewPrice"))
    if current is not None and review is not None and current <= review:
        return "触及复核线"
    if any(_money_text(row.get(key)) != "-" for key in ("plannedSellPrice", "firstTrimPrice", "secondTrimPrice", "reviewPrice")):
        return "已设置计划"
    return "未设置计划"


def _plan_main_text(row: dict) -> str:
    return _plan_status_text(row)


def _plan_sub_text(row: dict) -> str:
    sell = _money_text(row.get("plannedSellPrice"))
    first = _money_text(row.get("firstTrimPrice"))
    review = _money_text(row.get("reviewPrice"))
    if sell != "-":
        return "卖出 " + sell
    if first != "-":
        return "减仓 " + first
    if review != "-":
        return "复核 " + review
    return "计划未设置"


def _row_status_text(row: dict) -> str:
    alerts = _warnings_text(row.get("deviationWarnings"))
    return alerts if alerts != "-" else _price_status_text(row.get("priceStatus"))


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
    return "，".join(reasons) if reasons else "-"


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
    if first != "-":
        items.append("第一减仓 " + first)
    if second != "-":
        items.append("第二减仓 " + second)
    return " / ".join(items) if items else "未设置"


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
            padding: 0.42rem 0.58rem;
            border-bottom: 1px solid var(--zhx-line);
            color: var(--zhx-text);
            white-space: nowrap;
            vertical-align: middle;
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
        .portfolio-view-link {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            height: 24px;
            padding: 0 0.55rem;
            border: 1px solid var(--zhx-line);
            border-radius: 6px;
            color: var(--zhx-muted);
            background: #FFFFFF;
            font-size: 0.7rem;
            font-weight: 760;
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
