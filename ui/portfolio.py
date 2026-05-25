from __future__ import annotations

from html import escape

import streamlit as st

from data.portfolio import PortfolioPositionStore, PortfolioSettingsStore
from formatting import format_currency, format_percent
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
    settings = settings_store.get_settings()
    positions = position_store.list_active_positions()

    _render_overview_strip(positions, settings)
    _render_action_panel(positions, settings)
    _render_positions_table(positions)
    _render_editor(position_store, settings_store, positions, settings)


def _render_overview_strip(positions: list[dict], settings: dict) -> None:
    cost_basis = _portfolio_cost_basis(positions)
    cash = _number(settings.get("cash_balance"))
    configured_total = _number(settings.get("total_portfolio_value"))
    estimated_total = configured_total if configured_total is not None else cost_basis + (cash or 0)
    target_pct = sum(_number(row.get("target_position_pct")) or 0 for row in positions)
    max_pct = sum(_number(row.get("max_acceptable_position_pct")) or 0 for row in positions)

    items = [
        ("持仓数", str(len(positions)), "active positions"),
        ("持仓成本", format_currency(cost_basis), "cost basis"),
        ("组合基准", format_currency(estimated_total), "manual total" if configured_total else "cost + cash"),
        ("现金", format_currency(cash), "cash balance"),
        ("目标仓位", format_percent(target_pct), "sum target"),
        ("最大仓位", format_percent(max_pct), "sum max"),
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


def _render_action_panel(positions: list[dict], settings: dict) -> None:
    render_section_title("持仓行动面板", "只基于手动持仓计划，不读取行情。")
    denominator = _portfolio_denominator(positions, settings)
    rows = _action_rows(positions, denominator)
    if not rows:
        st.markdown(
            """
            <div class="portfolio-action-grid">
                <div class="portfolio-action-card muted">
                    <span>待处理</span>
                    <strong>暂无</strong>
                    <p>添加持仓后，这里会提示缺计划、超仓位和复核线问题。</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    html = "".join(
        f'<div class="portfolio-action-card tone-{escape(row["tone"])}">'
        f"<span>{escape(row['label'])}</span>"
        f"<strong>{escape(row['value'])}</strong>"
        f"<p>{escape(row['detail'])}</p>"
        "</div>"
        for row in rows
    )
    st.markdown(f'<div class="portfolio-action-grid">{html}</div>', unsafe_allow_html=True)


def _render_positions_table(positions: list[dict]) -> None:
    render_section_title("持仓清单", "当前只显示手动录入的 active 持仓。")
    if not positions:
        st.markdown(
            """
            <div class="portfolio-empty">
                <div>暂无持仓，先添加第一只股票</div>
                <span>填写代码、数量和平均成本后，组合总览会自动汇总成本与目标仓位。</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return

    headers = [
        "股票代码",
        "持股数量",
        "平均成本",
        "目标仓位",
        "最大可接受仓位",
        "计划卖出价",
        "第一减仓价",
        "第二减仓价",
        "复核线",
        "备注",
    ]
    header_html = "".join(f"<th>{escape(label)}</th>" for label in headers)
    body_html = "".join(_position_row_html(row) for row in positions)
    st.markdown(
        f"""
        <div class="portfolio-table-wrap">
            <table class="portfolio-table">
                <thead><tr>{header_html}</tr></thead>
                <tbody>{body_html}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_editor(
    position_store: PortfolioPositionStore,
    settings_store: PortfolioSettingsStore,
    positions: list[dict],
    settings: dict,
) -> None:
    render_section_title("添加/编辑持仓", "保存会写入本地 portfolio_positions / portfolio_settings。")
    settings_col, position_col = st.columns([0.85, 1.45])

    with settings_col:
        with st.form("portfolio-settings-form"):
            st.markdown("**组合设置**")
            total_value = st.text_input("组合总资产", value=_input_value(settings.get("total_portfolio_value")))
            cash_balance = st.text_input("现金余额", value=_input_value(settings.get("cash_balance")))
            base_currency = st.text_input("币种", value=str(settings.get("base_currency") or "USD"))
            if st.form_submit_button("保存组合设置", type="primary", width="stretch"):
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

    with position_col:
        symbols = [str(row.get("symbol") or "") for row in positions]
        selected = st.selectbox("编辑对象", ["新增持仓", *symbols], key="portfolio-edit-symbol")
        editing = selected != "新增持仓"
        current = position_store.get_position(selected) if editing else None
        current = current or EMPTY_POSITION

        with st.form("portfolio-position-form"):
            st.markdown("**持仓表单**")
            top_cols = st.columns(3)
            symbol = top_cols[0].text_input("股票代码", value=str(current.get("symbol") or ""), disabled=editing)
            quantity = top_cols[1].text_input("持股数量", value=_input_value(current.get("quantity")))
            average_cost = top_cols[2].text_input("平均成本", value=_input_value(current.get("average_cost")))

            plan_cols = st.columns(3)
            target_position_pct = plan_cols[0].text_input("目标仓位", value=_input_value(current.get("target_position_pct")))
            max_acceptable_position_pct = plan_cols[1].text_input(
                "最大可接受仓位",
                value=_input_value(current.get("max_acceptable_position_pct")),
            )
            planned_sell_price = plan_cols[2].text_input("计划卖出价", value=_input_value(current.get("planned_sell_price")))

            trim_cols = st.columns(3)
            first_trim_price = trim_cols[0].text_input("第一减仓价", value=_input_value(current.get("first_trim_price")))
            second_trim_price = trim_cols[1].text_input("第二减仓价", value=_input_value(current.get("second_trim_price")))
            review_price = trim_cols[2].text_input("复核线", value=_input_value(current.get("review_price")))
            notes = st.text_area("备注", value=str(current.get("notes") or ""), height=96)

            submitted = st.form_submit_button("保存持仓", type="primary", width="stretch")
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
            if st.button(f"停用 {selected}", width="stretch"):
                position_store.deactivate_position(selected)
                st.success(f"{selected} 已停用。")
                st.rerun()


def _action_rows(positions: list[dict], denominator: float | None) -> list[dict]:
    missing_plan = [
        row for row in positions
        if not any(_number(row.get(key)) for key in ("planned_sell_price", "first_trim_price", "second_trim_price"))
    ]
    missing_review = [row for row in positions if _number(row.get("review_price")) is None]
    over_max = [
        row for row in positions
        if _cost_position_pct(row, denominator) is not None
        and _number(row.get("max_acceptable_position_pct")) is not None
        and (_cost_position_pct(row, denominator) or 0) > (_number(row.get("max_acceptable_position_pct")) or 0)
    ]
    target_gap = [
        row for row in positions
        if _number(row.get("target_position_pct")) is not None
        and _number(row.get("max_acceptable_position_pct")) is not None
        and (_number(row.get("target_position_pct")) or 0) > (_number(row.get("max_acceptable_position_pct")) or 0)
    ]
    return [
        {
            "label": "缺少减仓计划",
            "value": str(len(missing_plan)),
            "detail": _symbols_detail(missing_plan, "全部持仓已有卖出/减仓价。"),
            "tone": "orange" if missing_plan else "green",
        },
        {
            "label": "缺少复核线",
            "value": str(len(missing_review)),
            "detail": _symbols_detail(missing_review, "全部持仓已设置复核线。"),
            "tone": "yellow" if missing_review else "green",
        },
        {
            "label": "成本仓位超上限",
            "value": str(len(over_max)),
            "detail": _symbols_detail(over_max, "未发现成本仓位超过个人上限。"),
            "tone": "red" if over_max else "green",
        },
        {
            "label": "目标高于上限",
            "value": str(len(target_gap)),
            "detail": _symbols_detail(target_gap, "目标仓位均未超过最大可接受仓位。"),
            "tone": "red" if target_gap else "green",
        },
    ]


def _position_row_html(row: dict) -> str:
    return (
        "<tr>"
        f"<td><strong>{escape(str(row.get('symbol') or ''))}</strong></td>"
        f"<td>{escape(_quantity_text(row.get('quantity')))}</td>"
        f"<td>{escape(format_currency(_number(row.get('average_cost'))))}</td>"
        f"<td>{escape(_percent_text(row.get('target_position_pct')))}</td>"
        f"<td>{escape(_percent_text(row.get('max_acceptable_position_pct')))}</td>"
        f"<td>{escape(format_currency(_number(row.get('planned_sell_price'))))}</td>"
        f"<td>{escape(format_currency(_number(row.get('first_trim_price'))))}</td>"
        f"<td>{escape(format_currency(_number(row.get('second_trim_price'))))}</td>"
        f"<td>{escape(format_currency(_number(row.get('review_price'))))}</td>"
        f"<td class=\"notes\">{escape(str(row.get('notes') or ''))}</td>"
        "</tr>"
    )


def _portfolio_cost_basis(positions: list[dict]) -> float:
    return sum((_number(row.get("quantity")) or 0) * (_number(row.get("average_cost")) or 0) for row in positions)


def _portfolio_denominator(positions: list[dict], settings: dict) -> float | None:
    total_value = _number(settings.get("total_portfolio_value"))
    if total_value and total_value > 0:
        return total_value
    cost_basis = _portfolio_cost_basis(positions)
    cash = _number(settings.get("cash_balance")) or 0
    fallback = cost_basis + cash
    return fallback if fallback > 0 else None


def _cost_position_pct(row: dict, denominator: float | None) -> float | None:
    if not denominator:
        return None
    cost = (_number(row.get("quantity")) or 0) * (_number(row.get("average_cost")) or 0)
    return cost / denominator * 100


def _symbols_detail(rows: list[dict], fallback: str) -> str:
    if not rows:
        return fallback
    symbols = [str(row.get("symbol") or "") for row in rows[:5]]
    suffix = "..." if len(rows) > 5 else ""
    return ", ".join(symbols) + suffix


def _quantity_text(value: object) -> str:
    number = _number(value)
    if number is None:
        return "N/A"
    return f"{number:,.4g}"


def _percent_text(value: object) -> str:
    return format_percent(_number(value))


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
        .portfolio-table th {
            padding: 0.58rem 0.62rem;
            text-align: left;
            color: var(--zhx-muted);
            background: #F8FAFC;
            border-bottom: 1px solid var(--zhx-line);
            font-size: 0.68rem;
            font-weight: 820;
            text-transform: uppercase;
        }
        .portfolio-table td {
            padding: 0.58rem 0.62rem;
            border-bottom: 1px solid var(--zhx-line);
            color: var(--zhx-text);
            white-space: nowrap;
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
        .portfolio-empty {
            margin: 0.55rem 0 1.1rem;
            padding: 1.4rem;
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
