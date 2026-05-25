from __future__ import annotations

from html import escape

import streamlit as st

from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from data.portfolio_view_model import build_portfolio_view_model
from data.stock_plan import StockPlanStore
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
        f"<b>{escape(str(primary or '-'))}</b>"
        f"<small>{escape(str(secondary or '-'))}</small>"
        "</div>"
    )


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
    deviation = _deviation_text(row)
    return deviation if deviation != "暂无偏离提示" else _price_status_text(row.get("priceStatus"))


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


def render() -> None:
    _render_styles()
    _render_final_portfolio_styles()
    render_page_header("组合持仓", "真实持仓、仓位偏离和下一步动作。")

    position_store = PortfolioPositionStore()
    settings_store = PortfolioSettingsStore()
    plan_store = StockPlanStore()
    view = build_portfolio_view_model()
    settings = view["settings"]
    rows = view["rows"]

    _render_deactivate_dialog_if_needed(position_store)
    _render_overview_strip(view["summary"], settings)
    _render_action_panel(view["actionGroups"])
    _render_positions_table(rows, position_store, plan_store)
    _render_editor(position_store, settings_store, rows, settings)


def _render_overview_strip(summary: dict, settings: dict) -> None:
    items = [
        ("持仓数", str(summary.get("positionCount", 0)), "active"),
        ("总市值", _money_or_dash(summary.get("marketValue"), zero_dash=True), "market value"),
        ("总成本", _money_or_dash(summary.get("costBasis"), zero_dash=True), "cost basis"),
        ("浮动盈亏", _money_or_dash(summary.get("unrealizedPnl")), _percent_or_dash(summary.get("unrealizedPnlPct"))),
        ("组合基准", _money_or_dash(settings.get("total_portfolio_value"), zero_dash=True), "manual total"),
        ("现金", _money_or_dash(settings.get("cash_balance"), zero_dash=True), "cash"),
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
    st.markdown(f'<div class="portfolio-lanes">{"".join(lanes)}</div>', unsafe_allow_html=True)


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

    headers = ["股票", "持仓 / 成本", "现价 / 盈亏", "仓位 / 上限", "系统参考", "我的计划", "查看"]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row) for row in rows)
    drawer_html = "".join(_drawer_html(row, plan_store) for row in rows)
    st.markdown(
        '<div id="portfolio-table"></div>'
        '<div class="portfolio-table-wrap terminal">'
        '<table class="portfolio-table terminal">'
        f"<thead><tr>{header_html}</tr></thead>"
        f"<tbody>{body_html}</tbody>"
        "</table>"
        "</div>"
        f"{drawer_html}",
        unsafe_allow_html=True,
    )


def _position_row_html(row: dict) -> str:
    symbol = str(row.get("symbol") or "")
    drawer_id = _drawer_id(symbol)
    return (
        "<tr>"
        f"<td>{_cell_html(symbol, _row_status_text(row))}</td>"
        f"<td>{_cell_html(_quantity_text(row.get('quantity')), '成本 ' + _money_text(row.get('costBasis')) + ' / 均价 ' + _money_text(row.get('averageCost')))}</td>"
        f"<td>{_cell_html(_money_text(row.get('currentPrice')), _money_text(row.get('unrealizedPnl')) + ' / ' + _percent_text(row.get('unrealizedPnlPct')))}</td>"
        f"<td>{_cell_html(_percent_text(row.get('positionPct')), '系统 ' + _percent_text(row.get('systemMaxPosition')) + ' / 个人 ' + _percent_text(row.get('maxAcceptablePositionPct')))}</td>"
        f"<td>{_cell_html(_system_action_text(row), _system_reason_text(row))}</td>"
        f"<td>{_cell_html(_plan_status_text(row), _plan_sub_text(row))}</td>"
        f'<td><a class="portfolio-view-link" href="#{escape(drawer_id)}">查看</a></td>'
        "</tr>"
    )


def _drawer_html(row: dict, plan_store: StockPlanStore) -> str:
    symbol = str(row.get("symbol") or "")
    drawer_id = _drawer_id(symbol)
    research_notes = _research_notes(symbol, plan_store)
    sections = [
        ("持仓摘要", [
            ("持股数量", _quantity_text(row.get("quantity"))),
            ("平均成本", _money_text(row.get("averageCost"))),
            ("现价", _money_text(row.get("currentPrice"))),
            ("价格状态", _price_status_text(row.get("priceStatus"))),
            ("市值", _money_text(row.get("marketValue"))),
            ("浮动盈亏", _money_text(row.get("unrealizedPnl")) + " / " + _percent_text(row.get("unrealizedPnlPct"))),
            ("当前仓位", _percent_text(row.get("positionPct"))),
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
    disable_href = f"?page=portfolio&portfolio_disable={escape(symbol)}"
    return (
        f'<aside id="{escape(drawer_id)}" class="portfolio-drawer">'
        '<a class="portfolio-drawer-backdrop" href="#portfolio-table"></a>'
        '<div class="portfolio-drawer-panel">'
        '<div class="portfolio-drawer-head">'
        f"<div><strong>{escape(symbol)}</strong><span>{escape(_system_action_text(row))} · {escape(_plan_status_text(row))}</span></div>"
        '<a href="#portfolio-table">关闭</a>'
        "</div>"
        f"{body}"
        '<div class="portfolio-drawer-danger">'
        f'<a href="{disable_href}">归档持仓</a>'
        "</div>"
        "</div>"
        "</aside>"
    )


def _drawer_section_html(title: str, items: list[tuple[str, object]]) -> str:
    rows = "".join(
        f"<span>{escape(label)}</span><b>{escape(str(value))}</b>"
        for label, value in items
    )
    return f'<section class="portfolio-drawer-section"><h4>{escape(title)}</h4><div>{rows}</div></section>'


def _research_notes(symbol: str, plan_store: StockPlanStore) -> str:
    try:
        notes = str(plan_store.get_plan(symbol).get("notes") or "").strip()
    except Exception:
        notes = ""
    return notes or "暂无研究备忘录"


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
    symbol = str(st.query_params.get("portfolio_disable", "")).strip().upper()
    if symbol:
        _confirm_deactivate_dialog(symbol, position_store)


@st.dialog("归档持仓")
def _confirm_deactivate_dialog(symbol: str, position_store: PortfolioPositionStore) -> None:
    st.write(f"确认归档 {symbol}？归档后不会出现在 active 持仓清单。")
    cols = st.columns(2)
    if cols[0].button("确认归档", type="primary", width="stretch"):
        position_store.deactivate_position(symbol)
        _clear_portfolio_disable_query()
        st.rerun()
    if cols[1].button("取消", width="stretch"):
        _clear_portfolio_disable_query()
        st.rerun()


def _clear_portfolio_disable_query() -> None:
    if "portfolio_disable" in st.query_params:
        st.query_params.pop("portfolio_disable")


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
            gap: 0.5rem;
            margin: 0.45rem 0 0.75rem;
        }
        .portfolio-stat.compact {
            min-height: 68px;
            padding: 0.55rem 0.68rem;
            box-shadow: none;
        }
        .portfolio-stat.compact strong {
            font-size: 1.15rem;
            letter-spacing: 0;
        }
        .portfolio-stat.compact em {
            opacity: 0.58;
        }
        .portfolio-lanes {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            gap: 0.45rem;
            margin: 0.35rem 0 0.9rem;
        }
        .portfolio-lane {
            display: grid;
            grid-template-columns: 1fr auto;
            gap: 0.1rem 0.5rem;
            align-items: baseline;
            min-height: 48px;
            padding: 0.46rem 0.58rem;
            border: 1px solid var(--zhx-line);
            border-left: 3px solid var(--zhx-line-strong);
            border-radius: 7px;
            background: #FFFFFF;
        }
        .portfolio-lane span {
            color: var(--zhx-muted);
            font-size: 0.68rem;
            font-weight: 820;
        }
        .portfolio-lane b {
            color: var(--zhx-text);
            font-size: 1rem;
        }
        .portfolio-lane small {
            grid-column: 1 / -1;
            color: var(--zhx-faint);
            font-size: 0.68rem;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .portfolio-lane.tone-green { border-left-color: var(--zhx-green); }
        .portfolio-lane.tone-yellow { border-left-color: var(--zhx-yellow); }
        .portfolio-lane.tone-red { border-left-color: #B45309; }
        .portfolio-lane.tone-neutral { border-left-color: var(--zhx-blue); }
        .portfolio-table-wrap.terminal {
            margin-top: 0.35rem;
            border-radius: 6px;
            box-shadow: none;
        }
        .portfolio-table.terminal {
            min-width: 920px;
            font-size: 0.75rem;
        }
        .portfolio-table.terminal th {
            height: 30px;
            padding: 0.36rem 0.55rem;
            background: #F8FAFC;
            color: var(--zhx-muted);
            font-size: 0.65rem;
            letter-spacing: 0;
            text-transform: none;
        }
        .portfolio-table.terminal td {
            height: 44px;
            padding: 0.38rem 0.55rem;
            vertical-align: middle;
        }
        .portfolio-table.terminal tr:hover td {
            background: #FAFBFC;
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
            text-decoration: none;
        }
        .portfolio-view-link:hover {
            color: var(--zhx-text);
            border-color: var(--zhx-line-strong);
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
